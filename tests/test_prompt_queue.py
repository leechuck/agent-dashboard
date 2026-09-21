"""A prompt handed to Claude's inbox shows on the board at once, marked as not yet read,
and the transcript's own record replaces it."""

from collections import deque
from unittest.mock import AsyncMock

from agentdash.hub.state import HubState
from agentdash.models import NODE_EVENT, NODE_MESSAGES, Message, Session
from agentdash.node import runner


def _node(extra: dict) -> tuple[runner.Node, str]:
    n = runner.Node.__new__(runner.Node)
    n.machine = "ws"
    n.hub = AsyncMock()
    key = "ws:claude:abc"
    n.sessions = {
        key: Session(
            key=key, machine="ws", harness="claude", session_id="abc", status="busy",
            cwd="/w", extra=extra,
        )
    }
    return n, key


async def test_a_prompt_is_typed_into_the_terminal_when_the_session_has_one(monkeypatch):
    # The inbox socket delivers text as a message from another Claude session; the agent
    # then reports to a peer instead of the owner. Typed text is the owner's own turn.
    n, key = _node({"socket": "/run/user/1000/cc-socks/1.sock", "tmux": {"socket": "s", "target": "t"}})
    monkeypatch.setattr(runner, "type_prompt", AsyncMock())
    monkeypatch.setattr(runner, "send_user_message", AsyncMock())
    await n.send_prompt(key, "continue", "req-1")
    runner.type_prompt.assert_awaited_once_with("s", "t", "continue")
    runner.send_user_message.assert_not_awaited()
    frames = {c.args[0]: c.args[1] for c in n.hub.send.await_args_list}
    assert frames[NODE_EVENT]["ok"] and frames[NODE_EVENT]["via"] == "tmux"
    [queued] = frames[NODE_MESSAGES]["messages"]
    assert queued["pending"] and queued["sender"] == "dashboard" and queued["text"] == "continue"
    assert queued["role"] == "user" and queued["id"] == "pending:req-1"


async def test_without_a_terminal_the_inbox_carries_the_prompt_and_shows_it_pending(monkeypatch):
    n, key = _node({"socket": "/run/user/1000/cc-socks/1.sock"})
    monkeypatch.setattr(runner, "send_user_message", AsyncMock(return_value={"ok": True}))
    await n.send_prompt(key, "continue", "req-2")
    frames = {c.args[0]: c.args[1] for c in n.hub.send.await_args_list}
    assert frames[NODE_EVENT]["ok"] and frames[NODE_EVENT]["kind"] == "prompt.result"
    [queued] = frames[NODE_MESSAGES]["messages"]
    assert queued["pending"] and queued["id"] == "pending:req-2"


def test_the_delivered_record_retires_the_pending_copy():
    st = HubState.__new__(HubState)
    st.caches = {}
    key = "ws:claude:abc"
    pending = Message(id="pending:1", role="user", text="continue", sender="dashboard", pending=True)
    other = Message(id="pending:2", role="user", text="then push", sender="dashboard", pending=True)
    st.cache_messages(key, [pending, other], reset=False)
    delivered = Message(id="rec-9", role="user", text="continue", sender="dashboard")
    st.cache_messages(key, [delivered], reset=False)
    assert [m.id for m in st.caches[key]] == ["pending:2", "rec-9"]
    assert isinstance(st.caches[key], deque)
    # typed into the terminal, the record has no sender; it still retires the copy
    st.cache_messages(key, [Message(id="rec-10", role="user", text="then push")], reset=False)
    assert "pending:2" not in [m.id for m in st.caches[key]]
    # plumbing the harness writes as a user record does not
    st.cache_messages(key, [Message(id="p", role="user", text="then push", pending=True)], reset=False)
    st.cache_messages(key, [Message(id="rec-11", role="user", text="then push", is_meta=True)], reset=False)
    assert "p" in [m.id for m in st.caches[key]]
