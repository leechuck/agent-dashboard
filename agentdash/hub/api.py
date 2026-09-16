"""REST + SSE API used by the web app."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from .auth import require_web_token
from .state import HubState

router = APIRouter(prefix="/api", dependencies=[Depends(require_web_token)])


def _state(request: Request) -> HubState:
    return request.app.state.hub


@router.get("/config")
async def config(request: Request):
    s = request.app.state.settings
    return {"history_public_url": s.history_public_url, "history_enabled": bool(s.history_url)}


@router.get("/machines")
async def machines(request: Request):
    return [m.model_dump() for m in await _state(request).db.list_machines()]


class ArmBody(BaseModel):
    armed: bool
    hours: float | None = None


@router.post("/machines/{machine_id}/arm")
async def arm(machine_id: str, body: ArmBody, request: Request):
    st = _state(request)
    hours = body.hours if body.hours is not None else request.app.state.settings.arm_hours
    return await st.set_armed(machine_id, body.armed, hours)


@router.get("/decisions")
async def decisions(request: Request, pending: bool = False, limit: int = 100):
    st = _state(request)
    for d in await st.db.expire_decisions():
        st.bus.publish("decision.updated", d.model_dump())
    return [d.model_dump() for d in await st.db.list_decisions(pending, limit)]


class AnswerBody(BaseModel):
    behavior: str  # allow | deny
    reason: str = ""
    remember: bool = False


@router.post("/decisions/{decision_id}/answer")
async def answer(decision_id: str, body: AnswerBody, request: Request):
    if body.behavior not in ("allow", "deny"):
        raise HTTPException(400, "behavior must be allow or deny")
    return await _state(request).answer_decision(
        decision_id, body.behavior, body.reason, body.remember
    )


@router.get("/push/key")
async def push_key(request: Request):
    st = _state(request)
    return {
        "key": st.pusher.public_key if st.pusher else "",
        "enabled": bool(st.pusher and st.pusher.enabled),
    }


class SubscribeBody(BaseModel):
    subscription: dict
    label: str = ""


@router.post("/push/subscribe")
async def push_subscribe(body: SubscribeBody, request: Request):
    await _state(request).db.add_push_subscription(body.subscription, body.label)
    return {"ok": True}


@router.post("/push/unsubscribe")
async def push_unsubscribe(body: SubscribeBody, request: Request):
    await _state(request).db.remove_push_subscription(body.subscription.get("endpoint", ""))
    return {"ok": True}


@router.post("/push/test")
async def push_test(request: Request):
    await _state(request).notify("agentdash", "Notifications are working.", "/#/")
    return {"ok": True}


@router.get("/sessions")
async def sessions(request: Request, machine: str | None = None, active: bool = False):
    return [s.model_dump() for s in await _state(request).db.list_sessions(machine, active)]


@router.get("/sessions/{key}")
async def session(key: str, request: Request):
    s = await _state(request).db.get_session(key)
    if not s:
        raise HTTPException(404, "unknown session")
    return s.model_dump()


@router.get("/sessions/{key}/messages")
async def session_messages(key: str, request: Request):
    st = _state(request)
    s = await st.db.get_session(key)
    if not s:
        raise HTTPException(404, "unknown session")
    if key not in st.caches:
        await st.ensure_subscribed(key)
        # wait briefly for the node's initial batch
        for _ in range(30):
            if key in st.caches:
                break
            await asyncio.sleep(0.1)
    return [m.model_dump() for m in st.caches.get(key, [])]


class PromptBody(BaseModel):
    text: str


@router.post("/sessions/{key}/prompt")
async def session_prompt(key: str, body: PromptBody, request: Request):
    st = _state(request)
    if not body.text.strip():
        raise HTTPException(400, "empty prompt")
    if not await st.db.get_session(key):
        raise HTTPException(404, "unknown session")
    try:
        return await st.send_prompt(key, body.text)
    except TimeoutError as e:
        raise HTTPException(504, "node did not answer") from e


class ActionBody(BaseModel):
    action: str  # stop | rm | respawn | terminate | kill


@router.post("/sessions/{key}/action")
async def session_action(key: str, body: ActionBody, request: Request):
    st = _state(request)
    if not await st.db.get_session(key):
        raise HTTPException(404, "unknown session")
    try:
        return await st.session_action(key, body.action)
    except TimeoutError as e:
        raise HTTPException(504, "node did not answer") from e


class StartBody(BaseModel):
    cwd: str
    prompt: str = ""
    name: str = ""
    resume: str = ""
    permission_mode: str = ""
    provider: str = "anthropic"


@router.post("/machines/{machine_id}/sessions")
async def start_session(machine_id: str, body: StartBody, request: Request):
    st = _state(request)
    try:
        return await st.session_start(machine_id, body.model_dump())
    except TimeoutError as e:
        raise HTTPException(504, "node did not answer") from e


@router.get("/machines/{machine_id}/dirs")
async def machine_dirs(machine_id: str, request: Request):
    """Recently used working directories on a machine, most recent first."""
    seen: dict[str, int] = {}
    for s in await _state(request).db.list_sessions(machine_id, limit=2000):
        if s.cwd and not s.cwd.startswith("/tmp/"):
            seen[s.cwd] = max(seen.get(s.cwd, 0), s.updated_at)
    return [d for d, _ in sorted(seen.items(), key=lambda kv: -kv[1])][:40]


@router.get("/usage")
async def usage(request: Request):
    return [w.model_dump() for w in await _state(request).db.latest_usage()]


@router.get("/usage/history")
async def usage_history(request: Request, provider: str, window: str, hours: int = 48):
    from ..models import now_ms as _now

    pts = await _state(request).db.usage_history(provider, window, _now() - hours * 3600 * 1000)
    return [{"t": t, "pct": p} for t, p in pts]


@router.get("/events/recent")
async def recent_events(request: Request, limit: int = 50):
    return await _state(request).db.list_events(limit)


@router.get("/events")
async def events(request: Request):
    st = _state(request)
    q = st.bus.subscribe()

    async def gen():
        try:
            yield {"event": "hello", "data": json.dumps({"ok": True})}
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), 15)
                except TimeoutError:
                    yield {"event": "ping", "data": "{}"}
                    continue
                yield {"event": "message", "data": msg}
        finally:
            st.bus.unsubscribe(q)

    return EventSourceResponse(gen())
