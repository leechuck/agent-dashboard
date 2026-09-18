import json
from pathlib import Path

from agentdash.node.collectors.usage import UsageCollector, _iso_ms


def test_iso_and_epoch_parsing():
    assert _iso_ms("2026-09-16T14:50:00.342386+00:00") == 1789570200342
    assert _iso_ms(1789805522) == 1789805522000
    assert _iso_ms(None) is None


def test_statusline_file_is_used_when_fresh(tmp_path: Path):
    cfg = tmp_path / ".claude"
    cfg.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    from agentdash.models import now_ms

    (state / "claude-rate-limits.json").write_text(
        json.dumps(
            {
                "rate_limits": {
                    "five_hour": {"used_percentage": 23.5, "resets_at": 1789570200},
                    "seven_day": {"used_percentage": 41.2, "resets_at": 1789740000},
                },
                "_written": now_ms(),
            }
        )
    )
    c = UsageCollector("m", [cfg], state)
    wins = c._claude_from_statusline(cfg, "max · personal")
    assert [(w.window, w.used_pct, w.resets_at) for w in wins] == [
        ("session", 23.5, 1789570200000),
        ("weekly", 41.2, 1789740000000),
    ]
    assert all(w.source == "statusline" for w in wins)


def test_statusline_file_ignored_when_stale(tmp_path: Path):
    cfg = tmp_path / ".claude"
    cfg.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    (state / "claude-rate-limits.json").write_text(
        json.dumps({"rate_limits": {"five_hour": {"used_percentage": 1}}, "_written": 0})
    )
    assert UsageCollector("m", [cfg], state)._claude_from_statusline(cfg, "max · personal") == []


def test_every_limit_category_is_reported_separately():
    from agentdash.node.collectors.usage import parse_claude_usage

    data = {
        "five_hour": {"utilization": 8.0, "resets_at": "2026-09-17T12:40:00+00:00"},
        "limits": [
            {
                "kind": "session",
                "group": "session",
                "percent": 8,
                "resets_at": "2026-09-17T12:40:00+00:00",
                "is_active": True,
            },
            {
                "kind": "weekly_all",
                "group": "weekly",
                "percent": 2,
                "resets_at": "2026-09-22T03:00:00+00:00",
            },
            {
                "kind": "weekly_scoped",
                "group": "weekly",
                "percent": 41,
                "scope": {"model": {"display_name": "Fable"}},
            },
            {
                "kind": "weekly_scoped",
                "group": "weekly",
                "percent": 5,
                "scope": {"model": {"display_name": "Opus"}, "surface": "cowork"},
            },
            {
                "kind": "weekly_scoped",
                "group": "weekly",
                "percent": 0,
                "scope": None,
                "is_active": False,
            },
            {"kind": "broken", "group": "weekly", "percent": None},
        ],
        "spend": {
            "enabled": True,
            "percent": 12,
            "used": {"amount_minor": 450, "exponent": 2},
            "balance": 20,
        },
    }
    got = {w.window: (w.label, w.used_pct) for w in parse_claude_usage(data, "team · x")}
    assert got["session"] == ("Session · 5 hours", 8.0)
    assert got["weekly"] == ("Week · everything", 2.0)
    assert got["weekly_fable"] == ("Week · Fable", 41.0)
    assert got["weekly_opus-cowork"] == ("Week · Opus · cowork", 5.0)
    assert len(got) == 5 and got["credits"][0] == "Usage credits"
    credits = next(w for w in parse_claude_usage(data, "a") if w.window == "credits")
    assert credits.detail["used_usd"] == 4.5 and credits.detail["remaining"] == 20


def test_an_account_without_the_limits_list_still_works():
    from agentdash.node.collectors.usage import parse_claude_usage

    old = {
        "five_hour": {"utilization": 30.0, "resets_at": "2026-09-17T12:40:00+00:00"},
        "seven_day": {"utilization": 50.0, "resets_at": None},
        "seven_day_opus": {"utilization": 70.0, "resets_at": None},
        "spend": {"enabled": False},
    }
    got = {w.window: w.label for w in parse_claude_usage(old, "max · personal")}
    assert got == {
        "session": "Session · 5 hours",
        "weekly": "Week · everything",
        "seven_day_opus": "Week · Opus",
    }
    assert parse_claude_usage({}, "a") == []


async def test_inventory_keeps_accounts_when_usage_fails_and_tries_duplicate_login(tmp_path, monkeypatch):
    from agentdash.models import UsageWindow
    from agentdash.node.collectors import usage

    dirs = [tmp_path / n for n in (".claude", ".claude-personal", ".claude-team")]
    for d in dirs:
        d.mkdir()
    monkeypatch.setattr(usage, "discover_claude_dirs", lambda: dirs)
    monkeypatch.setattr(usage, "account_of", lambda d: {
        "provider": "anthropic", "plan": "max" if d == dirs[1] else "team",
        "account": "personal" if d == dirs[1] else "team",
    })
    c = UsageCollector("m", dirs, tmp_path)
    called = []

    async def api(d, account):
        called.append(d)
        return [UsageWindow(provider="anthropic", account=account, window="session", used_pct=10)] if d == dirs[2] else []

    monkeypatch.setattr(c, "_claude_from_api", api)
    got = await c.claude()
    assert c.claude_accounts == ["personal", "team"]
    assert called == dirs  # a failed duplicate must not shadow the working login
    assert [(w.account, w.used_pct) for w in got] == [("team", 10)]


async def test_partial_usage_poll_does_not_delete_other_plan(tmp_path):
    from unittest.mock import Mock

    from agentdash.db import Database
    from agentdash.hub.state import HubState
    from agentdash.models import UsageWindow

    db = Database(tmp_path / "hub.db")
    await db.open()
    try:
        state = HubState(db, Mock())
        personal = UsageWindow(provider="anthropic", account="personal", machine="m", window="session", used_pct=10)
        team = personal.model_copy(update={"account": "team"})
        await db.add_usage(personal)
        await state.usage_snapshot([team])  # old nodes have no explicit inventory
        assert {w.account for w in await db.latest_usage()} == {"personal", "team"}
        await state.usage_snapshot([team], "m", ["personal", "team"])
        assert {w.account for w in await db.latest_usage()} == {"personal", "team"}
        await state.usage_snapshot([team], "m", ["team"])
        assert {w.account for w in await db.latest_usage()} == {"team"}
    finally:
        await db.close()
