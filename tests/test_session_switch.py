from unittest.mock import AsyncMock

import pytest

from agentdash.config import Settings
from agentdash.models import Session
from agentdash.node import runner
from agentdash.node.catalog import Catalog
from agentdash.node.launcher import LaunchError


@pytest.fixture
def node(tmp_path, monkeypatch):
    base, personal = tmp_path / ".claude", tmp_path / ".claude-personal"
    (base / "projects").mkdir(parents=True)
    personal.mkdir()
    (personal / "projects").symlink_to(base / "projects")
    n = runner.Node.__new__(runner.Node)
    n.s = Settings(claude_config_dirs=[base, personal])
    n.machine = "test"
    sess = Session(
        key="test:claude:old",
        machine="test",
        harness="claude",
        session_id="old",
        status="idle",
        pid=12345,
        cwd=str(tmp_path),
        transcript_path=str(base / "projects/old.jsonl"),
        last_line="You've hit your session limit",
        extra={"config_dir": str(base)},
    )
    n.locate = AsyncMock(return_value=sess)
    n._stop = AsyncMock()
    n._endpoints = lambda p: []
    monkeypatch.setattr(runner, "discover_claude_dirs", lambda: [base, personal])
    monkeypatch.setattr("shutil.which", lambda name: "/bin/" + name)
    monkeypatch.setattr(runner, "launch", AsyncMock(return_value={"ok": True}))
    return n


async def test_exhausted_claude_resumes_same_conversation_on_other_subscription(node):
    r = await node._switch(
        {
            "session_key": "test:claude:old",
            "harness": "claude",
            "backend": "login",
            "login": ".claude-personal",
        }
    )
    target = runner.launch.call_args.args[0]
    assert r["resumed"] and target.resume == "old"
    assert target.login == ".claude-personal"
    node._stop.assert_awaited_once()


async def test_exhausted_claude_can_hand_over_to_codex(node):
    r = await node._switch({"session_key": "test:claude:old", "harness": "codex"})
    target = runner.launch.call_args.args[0]
    assert not r["resumed"] and target.harness == "codex" and not target.resume
    assert "old.jsonl" in target.prompt and "Continue that work" in target.prompt
    node._stop.assert_not_awaited()


async def test_invalid_destination_never_stops_source(node, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None if name == "codex" else "/bin/" + name)
    with pytest.raises(LaunchError, match="not installed"):
        await node._switch({"session_key": "test:claude:old", "harness": "codex", "stop_old": True})
    node._stop.assert_not_awaited()
    runner.launch.assert_not_awaited()


async def test_quick_catalog_does_not_wait_for_any_model_probes(node, monkeypatch):
    async def forbidden(*args):
        pytest.fail("Switch options must not invoke model or endpoint probes")

    for name in ("pi_models", "opencode_models", "probe_endpoint"):
        monkeypatch.setattr("agentdash.node.catalog." + name, forbidden)
    r = await Catalog(node.s).get([], quick=True)
    assert r["harnesses"]["codex"] and r["harnesses"]["claude"]
    assert len(r["logins"]) == 2
