"""Websocket endpoint that nodes connect to."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..models import (
    NODE_DECISION_CREATED,
    NODE_DECISION_RESOLVED,
    NODE_EVENT,
    NODE_HELLO,
    NODE_MESSAGES,
    NODE_SESSIONS,
    NODE_USAGE,
    Decision,
    Frame,
    Machine,
    Message,
    Session,
    UsageWindow,
    now_ms,
)
from .state import HubState, NodeLink, slim

log = logging.getLogger(__name__)
router = APIRouter()


def _authorized(ws: WebSocket, token: str) -> bool:
    if not token:
        return True
    auth = ws.headers.get("authorization", "")
    return auth == f"Bearer {token}" or ws.query_params.get("token") == token


@router.websocket("/nodes")
async def nodes_ws(ws: WebSocket) -> None:
    state: HubState = ws.app.state.hub
    token: str = ws.app.state.settings.node_token
    from .clientguard import client_allowed

    if not client_allowed(
        ws.client.host if ws.client else None, ws.app.state.settings.hub_allowed_cidrs
    ):
        await ws.close(code=4403)
        return
    if not _authorized(ws, token):
        await ws.close(code=4401)
        return
    await ws.accept()
    link: NodeLink | None = None
    try:
        while True:
            raw = await ws.receive_text()
            try:
                frame = Frame.model_validate(json.loads(raw))
            except (json.JSONDecodeError, ValueError) as e:
                log.warning("bad node frame: %s", e)
                continue
            # one bad frame (a failing push, a schema slip) must not drop the node's link
            try:
                p = frame.payload
                if frame.type != NODE_MESSAGES:
                    log.debug("frame %s from %s", frame.type, link.machine if link else "?")
                if frame.type == NODE_HELLO:
                    m = Machine.model_validate(
                        {**p.get("machine", {}), "online": True, "last_seen": now_ms()}
                    )
                    link = NodeLink(machine=m.id, ws=ws)
                    old = state.nodes.get(m.id)
                    state.nodes[m.id] = link
                    if old and old.ws is not ws:
                        try:
                            await old.ws.close(code=4409)
                        except Exception:  # noqa: BLE001
                            pass
                    await state.db.upsert_machine(m)
                    state.bus.publish("machine.updated", m.model_dump())
                    await link.send(
                        "hello.ok",
                        {"server_time": now_ms(), "armed": m.armed, "armed_until": m.armed_until},
                    )
                    log.info("node %s connected", m.id)
                    continue
                if link is None:
                    continue
                if frame.type == NODE_SESSIONS:
                    sessions = [Session.model_validate(s) for s in p.get("sessions", [])]
                    state.titler.apply(sessions)
                    previous = {
                        s.key: s.status for s in await state.db.list_sessions(machine=link.machine)
                    }
                    changed = await state.db.replace_sessions(link.machine, sessions)
                    for s in changed:
                        state.bus.publish("session.updated", s.model_dump())
                        await state.on_session_transition(s, previous.get(s.key))
                    await state.prewarm(link.machine, sessions)
                elif frame.type == NODE_MESSAGES:
                    key = p.get("session_key", "")
                    msgs = [Message.model_validate(m) for m in p.get("messages", [])]
                    reset = bool(p.get("reset"))
                    state.cache_messages(key, msgs, reset)
                    # a first batch is a whole transcript: with every live session kept warm,
                    # broadcasting those would flood each browser. Pages that hold this
                    # transcript are told to fetch it again; the rest ask when they need it.
                    state.bus.publish(
                        "session.messages",
                        {
                            "session_key": key,
                            "messages": [] if reset else [slim(m.model_dump()) for m in msgs],
                            "reset": reset,
                        },
                    )
                elif frame.type == NODE_USAGE:
                    await state.usage_snapshot(
                        [UsageWindow.model_validate(w) for w in p.get("windows", [])]
                    )
                elif frame.type == NODE_DECISION_CREATED:
                    await state.decision_created(Decision.model_validate(p))
                elif frame.type == NODE_DECISION_RESOLVED:
                    await state.decision_resolved(Decision.model_validate(p))
                elif frame.type == NODE_EVENT:
                    await state.node_event(link.machine, p)
            except WebSocketDisconnect:
                raise
            except Exception:  # noqa: BLE001
                log.exception("frame %s from %s failed", frame.type, link.machine if link else "?")
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.exception("node websocket error")
    finally:
        if link and state.nodes.get(link.machine) is link:
            del state.nodes[link.machine]
            await state.db.set_machine_online(link.machine, False)
            state.bus.publish("machine.updated", {"id": link.machine, "online": False})
            for s in await state.db.list_sessions(machine=link.machine):
                state.bus.publish("session.updated", s.model_dump())
            for d in await state.db.expire_decisions(machine=link.machine):
                state.bus.publish("decision.updated", d.model_dump())
            log.info("node %s disconnected", link.machine)
