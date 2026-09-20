"""A prompt handed to Claude's inbox shows on the board at once, marked as not yet read,
and the transcript's own record replaces it."""

from collections import deque
from unittest.mock import AsyncMock

from agentdash.hub.state import HubState
from agentdash.models import NODE_EVENT, NODE_MESSAGES, Message, Session
from agentdash.node import runner


async def test_an_inbox_prompt_is_shown_as_pending_until_the_agent_reads_it(monkeypatch):
    n = runner.Node.__new__(runner.Node)
    n.machine = "ws"
    n.hub = AsyncMock()
    key = "ws:claude:abc"
    n.sessions = {
        key: Session(
            key=key, machine="ws", harness="claude", session_id="abc", status="waiting",
            cwd="/w", extra={"socket": "/run/user/1000/cc-socks/1.sock", "tmux": {"target": "t"}},
        )
    }
    monkeypatch.setattr(runner, "send_user_message", AsyncMock(return_value={"ok": True}))
    await n.send_prompt(key, "continue", "req-1")
    frames = {c.args[0]: c.args[1] for c in n.hub.send.await_args_list}
    assert frames[NODE_EVENT]["ok"] and frames[NODE_EVENT]["kind"] == "prompt.result"
    [queued] = frames[NODE_MESSAGES]["messages"]
    assert queued["pending"] and queued["sender"] == "dashboard" and queued["text"] == "continue"
    assert queued["role"] == "user" and queued["id"] == "pending:req-1"


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
    # a typed message with the same words is not the inbox one: the copy stays
    st.cache_messages(key, [Message(id="rec-10", role="user", text="then push")], reset=False)
    assert "pending:2" in [m.id for m in st.caches[key]]
