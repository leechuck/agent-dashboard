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
    fs = ck.analyse([], M, [], [win("anthropic", "five_hour", 64, 3)], {"anthropic::five_hour": pts}, now=NOW)
    assert fs and fs[0].kind == "limit" and "before the reset" in fs[0].title + fs[0].detail
    # a reset inside the lookback must not produce a negative or inflated rate
    assert ck.burn_rate([(NOW - 50 * MIN, 90.0), (NOW - 40 * MIN, 2.0), (NOW, 6.0)], NOW) == 6.0


def test_openrouter_is_money_not_a_window():
    usage = [win("openrouter", "credits", 99, remaining=4.2, total=500), win("openrouter", "key_limit", 10)]
    fs = ck.analyse([], M, [], usage, now=NOW)
    assert [f.title for f in fs] == ["OpenRouter credits low: $4.20 left"]
    assert ck.provider_headroom(usage) == []


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


def test_lineage_links_children_by_env_then_by_process_tree_and_remembers():
    from agentdash.node.lineage import link_parents

    main = sess("main", "busy")
    main.pid = 100
    env_child = sess("envchild", "busy")
    env_child.pid = 200
    tree_child = sess("treechild", "busy", harness=Harness.codex)
    tree_child.pid = 300
    loner = sess("loner")
    loner.pid = 400
    env = {100: "", 200: "main", 300: "", 400: "someone-else"}
    tree = {200: [1], 300: [250, 100, 1], 400: [1], 100: [50]}
    known: dict[str, str] = {}
    link_parents([main, env_child, tree_child, loner], known, lambda p, n: env[p], lambda p: tree[p])
    assert env_child.extra["parent"] == main.key and tree_child.extra["parent"] == main.key
    assert "parent" not in main.extra and "parent" not in loner.extra
    # the child process is gone but the parent link survives while the session is listed
    gone = sess("envchild", "done")
    link_parents([main, gone], known, lambda p, n: env[p], lambda p: tree[p])
    assert gone.extra["parent"] == main.key and tree_child.key not in known


def test_account_of_tells_subscriptions_from_gateways(tmp_path):
    import json

    from agentdash.config import discover_claude_dirs
    from agentdash.node.collectors.claude import account_of

    home = tmp_path
    for name, plan, org in ((".claude", "max", "rob@x's Organization"), (".claude-team", "team", "KAUST BORG")):
        d = home / name
        d.mkdir()
        (d / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": "t", "subscriptionType": plan}}))
        target = home / ".claude.json" if name == ".claude" else d / ".claude.json"
        target.write_text(json.dumps({"oauthAccount": {"organizationName": org}}))
    (home / ".claude-openrouter").mkdir()
    (home / ".claude.json.bak").write_text("{}")
    assert [d.name for d in discover_claude_dirs(home)] == [".claude", ".claude-openrouter", ".claude-team"]
    assert account_of(home / ".claude") == {"provider": "anthropic", "plan": "max", "account": "max · personal"}
    assert account_of(home / ".claude-team")["account"] == "team · KAUST BORG"
    assert account_of(home / ".claude-openrouter") == {"provider": "openrouter", "plan": "", "account": ""}


def test_two_claude_logins_pick_the_quota_that_expires_first():
    def w(account, window, pct, resets_h, d):
        x = win("anthropic", window, pct, resets_h, config_dir=d)
        x.account = account
        return x

    usage = [
        w("max · personal", "five_hour", 20, 3, ".claude"),
        w("max · personal", "seven_day", 60, 100, ".claude"),
        w("team · KAUST", "five_hour", 10, 3, ".claude-team"),
        w("team · KAUST", "seven_day", 30, 20, ".claude-team"),
    ]
    pick = ck.pick_account(usage, "anthropic", NOW)
    assert pick["best"]["account"] == "team · KAUST" and pick["best"]["command"] == "claude-team"
    # a full short window takes a login out of the running, whatever its weekly headroom
    usage[2].used_pct = 95
    assert ck.pick_account(usage, "anthropic", NOW)["best"]["account"] == "max · personal"
    usage[2].used_pct = 10
    s = sess("a", "busy", account="max · personal")
    fs = ck.analyse([s], M, [], usage, now=NOW)
    (f,) = [f for f in fs if f.kind == "account"]
    assert "team · KAUST" in f.title and "`claude-team`" in f.title and "a." in f.detail
    labels = {h["label"] for h in ck.provider_headroom(usage)}
    assert labels == {"Claude (max · personal)", "Claude (team · KAUST)"}
    assert ck.pick_account(usage[:2], "anthropic", NOW) is None


def test_subagents_are_not_offered_as_ready_or_conflicts():
    parent = sess("main", "idle", cwd="/w/r")
    kids = [sess(f"k{i}", "idle" if i else "busy", cwd="/w/r", parent=parent.key) for i in range(3)]
    fs = ck.analyse([parent, *kids], M, [], [], now=NOW)
    (ready,) = [f for f in fs if f.kind == "ready"]
    assert ready.title.startswith("1 session") and not [f for f in fs if f.kind == "conflict"]
    assert ck.stats([parent, *kids], M, NOW)["subagents"] == 3


def test_install_account_shares_transcripts_and_is_idempotent(tmp_path):
    from agentdash.install.account import install_account

    base = tmp_path / ".claude"
    (base / "projects").mkdir(parents=True)
    (base / "settings.json").write_text('{"hooks": {}}')
    first = install_account("team", tmp_path, tmp_path / "bin")
    team = tmp_path / ".claude-team"
    assert (team / "projects").resolve() == (base / "projects").resolve()
    assert (team / "settings.json").read_text() == '{"hooks": {}}'
    assert "CLAUDE_CONFIG_DIR" in (tmp_path / "bin" / "claude-team").read_text() and first
    assert install_account("team", tmp_path, tmp_path / "bin") == []
