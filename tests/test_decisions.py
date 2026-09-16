import asyncio
import json
from pathlib import Path

import pytest

from agentdash.config import Settings
from agentdash.hooks import claude as hook
from agentdash.install.hooks import install, uninstall
from agentdash.models import HUB_DECISION_ANSWER, Frame
from agentdash.node.runner import Node


class FakeHub:
    def __init__(self):
        self.sent = []
        self.connected = asyncio.Event()

    async def send(self, type_, payload=None):
        self.sent.append((type_, payload or {}))


def make_node(tmp_path: Path) -> Node:
    s = Settings(claude_config_dirs=[], decision_timeout=0.5)
    s.model_config["env_file"] = None
    n = Node(s)
    n.hub = FakeHub()  # type: ignore[assignment]
    n.hub.connected.set()
    return n


PAYLOAD = {
    "hook_event_name": "PermissionRequest",
    "session_id": "abc",
    "cwd": "/tmp",
    "tool_name": "Bash",
    "tool_input": {"command": "rm -rf build"},
    "_env": {"CLAUDE_CODE_BRIDGE_SESSION_ID": "br1"},
}


async def test_unarmed_returns_immediately(tmp_path, monkeypatch):
    monkeypatch.setattr("agentdash.config.STATE_DIR", tmp_path)
    n = make_node(tmp_path)
    reply = await n.on_claude_permission(dict(PAYLOAD))
    assert reply == {}
    assert n.decisions.pending == {}
    assert n.bridge["abc"] == "https://claude.ai/code/br1"


async def test_armed_roundtrip_allow(tmp_path, monkeypatch):
    monkeypatch.setattr("agentdash.config.STATE_DIR", tmp_path)
    n = make_node(tmp_path)
    n.armed = True

    async def answer_soon():
        while not n.decisions.pending:
            await asyncio.sleep(0.01)
        did = next(iter(n.decisions.pending))
        await n.on_hub_frame(
            Frame(
                type=HUB_DECISION_ANSWER,
                payload={"decision_id": did, "behavior": "allow", "remember": True},
            )
        )

    task = asyncio.create_task(answer_soon())
    reply = await n.on_claude_permission(dict(PAYLOAD))
    await task
    assert reply["hookSpecificOutput"]["decision"]["behavior"] == "allow"
    kinds = [t for t, _ in n.hub.sent]
    assert "decision.created" in kinds and "decision.resolved" in kinds
    # remembered: second call answers without a round trip
    reply2 = await n.on_claude_permission(dict(PAYLOAD))
    assert reply2["hookSpecificOutput"]["decision"]["behavior"] == "allow"
    assert "remembered" in reply2["hookSpecificOutput"]["decisionReason"]


async def test_armed_timeout_falls_through(tmp_path, monkeypatch):
    monkeypatch.setattr("agentdash.config.STATE_DIR", tmp_path)
    n = make_node(tmp_path)
    n.armed = True
    reply = await n.on_claude_permission(dict(PAYLOAD))
    assert reply == {}
    resolved = [p for t, p in n.hub.sent if t == "decision.resolved"]
    assert resolved and resolved[0]["status"] == "expired"


async def test_question_is_surfaced_not_blocked(tmp_path, monkeypatch):
    monkeypatch.setattr("agentdash.config.STATE_DIR", tmp_path)
    n = make_node(tmp_path)
    n.armed = True
    p = dict(PAYLOAD, tool_name="AskUserQuestion", ask_user_question="Which branch?")
    reply = await n.on_claude_permission(p)
    assert reply == {}
    created = [p for t, p in n.hub.sent if t == "decision.created"]
    assert created[0]["kind"] == "question" and created[0]["question"] == "Which branch?"


def test_install_and_uninstall_hooks(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "model": "x",
                "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo hi"}]}]},
            }
        )
    )
    settings = install(path, 1800)
    assert settings["model"] == "x"
    assert len(settings["hooks"]["Stop"]) == 2  # existing kept + ours
    assert settings["hooks"]["PermissionRequest"][0]["hooks"][0]["timeout"] == 1800
    assert path.with_suffix(".json.bak-agentdash").exists()
    # idempotent
    install(path, 1800)
    assert len(json.loads(path.read_text())["hooks"]["Stop"]) == 2
    after = uninstall(path)
    assert list(after["hooks"]) == ["Stop"] and len(after["hooks"]["Stop"]) == 1


def test_hook_client_silent_when_node_down(monkeypatch, capsys):
    monkeypatch.setattr(hook, "NODE_URL", "http://127.0.0.1:1")
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps(PAYLOAD)))
    assert hook.main(["x", "permission"]) == 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("mode", ["event", "permission"])
def test_hook_client_posts(monkeypatch, capsys, mode):
    calls = {}

    def fake_post(path, body, timeout):
        calls["path"] = path
        calls["body"] = body
        return (
            {"hookSpecificOutput": {"decision": {"behavior": "deny"}}}
            if mode == "permission"
            else {}
        )

    monkeypatch.setattr(hook, "_post", fake_post)
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(json.dumps(PAYLOAD)))
    hook.main(["x", mode])
    assert calls["path"].endswith(mode)
    assert "_env" in calls["body"]
    out = capsys.readouterr().out
    assert ("deny" in out) == (mode == "permission")
