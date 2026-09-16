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
    wins = c._claude_from_statusline(cfg)
    assert [(w.window, w.used_pct, w.resets_at) for w in wins] == [
        ("five_hour", 23.5, 1789570200000),
        ("seven_day", 41.2, 1789740000000),
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
    assert UsageCollector("m", [cfg], state)._claude_from_statusline(cfg) == []
