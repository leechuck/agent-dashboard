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
    a, b = (
        sess("a", "waiting", waiting_for="permission: Bash"),
        sess("b", "waiting", waiting_for="your answer"),
    )
    d = Decision(
        id="d1",
        machine="m1",
        session_key=a.key,
        harness=Harness.claude,
        tool_name="Bash",
        expires_at=NOW + HOUR,
    )
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
    assert [f for f in ck.analyse([], M, [], usage, now=NOW) if f.kind == "limit"][
        0
    ].severity == "act"


def test_burn_rate_predicts_running_out_before_reset():
    pts = [(NOW - 60 * MIN, 40.0), (NOW - 30 * MIN, 52.0), (NOW, 64.0)]
    assert round(ck.burn_rate(pts, NOW)) == 24
    fs = ck.analyse(
        [], M, [], [win("anthropic", "five_hour", 64, 3)], {"anthropic::five_hour": pts}, now=NOW
    )
    assert fs and fs[0].kind == "limit" and "before the reset" in fs[0].title + fs[0].detail
    # a reset inside the lookback must not produce a negative or inflated rate
    assert ck.burn_rate([(NOW - 50 * MIN, 90.0), (NOW - 40 * MIN, 2.0), (NOW, 6.0)], NOW) == 6.0


def test_openrouter_is_money_not_a_window():
    usage = [
        win("openrouter", "credits", 99, remaining=4.2, total=500),
        win("openrouter", "key_limit", 10),
    ]
    fs = ck.analyse([], M, [], usage, now=NOW)
    assert [f.title for f in fs] == ["OpenRouter credits low: $4.20 left"]
    assert ck.provider_headroom(usage) == []


def test_context_thresholds():
    fs = ck.analyse(
        [
            sess("hi", context_pct=88.0, context_window=200000),
            sess("mid", context_pct=72.0),
            sess("lo", context_pct=40.0),
        ],
        M,
        [],
        [],
        now=NOW,
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
    (s,) = b["suggestions"]
    assert s["id"].startswith("sug:") and s["id"] == ck.suggestion_id({"title": "do!", "kind": "x"})
    assert {k: v for k, v in s.items() if k != "id"} == {
        "title": "Do",
        "why": "",
        "kind": "other",
        "session_key": "",
        "prompt": "",
    }
    keyed = {"title": "Reworded", "kind": "compact", "session_key": "m:claude:1"}
    assert ck.suggestion_id(keyed) == ck.suggestion_id({**keyed, "title": "Other words"})


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
    link_parents(
        [main, env_child, tree_child, loner], known, lambda p, n: env[p], lambda p: tree[p]
    )
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
    for name, plan, org in (
        (".claude", "max", "rob@x's Organization"),
        (".claude-team", "team", "KAUST BORG"),
    ):
        d = home / name
        d.mkdir()
        (d / ".credentials.json").write_text(
            json.dumps({"claudeAiOauth": {"accessToken": "t", "subscriptionType": plan}})
        )
        target = home / ".claude.json" if name == ".claude" else d / ".claude.json"
        target.write_text(json.dumps({"oauthAccount": {"organizationName": org}}))
    (home / ".claude-openrouter").mkdir()
    (home / ".claude.json.bak").write_text("{}")
    assert [d.name for d in discover_claude_dirs(home)] == [
        ".claude",
        ".claude-openrouter",
        ".claude-team",
    ]
    assert account_of(home / ".claude") == {
        "provider": "anthropic",
        "plan": "max",
        "account": "max · personal",
    }
    assert account_of(home / ".claude-team")["account"] == "team · KAUST BORG"
    assert account_of(home / ".claude-openrouter") == {
        "provider": "openrouter",
        "plan": "",
        "account": "",
    }


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


def test_codex_goal_mode_and_fresh_last_line():
    import json

    from agentdash.node.adapters.codex_rollout import scan_status

    def rec(t, payload):
        return json.dumps({"timestamp": "2026-09-17T06:00:00Z", "type": t, "payload": payload})

    goal = '<codex_internal_context source="goal">\nContinue.\n<objective>\nShip the\nbenchmark\n</objective>\nBudget:\n- Tokens used: 2667056\n'
    lines = [
        rec("event_msg", {"type": "task_complete", "last_agent_message": "old summary"}),
        rec("event_msg", {"type": "task_started"}),
        rec(
            "response_item",
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": goal}]},
        ),
        rec(
            "response_item",
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Checking the queue again."}],
            },
        ),
    ]
    info = scan_status(iter(lines))
    assert info["busy"] and info["last_agent_message"] == "Checking the queue again."
    assert (
        info["goal"] == "Ship the benchmark"
        and info["goal_tokens"] == 2667056
        and info["last_user"] == ""
    )
    done = rec(
        "event_msg",
        {"type": "thread_goal_updated", "goal": {"status": "complete", "objective": "x"}},
    )
    assert scan_status(iter([*lines, done]))["goal"] == ""


def test_agent_settings_merge_and_title_parsing():
    a = ck.agent_settings(
        {"advice": {"harness": "codex", "model": ""}, "titles": {"enabled": False}}, "lap", ""
    )
    assert a["advice"]["harness"] == "codex" and a["advice"]["machine"] == "lap"
    assert a["personal"]["machine"] == "lap" and a["titles"] == {"enabled": False, "model": "haiku"}
    got = ck.parse_titles(
        '```json\n{"titles": {"k1": "\\"Revise ecology paper section 3.\\"", "zz": "x", "k2": ""}}\n```',
        {"k1", "k2"},
    )
    assert got == {"k1": "Revise ecology paper section 3"}


async def test_titler_renames_only_when_the_request_changes():
    class DB:
        def __init__(self, sessions):
            self.sessions, self.saved = sessions, {}

        async def titles(self):
            return {}

        async def list_sessions(self, active_only=False):
            return self.sessions

        async def set_title(self, key, title, basis):
            self.saved[key] = title

        async def delete_title(self, key):
            self.saved.pop(key, None)

        async def get_setting(self, key):
            return {}

    class State:
        def __init__(self, db):
            self.db, self.nodes, self.calls = db, {"m1": object()}, []

        async def request(self, link, type_, payload, timeout=0):
            self.calls.append(payload)
            keys = [s["key"] for s in payload["digest"]["sessions"]]
            return {
                "ok": True,
                "text": '{"titles": {'
                + ",".join(f'"{k}": "Title for {k[-1]}"' for k in keys)
                + "}}",
            }

    a = sess("a", "busy", first_user="fix the parser", last_user="status?")
    kid = sess("k", "busy", parent=a.key, first_user="sub work")
    blank = sess("b", "idle")
    state = State(DB([a, kid, blank]))
    titler, agents = ck.Titler(), ck.agent_settings({}, "m1")
    assert await titler.tick(state, agents) == 1
    assert (
        state.calls[0]["agent"]["model"] == "haiku"
        and len(state.calls[0]["digest"]["sessions"]) == 1
    )
    fresh = [sess("a", "busy", first_user="fix the parser", last_user="status?")]
    titler.apply(fresh)
    assert fresh[0].extra["title"] == "Title for a"
    assert await titler.tick(state, agents) == 0 and len(state.calls) == 1  # nothing changed
    a.extra["last_user"] = "now write the paper"
    assert await titler.tick(state, agents) == 0  # changed, but renaming waits out the gap
    titler.last_run = 0
    assert await titler.tick(state, agents) == 1 and len(state.calls) == 2
    assert await titler.tick(state, ck.agent_settings({"titles": {"enabled": False}})) == 0
    # a name the owner typed is never overwritten; clearing it hands naming back
    await titler.rename(state.db, a.key, "  My own name ")
    a.extra["last_user"] = "yet another topic"
    titler.last_run = 0
    assert await titler.tick(state, agents) == 0 and titler.titles[a.key] == "My own name"
    await titler.rename(state.db, a.key, "")
    assert (
        await titler.tick(state, agents) == 1
        and state.calls[-1]["endpoints"][0]["id"] == "openrouter"
    )


def test_slim_cuts_only_folded_tool_payloads():
    from agentdash.hub.state import SLIM_CHARS, slim

    big = "x" * 5000
    use = slim(
        {"kind": "tool_use", "text": "", "tool_input": {"command": big, "n": 3, "edits": [big]}}
    )
    assert (
        use["slim"]
        and len(use["tool_input"]["command"]) == SLIM_CHARS
        and use["tool_input"]["n"] == 3
    )
    assert isinstance(use["tool_input"]["edits"], str)
    assert slim({"kind": "tool_result", "text": big})["text"] == big[:SLIM_CHARS]
    said = {"kind": "text", "text": big}
    assert slim(said) is said and "slim" not in slim({"kind": "tool_result", "text": "short"})


def test_read_last_reads_the_tail_and_grows_until_it_has_enough(tmp_path):
    import json

    from agentdash.node.adapters.claude_transcript import read_last

    p = tmp_path / "t.jsonl"
    with p.open("w") as f:
        for i in range(400):
            f.write(
                json.dumps(
                    {
                        "type": "user",
                        "uuid": f"u{i}",
                        "message": {"role": "user", "content": f"msg {i} " + "y" * 200},
                    }
                )
                + "\n"
            )
    whole = read_last(p, 50, chunk=10_000_000)
    tail = read_last(p, 50, chunk=1_000)  # far too small at first: must double its way up
    assert [m.id for m in tail] == [m.id for m in whole] and len(tail) == 50
    assert tail[-1].text.startswith("msg 399")
    assert len(read_last(p, 1000, chunk=1_000)) == 400


async def test_retention_steps_run_on_a_real_database(tmp_path):
    from agentdash.db import Database

    db = Database(tmp_path / "hub.db")
    await db.open()
    await db.set_title("k", "t", "b")
    await db.prune_decisions(1)
    await db.prune_titles(10**12)
    await db.vacuum()  # used to raise: cannot VACUUM from within a transaction
    assert (await db.titles())["k"]["title"] == "t"
    await db.close()


async def test_a_terminal_prompt_on_an_unarmed_machine_pushes():
    """The hook tells the hub when a dialog is waiting in a terminal it cannot answer for."""
    from agentdash.hub.state import HubState

    class DB:
        async def get_session(self, key):
            return None

        async def list_push_subscriptions(self):
            return [{"endpoint": "e"}]

        async def remove_push_subscription(self, ep):
            pass

    class Pusher:
        enabled = True

        def __init__(self):
            self.sent = []

        async def send(self, subs, payload):
            self.sent.append(payload)
            return []

    st = HubState(DB(), type("B", (), {"publish": lambda *a: None})(), Pusher())
    for kind in (
        "claude.permission_prompt",
        "claude.agent_needs_input",
        "claude.elicitation_dialog",
    ):
        await st.on_node_event(
            "ws", {"kind": kind, "session_key": "ws:claude:abcdef1234", "armed": False}
        )
    assert len(st.pusher.sent) == 3 and "needs you at the terminal" in st.pusher.sent[0]["title"]
    await st.on_node_event(
        "ws", {"kind": "claude.permission_prompt", "session_key": "k", "armed": True}
    )
    await st.on_node_event(
        "ws", {"kind": "claude.notification", "session_key": "k", "armed": False}
    )
    assert (
        len(st.pusher.sent) == 3
    )  # armed goes to the phone as a decision; chatter is not a prompt


def test_a_session_the_plan_will_not_serve_is_flagged_with_the_way_out():
    blocked = sess("a", "idle")
    blocked.model = "claude-fable-5-1"
    blocked.last_line = "You're out of usage credits. Run /usage-credits to keep using Fable 5.1 or /model to switch"
    fs = ck.analyse([blocked, sess("b", "busy")], M, [], [], now=NOW)
    (f,) = [f for f in fs if f.kind == "blocked"]
    assert (
        f.severity == "act"
        and "claude-fable-5-1" in f.title
        and f.action.label == "Switch its model"
    )
    assert not [
        x for x in ck.analyse([sess("b", "busy")], M, [], [], now=NOW) if x.kind == "blocked"
    ]


async def test_usage_for_a_login_a_machine_no_longer_has_is_dropped(tmp_path):
    from agentdash.db import Database
    from agentdash.models import UsageWindow

    db = Database(tmp_path / "hub.db")
    await db.open()

    def w(account, window, machine="lc-dell", provider="anthropic"):
        return UsageWindow(
            provider=provider, account=account, window=window, used_pct=1, machine=machine
        )

    for x in (
        w("max · personal", "session"),
        w("max · personal", "weekly"),
        w("pro", "week", provider="openai"),
    ):
        await db.add_usage(x)
    fresh = [w("team · BORG", "session"), w("team · BORG", "weekly")]
    for x in fresh:
        await db.add_usage(x)
    await db.forget_gone_accounts("lc-dell", "anthropic", ["team · BORG"])
    left = {(x.provider, x.account) for x in await db.latest_usage()}
    assert left == {("anthropic", "team · BORG"), ("openai", "pro")}  # other providers untouched
    await db.forget_gone_accounts("lc-dell", "anthropic", [])  # a failed poll deletes nothing
    assert len(await db.latest_usage()) == 3
    await db.close()
