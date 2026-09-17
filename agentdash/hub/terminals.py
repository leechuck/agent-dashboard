"""Pair a browser websocket with a node websocket for one terminal session."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .auth import COOKIE
from .clientguard import client_allowed
from .state import HubState

log = logging.getLogger(__name__)
router = APIRouter()


@dataclass
class Pair:
    term_id: str
    browser: WebSocket
    node: WebSocket | None = None
    ready: asyncio.Event = field(default_factory=asyncio.Event)


PAIRS: dict[str, Pair] = {}


def _web_ok(ws: WebSocket) -> bool:
    expected = ws.app.state.settings.web_token
    if not expected:
        return True
    auth = ws.headers.get("authorization", "")
    tok = (
        auth[7:]
        if auth.startswith("Bearer ")
        else ws.cookies.get(COOKIE, "") or ws.query_params.get("token", "")
    )
    return tok == expected


@router.websocket("/api/terminal/{session_key:path}")
async def browser_terminal(ws: WebSocket, session_key: str) -> None:
    state: HubState = ws.app.state.hub
    if not client_allowed(
        ws.client.host if ws.client else None, ws.app.state.settings.hub_allowed_cidrs
    ):
        await ws.close(code=4403)
        return
    if not _web_ok(ws):
        await ws.close(code=4401)
        return
    sess = await state.db.get_session(session_key)
    tmux = (sess.extra.get("tmux") if sess else None) or {}
    parts = session_key.split(":", 3)
    if not tmux and len(parts) == 4 and parts[1] == "tmux":
        # a pane the roster has not listed yet (a session started seconds ago):
        # "<machine>:tmux:<tmux server name>:<target>"
        tmux = {"socket": parts[2], "target": parts[3]}
    link = state.node_for(session_key)
    if not tmux or not link:
        await ws.close(code=4404)
        return
    await ws.accept()
    term_id = uuid.uuid4().hex[:12]
    pair = Pair(term_id=term_id, browser=ws)
    PAIRS[term_id] = pair
    cols = int(ws.query_params.get("cols", 100))
    rows = int(ws.query_params.get("rows", 30))
    try:
        await link.send(
            "terminal.open",
            {
                "term_id": term_id,
                "socket": tmux["socket"],
                "target": tmux["target"],
                "rows": rows,
                "cols": cols,
            },
        )
        try:
            await asyncio.wait_for(pair.ready.wait(), 15)
        except TimeoutError:
            await ws.send_text('{"type":"error","message":"node did not open the terminal"}')
            return
        assert pair.node
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                await pair.node.send_bytes(msg["bytes"])
            elif msg.get("text") is not None:
                await pair.node.send_text(msg["text"])
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.exception("browser terminal")
    finally:
        PAIRS.pop(term_id, None)
        if pair.node:
            try:
                await pair.node.close()
            except Exception:  # noqa: BLE001
                pass


@router.websocket("/nodes/terminal/{term_id}")
async def node_terminal(ws: WebSocket, term_id: str) -> None:
    token = ws.app.state.settings.node_token
    if token and ws.headers.get("authorization", "") != f"Bearer {token}":
        await ws.close(code=4401)
        return
    pair = PAIRS.get(term_id)
    if not pair:
        await ws.close(code=4404)
        return
    await ws.accept()
    pair.node = ws
    pair.ready.set()
    try:
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            if msg.get("bytes") is not None:
                await pair.browser.send_bytes(msg["bytes"])
            elif msg.get("text") is not None:
                await pair.browser.send_text(msg["text"])
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.exception("node terminal")
    finally:
        try:
            await pair.browser.close()
        except Exception:  # noqa: BLE001
            pass
