import json
from pathlib import Path

import pytest

from agentdash.node import past


def _claude_transcript(path: Path, sid: str, cwd: str, title: str = "") -> None:
    recs = [
        {
            "type": "user",
            "uuid": "u1",
            "cwd": cwd,
            "sessionId": sid,
            "timestamp": "2026-09-10T08:00:00Z",
            "message": {"role": "user", "content": "Fix the flaky test in the parser"},
        },
        {
            "type": "assistant",
            "uuid": "a1",
            "cwd": cwd,
            "timestamp": "2026-09-10T08:00:05Z",
            "message": {
                "role": "assistant",
                "model": "claude-opus-5",
                "content": [{"type": "text", "text": "Looking."}],
            },
        },
    ]
    if title:
        recs.append({"type": "ai-title", "aiTitle": title, "sessionId": sid})
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(json.dumps(r) for r in recs) + "\n"
    path.write_text(body + "x" * max(0, 600 - len(body)) * 0)  # small but not empty
    if path.stat().st_size < 500:
        with path.open("a") as f:
            f.write(json.dumps({"type": "system", "subtype": "pad", "content": "." * 600}) + "\n")


def test_slugs_match_the_harnesses():
    assert past.claude_slug("/home/r/Public/agent.dash_v2") == "-home-r-Public-agent-dash-v2"
    assert (
        past.pi_slug("/home/leechuck/Public/software/dell")
        == "--home-leechuck-Public-software-dell--"
    )


def test_past_claude_sessions_are_named_by_their_title_and_carry_the_folder(tmp_path):
    home = tmp_path / ".claude"
    cwd = str(tmp_path / "work" / "proj")
    _claude_transcript(
        home / "projects" / past.claude_slug(cwd) / "s1.jsonl", "s1", cwd, "Parser test flake"
    )
    _claude_transcript(home / "projects" / past.claude_slug(cwd) / "s2.jsonl", "s2", cwd)
    (home / "projects" / past.claude_slug(cwd) / "tiny.jsonl").write_text("{}\n")
    # a second login shares the same projects folder through a symlink: read once
    team = tmp_path / ".claude-team"
    team.mkdir()
    (team / "projects").symlink_to(home / "projects")

    found = past.list_claude("m", [home, team], limit=10)
    by = {s.session_id: s for s in found}
    assert set(by) == {"s1", "s2"}
    assert by["s1"].name == "Parser test flake" and by["s1"].extra["title"] == "Parser test flake"
    assert by["s2"].name == "Fix the flaky test in the parser"
    assert by["s1"].cwd == cwd and by["s1"].status == "done" and by["s1"].model == "claude-opus-5"
    assert by["s1"].key == "m:claude:s1" and by["s1"].extra["past"] is True
    assert past.matches(by["s1"], "flake parser") and not past.matches(by["s1"], "banana")
    assert past.find_claude("m", [home], "s2").transcript_path == by["s2"].transcript_path
    assert past.find_claude("m", [home], "nope") is None


def test_a_claude_session_moves_into_the_folder_for_its_new_cwd(tmp_path):
    src_home, dst_home = tmp_path / "a" / ".claude", tmp_path / "b" / ".claude"
    cwd = "/home/r/work/proj"
    tpath = src_home / "projects" / past.claude_slug(cwd) / "abc.jsonl"
    _claude_transcript(tpath, "abc", cwd, "Moving day")
    sub = tpath.with_suffix("") / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-1.jsonl").write_text('{"type":"user"}\n')

    sess = past.claude_session("a", src_home, tpath)
    bundle, meta = past.pack(sess, tmp_path / "out")
    assert meta["harness"] == "claude" and meta["session_id"] == "abc" and meta["cwd"] == cwd

    new_cwd = "/home/r/elsewhere/proj"
    target = past.unpack(bundle, "claude", "abc", new_cwd, dst_home)
    assert target == dst_home / "projects" / past.claude_slug(new_cwd) / "abc.jsonl"
    assert target.read_text() == tpath.read_text()
    assert (target.with_suffix("") / "subagents" / "agent-1.jsonl").exists()


def test_codex_and_pi_bundles_land_where_those_harnesses_look(tmp_path):
    codex_home = tmp_path / ".codex"
    rollout = (
        codex_home / "sessions" / "2026" / "09" / "17" / "rollout-2026-09-17T12-00-00-t1.jsonl"
    )
    rollout.parent.mkdir(parents=True)
    rollout.write_text('{"type":"session_meta","payload":{"id":"t1","cwd":"/w"}}\n')
    t = {
        "id": "t1",
        "rollout_path": str(rollout),
        "cwd": "/w",
        "title": "Ship it",
        "updated_at_ms": 5,
    }
    sess = past.codex_session("m", t, codex_home)
    assert sess.name == "Ship it" and sess.key == "m:codex:t1"
    bundle, _ = past.pack(sess, tmp_path / "out")
    other = tmp_path / "other" / ".codex"
    target = past.unpack(bundle, "codex", "t1", "/w", other)
    assert target == other / "sessions" / "2026" / "09" / "17" / rollout.name

    pi_dir = tmp_path / ".pi" / "agent" / "sessions"
    f = pi_dir / past.pi_slug("/w") / "2026-09-02T10-57-44_p1.jsonl"
    f.parent.mkdir(parents=True)
    f.write_text(
        json.dumps({"type": "session", "version": 3, "id": "p1", "cwd": "/w"})
        + "\n"
        + json.dumps(
            {"type": "message", "id": "m1", "message": {"role": "user", "content": "hello pi"}}
        )
        + "\n"
    )
    [ps] = past.list_pi("m", pi_dir, 5)
    assert ps.session_id == "p1" and ps.name == "hello pi" and ps.cwd == "/w"
    assert past.find_pi("m", pi_dir, "p1").transcript_path == str(f)
    bundle, _ = past.pack(ps, tmp_path / "out")
    target = past.unpack(bundle, "pi", "p1", "/new/w", tmp_path / "pi2")
    assert target == tmp_path / "pi2" / past.pi_slug("/new/w") / f.name


def test_bundles_that_do_not_belong_are_refused(tmp_path):
    import tarfile

    bad = tmp_path / "bad.tgz"
    with tarfile.open(bad, "w:gz") as tar:
        p = tmp_path / "evil.jsonl"
        p.write_text("{}")
        tar.add(p, arcname="../evil.jsonl")
        tar.add(p, arcname="other.jsonl")
    with pytest.raises(past.TransferError):
        past.unpack(bad, "claude", "abc", "/w", tmp_path / ".claude")
    with pytest.raises(past.TransferError):
        past.unpack(bad, "opencode", "abc", "/w", tmp_path)
