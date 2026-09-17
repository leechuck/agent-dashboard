from agentdash.hub import cockpit as ck
from agentdash.models import Decision, Harness, Machine, Session, UsageWindow

NOW = 1_800_000_000_000
MIN = 60_000
HOUR = 60 * MIN


def sess(sid, status="idle", age_min=5, harness=Harness.claude, machine="m1", **extra):
    return Session(
        key=f"{machine}:{harness}:{sid}",
        machine=machine,
        harness=harness,
        session_id=sid,
        name=sid,
        cwd=extra.pop("cwd", f"/w/{sid}"),
        status=status,
        waiting_for=extra.pop("waiting_for", ""),
        updated_at=NOW - age_min * MIN,
        extra=extra,
    )


def win(provider, window, pct, resets_h=None, **detail):
    return UsageWindow(
        provider=provider,
        window=window,
        label=window,
        used_pct=pct,
        resets_at=NOW + int(resets_h * HOUR) if resets_h else None,
        detail=detail,
    )


M = [Machine(id="m1", online=True, last_seen=NOW)]


def kinds(fs):
    return [(f.kind, f.severity) for f in fs]


def test_decisions_and_waiting_come_first_and_do_not_double_count():
    a, b = sess("a", "waiting", waiting_for="permission: Bash"), sess("b", "waiting", waiting_for="your answer")
    d = Decision(id="d1", machine="m1", session_key=a.key, harness=Harness.claude, tool_name="Bash", expires_at=NOW + HOUR)
    fs = ck.analyse([sess("c", "busy"), a, b], M, [d], [], now=NOW)
    assert kinds(fs)[:2] == [("decision", "act"), ("waiting", "act")]
    assert sum(f.session_key == a.key for f in fs) == 1
    assert "1 thing needs you" not in ck.headline(fs, ck.stats([a, b], M, NOW))
    assert ck.headline(fs, ck.stats([a, b], M, NOW)).startswith("2 things need you now")


def test_stale_waiting_session_is_not_urgent():
    fs = ck.analyse([sess("old", "waiting", age_min=72 * 60)], M, [], [], now=NOW)
    assert not [f for f in fs if f.severity == "act"]


def test_limit_warning_suggests_the_provider_with_room():
    usage = [win("anthropic", "five_hour", 91, 1.5), win("openai", "week", 30, 50)]
    fs = ck.analyse([sess("a", "busy")], M, [], usage, now=NOW)
    (f,) = [f for f in fs if f.kind == "limit"]
    assert f.severity == "warn" and "Codex (30% used)" in f.detail and "a." in f.detail
    usage[0].used_pct = 96
    assert [f for f in ck.analyse([], M, [], usage, now=NOW) if f.kind == "limit"][0].severity == "act"


def test_burn_rate_predicts_running_out_before_reset():
    pts = [(NOW - 60 * MIN, 40.0), (NOW - 30 * MIN, 52.0), (NOW, 64.0)]
    assert round(ck.burn_rate(pts, NOW)) == 24
    fs = ck.analyse([], M, [], [win("anthropic", "five_hour", 64, 3)], {"anthropic:five_hour": pts}, now=NOW)
    assert fs and fs[0].kind == "limit" and "before the reset" in fs[0].title + fs[0].detail
    # a reset inside the lookback must not produce a negative or inflated rate
    assert ck.burn_rate([(NOW - 50 * MIN, 90.0), (NOW - 40 * MIN, 2.0), (NOW, 6.0)], NOW) == 6.0


def test_openrouter_is_money_not_a_window():
    usage = [win("openrouter", "credits", 99, remaining=4.2, total=500), win("openrouter", "key_limit", 10)]
    fs = ck.analyse([], M, [], usage, now=NOW)
    assert [f.title for f in fs] == ["OpenRouter credits low: $4.20 left"]
    assert "openrouter" not in ck.provider_headroom(usage)


def test_context_thresholds():
    fs = ck.analyse(
        [sess("hi", context_pct=88.0, context_window=200000), sess("mid", context_pct=72.0), sess("lo", context_pct=40.0)],
        M, [], [], now=NOW,
    )
    ctx = {f.session_key.rsplit(":", 1)[-1]: f.severity for f in fs if f.kind == "context"}
    assert ctx == {"hi": "warn", "mid": "info"}


def test_shared_directory_and_silent_busy():
    a, b = sess("a", "busy", cwd="/w/repo"), sess("b", "busy", cwd="/w/repo", age_min=45)
    fs = ck.analyse([a, b], M, [], [], now=NOW)
    assert ("conflict", "info") in kinds(fs) and ("stuck", "warn") in kinds(fs)


def test_parse_briefing_tolerates_fences_and_bad_fields():
    text = 'Sure:\n```json\n{"summary": "ok", "sessions": [{"key": "k", "doing": "x"}, 3], "suggestions": [{"title": "Do", "kind": "weird"}, {"why": "no title"}]}\n```'
    b = ck.parse_briefing(text)
    assert b["summary"] == "ok" and b["sessions"] == {"k": "x"}
    assert b["suggestions"] == [{"title": "Do", "why": "", "kind": "other", "session_key": "", "prompt": ""}]


def test_fingerprint_ignores_ticks_but_sees_status_changes():
    s = sess("a", "busy", context_pct=41.0)
    d1 = ck.digest([s], M, [win("anthropic", "five_hour", 41, 2)], [], NOW)
    s2 = sess("a", "busy", age_min=1, context_pct=43.0)
    d2 = ck.digest([s2], M, [win("anthropic", "five_hour", 43, 2)], [], NOW + MIN)
    assert ck.fingerprint(d1) == ck.fingerprint(d2)
    d3 = ck.digest([sess("a", "waiting")], M, [], [], NOW)
    assert ck.fingerprint(d1) != ck.fingerprint(d3)
