"""REST + SSE API used by the web app."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from ..models import now_ms as _now
from . import cockpit as ck
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
        # wait for the node's initial batch (large transcripts take a few seconds to parse)
        for _ in range(120):
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
    config_dir: str = ""  # which Claude login, by directory name (".claude-team")


@router.post("/machines/{machine_id}/sessions")
async def start_session(machine_id: str, body: StartBody, request: Request):
    st = _state(request)
    try:
        return await st.session_start(machine_id, body.model_dump())
    except TimeoutError as e:
        raise HTTPException(504, "node did not answer") from e


class CleanupBody(BaseModel):
    older_than_hours: float = 48
    keys: list[str] | None = None  # explicit selection, else every stale one


@router.post("/machines/{machine_id}/cleanup")
async def cleanup(machine_id: str, body: CleanupBody, request: Request):
    """Remove stale finished/blocked background sessions (Claude `rm`); others are only hidden."""
    st = _state(request)
    cutoff = _now() - body.older_than_hours * 3600 * 1000
    targets = []
    for s in await st.db.list_sessions(machine_id, limit=2000):
        if body.keys is not None and s.key not in body.keys:
            continue
        if body.keys is None and (s.status == "busy" or s.updated_at > cutoff):
            continue
        if s.harness == "claude" and s.kind == "background":
            targets.append(s.key)
    results = []
    for key in targets:
        try:
            r = await st.session_action(key, "rm")
        except TimeoutError:
            r = {"ok": False, "error": "timeout"}
        results.append({"key": key, **{k: r.get(k) for k in ("ok", "error")}})
    return {"removed": sum(1 for r in results if r.get("ok")), "results": results}


@router.get("/machines/{machine_id}/dirs")
async def machine_dirs(machine_id: str, request: Request):
    """Recently used working directories on a machine, most recent first."""
    seen: dict[str, int] = {}
    for s in await _state(request).db.list_sessions(machine_id, limit=2000):
        if s.cwd and not s.cwd.startswith("/tmp/"):
            seen[s.cwd] = max(seen.get(s.cwd, 0), s.updated_at)
    return [d for d, _ in sorted(seen.items(), key=lambda kv: -kv[1])][:40]


async def _cockpit_inputs(st: HubState):
    sessions = await st.db.list_sessions(limit=1000)
    machines = await st.db.list_machines()
    decisions = await st.db.list_decisions(pending_only=True)
    usage = await st.db.latest_usage()
    since = _now() - 3 * 3600 * 1000
    history = {
        f"{w.provider}:{w.account}:{w.window}": await st.db.usage_history(
            w.provider, w.window, since, w.account
        )
        for w in usage
        if w.provider != "openrouter"
    }
    findings = ck.analyse(sessions, machines, decisions, usage, history)
    silenced = await st.db.silenced()
    quiet = {x["id"] for x in silenced}
    findings = [f for f in findings if f.id not in quiet]
    return sessions, machines, usage, findings, silenced


async def _agents(request: Request) -> dict:
    s = request.app.state.settings
    stored = await _state(request).db.get_setting("agents")
    return ck.agent_settings(stored, s.cockpit_node, s.pa_node)


@router.get("/cockpit")
async def cockpit(request: Request):
    """Findings from the rules plus the last advice. Reading never spends model tokens."""
    st = _state(request)
    sessions, machines, usage, findings, silenced = await _cockpit_inputs(st)
    stats = ck.stats(sessions, machines)
    d = ck.digest(sessions, machines, usage, findings)
    d["silenced_by_owner"] = [x["title"] for x in silenced]
    return {
        "headline": ck.headline(findings, stats),
        "stats": stats,
        "findings": ck.to_json(findings),
        "headroom": ck.provider_headroom(usage),
        "silenced": silenced,
        **_quiet_view(st.briefer.view(ck.fingerprint(d)), silenced),
    }


def _quiet_view(view: dict, silenced: list[dict]) -> dict:
    """The cached briefing without the suggestions the owner silenced."""
    b = view.get("briefing")
    if b:
        quiet = {x["id"] for x in silenced}
        view = {
            **view,
            "briefing": {
                **b,
                "suggestions": [s for s in b["suggestions"] if s.get("id") not in quiet],
            },
        }
    return view


class SilenceBody(BaseModel):
    id: str
    title: str = ""
    hours: float | None = None  # None = for good


@router.post("/cockpit/silence")
async def cockpit_silence(body: SilenceBody, request: Request):
    until = int(_now() + body.hours * 3600 * 1000) if body.hours else 0
    await _state(request).db.silence(body.id, body.title[:200], until)
    _state(request).bus.publish("cockpit.updated", {})
    return {"ok": True, "until": until}


@router.delete("/cockpit/silence/{item_id:path}")
async def cockpit_unsilence(item_id: str, request: Request):
    await _state(request).db.unsilence(item_id)
    _state(request).bus.publish("cockpit.updated", {})
    return {"ok": True}


@router.post("/cockpit/brief")
async def cockpit_brief(request: Request):
    """Generate a fresh briefing now and return the result."""
    st = _state(request)
    sessions, machines, usage, findings, silenced = await _cockpit_inputs(st)
    d = ck.digest(sessions, machines, usage, findings)
    d["silenced_by_owner"] = [x["title"] for x in silenced]
    await st.briefer.refresh(st, d, (await _agents(request))["advice"])
    return _quiet_view(st.briefer.view(ck.fingerprint(d)), silenced)


class AgentsBody(BaseModel):
    advice: dict = {}
    personal: dict = {}
    titles: dict = {}


_AGENT_FIELDS = {
    "advice": {"machine", "harness", "model", "login", "effort"},
    "personal": {"machine", "model", "login"},
    "titles": {"enabled", "model"},
}


@router.get("/settings/agents")
async def get_agents(request: Request):
    """Which agent the dashboard itself uses, and the choices that exist on the fleet."""
    st = _state(request)
    logins = {}
    for w in await st.db.latest_usage():
        d = str(w.detail.get("config_dir") or "")
        if w.provider == "anthropic" and d:
            logins[d] = w.account
    return {
        "agents": await _agents(request),
        "machines": sorted(st.nodes),
        "logins": [{"dir": d, "account": a} for d, a in sorted(logins.items())],
    }


@router.put("/settings/agents")
async def put_agents(body: AgentsBody, request: Request):
    clean = {
        section: {k: v for k, v in getattr(body, section).items() if k in fields}
        for section, fields in _AGENT_FIELDS.items()
    }
    if clean["advice"].get("harness") not in (None, "claude", "codex", "api"):
        raise HTTPException(400, "unknown harness")
    await _state(request).db.set_setting("agents", clean)
    return {"ok": True, "agents": await _agents(request)}


async def _pa(request: Request, payload: dict, timeout: float = 120) -> dict:
    """Relay to the node that holds the personal-assistant repo. Nothing is stored here."""
    st = _state(request)
    agents = await _agents(request)
    prefer = agents["personal"]["machine"]
    if payload.get("op") == "run":
        payload = {**payload, "agent": agents["personal"]}
    order = sorted(st.nodes, key=lambda m: (m != prefer, m))
    if not order:
        raise HTTPException(503, "no machine is online")
    for machine in order:
        try:
            res = await st.request(st.nodes[machine], "pa.request", payload, timeout=timeout)
        except TimeoutError as e:
            raise HTTPException(504, f"{machine} did not answer") from e
        if res.get("error") != "no pa":
            return {**res, "machine": machine}
    return {"ok": False, "error": "no online machine has the personal-assistant repo"}


@router.get("/pa")
async def pa_get(request: Request):
    return await _pa(request, {"op": "get"}, timeout=30)


class PARunBody(BaseModel):
    focus: str = ""


@router.post("/pa/run")
async def pa_run(body: PARunBody, request: Request):
    return await _pa(request, {"op": "run", "focus": body.focus})


class PAItemBody(BaseModel):
    id: str
    op: str  # send_email | discard | mark
    body: str | None = None
    status: str = ""
    note: str = ""


@router.post("/pa/item")
async def pa_item(body: PAItemBody, request: Request):
    if body.op not in ("send_email", "discard", "mark"):
        raise HTTPException(400, "unknown operation")
    return await _pa(request, body.model_dump(), timeout=200)


@router.get("/usage")
async def usage(request: Request):
    return [w.model_dump() for w in await _state(request).db.latest_usage()]


@router.get("/usage/history")
async def usage_history(
    request: Request, provider: str, window: str, hours: int = 48, account: str | None = None
):
    pts = await _state(request).db.usage_history(
        provider, window, _now() - hours * 3600 * 1000, account
    )
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
