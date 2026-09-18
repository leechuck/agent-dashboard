"""REST + SSE API used by the web app."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from ..models import now_ms as _now
from . import blobs
from . import cockpit as ck
from .auth import require_web_token
from .state import HubState, slim

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
    s = await _state(request).find_session(key)
    if not s:
        raise HTTPException(404, "unknown session")
    return s.model_dump()


@router.get("/sessions/{key}/messages")
async def session_messages(key: str, request: Request):
    st = _state(request)
    s = await st.find_session(key)
    if not s:
        raise HTTPException(404, "unknown session")
    if not st.caches.get(key):  # nothing yet, or an empty answer from a node that was not ready
        await st.ensure_subscribed(key)
        # wait for the node's initial batch (large transcripts take a few seconds to parse)
        for _ in range(120):
            if st.caches.get(key) or (key in st.caches and not s.transcript_path):
                break
            await asyncio.sleep(0.1)
    return [slim(m.model_dump()) for m in st.caches.get(key, [])]


@router.get("/sessions/{key}/message")
async def session_message(key: str, id: str, request: Request):
    """One message in full (the list carries long tool calls and results cut short)."""
    for m in _state(request).caches.get(key, []):
        if m.id == id:
            return m.model_dump()
    raise HTTPException(404, "message is no longer cached")


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
    harness: str = "claude"
    backend: str = "default"  # default | login | endpoint
    login: str = ""
    endpoint: str = ""
    model: str = ""
    effort: str = ""
    permissions: str = "default"
    mode: str = "background"  # background (Claude job) | tmux
    # older clients
    permission_mode: str = ""
    config_dir: str = ""


@router.post("/machines/{machine_id}/sessions")
async def start_session(machine_id: str, body: StartBody, request: Request):
    st = _state(request)
    try:
        spec = {**body.model_dump(), "endpoints": await ck.endpoints_setting(st.db)}
        return await st.session_start(machine_id, spec)
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


async def _node_call(request: Request, machine: str, type_: str, payload: dict, timeout=60):
    st = _state(request)
    link = st.nodes.get(machine)
    if not link:
        return {"ok": False, "error": f"{machine} is offline"}
    payload = {**payload, "endpoints": await ck.endpoints_setting(st.db)}
    try:
        return await st.request(link, type_, payload, timeout=timeout)
    except TimeoutError as e:
        raise HTTPException(504, f"{machine} did not answer") from e


@router.get("/sessions/{key}/commands")
async def session_commands(key: str, request: Request):
    """Slash commands this session accepts, for completion in the composer."""
    st = _state(request)
    s = await st.find_session(key)
    if not s:
        raise HTTPException(404, "unknown session")
    cached = st.commands.get(key)
    if cached:
        return cached
    r = await _node_call(
        request,
        key.split(":", 1)[0],
        "commands.list",
        {"session_key": key, "harness": s.harness, "cwd": s.cwd},
        timeout=30,
    )
    if r.get("ok"):
        st.commands[key] = r
    return r


@router.get("/catalog")
async def catalog(request: Request, fresh: bool = False):
    """Per machine: installed harnesses, Claude logins, models, and which endpoints work there."""
    st = _state(request)
    machines = sorted(st.nodes)
    results = await asyncio.gather(
        *(_node_call(request, m, "catalog.get", {"fresh": fresh}, timeout=40) for m in machines),
        return_exceptions=True,
    )
    return {
        "endpoints": await ck.endpoints_setting(st.db),
        "machines": {
            m: (r if isinstance(r, dict) else {"ok": False, "error": "did not answer"})
            for m, r in zip(machines, results, strict=True)
        },
    }


class EndpointsBody(BaseModel):
    endpoints: list[dict]


@router.put("/settings/endpoints")
async def put_endpoints(body: EndpointsBody, request: Request):
    fields = ("id", "name", "base_url", "anthropic_base_url", "key_env", "wire_api",
              "context_window", "models")  # fmt: skip
    clean = []
    for raw in body.endpoints:
        e = {k: raw[k] for k in fields if raw.get(k) not in (None, "", [])}
        if not e.get("id") or not (e.get("base_url") or e.get("anthropic_base_url")):
            raise HTTPException(400, "an endpoint needs an id and an address")
        if e.get("key_env") and not str(e["key_env"]).replace("_", "").isalnum():
            raise HTTPException(400, "the key variable must be a plain name such as MY_LLM_KEY")
        clean.append(e)
    await _state(request).db.set_setting("endpoints", {"list": clean})
    return {"ok": True, "endpoints": clean}


class LoginBody(BaseModel):
    name: str


@router.post("/machines/{machine_id}/logins")
async def open_login(machine_id: str, body: LoginBody, request: Request):
    """Create the login directory if needed and open Claude on it in tmux for /login."""
    r = await _node_call(request, machine_id, "login.open", {"name": body.name.strip().lower()})
    if r.get("ok") and r.get("tmux"):
        t = r["tmux"]
        r["terminal_key"] = f"{machine_id}:tmux:{t['socket']}:{t['target']}"
    return r


class SwitchBody(BaseModel):
    harness: str = ""
    backend: str = "default"
    login: str = ""
    endpoint: str = ""
    model: str = ""
    effort: str = ""
    permissions: str = "default"
    note: str = ""
    force: bool = False
    stop_old: bool = False


@router.post("/sessions/{key}/switch")
async def switch_session(key: str, body: SwitchBody, request: Request):
    """Same harness: restart the conversation on another login, endpoint or model.
    Other harness: start it in the same directory with a handover briefing."""
    machine = key.split(":", 1)[0]
    payload = {**body.model_dump(), "session_key": key, "mode": "tmux"}
    r = await _node_call(request, machine, "session.switch", payload, timeout=90)
    if r.get("ok") and r.get("tmux"):
        t = r["tmux"]
        r["terminal_key"] = f"{machine}:tmux:{t['socket']}:{t['target']}"
    return r


@router.get("/past")
async def past_sessions(
    request: Request, machine: str = "", harness: str = "", q: str = "", limit: int = 120
):
    """Sessions that are over but still on disk, from every online machine (or one):
    what can be opened, resumed, or moved elsewhere."""
    st = _state(request)
    machines = [machine] if machine else sorted(st.nodes)
    payload = {"harness": harness, "q": q, "limit": limit}
    results = await asyncio.gather(
        *(_node_call(request, m, "past.list", payload, timeout=60) for m in machines),
        return_exceptions=True,
    )
    sessions: list[dict] = []
    errors: dict[str, str] = {}
    for m, r in zip(machines, results, strict=True):
        if not isinstance(r, dict) or not r.get("ok"):
            errors[m] = str(r.get("error") if isinstance(r, dict) else r)[:200]
            continue
        sessions += r.get("sessions") or []
    sessions.sort(key=lambda s: -int(s.get("updated_at") or 0))
    sessions = sessions[:limit]
    from ..models import Session

    for raw in sessions:
        try:
            st.past[raw["key"]] = Session.model_validate(raw)
        except ValueError:
            continue
    return {"sessions": sessions, "errors": errors}


class MoveBody(BaseModel):
    machine: str  # where it goes
    cwd: str = ""  # folder there; default the same path
    backend: str = "default"
    login: str = ""
    endpoint: str = ""
    model: str = ""
    effort: str = ""
    permissions: str = "default"
    note: str = ""
    stop_old: bool = True
    force: bool = False
    name: str = ""


@router.post("/sessions/{key}/move")
async def move_session(key: str, body: MoveBody, request: Request):
    """Resume a session on another machine: its node packs the transcript, the hub holds
    the bundle for a moment, the other node files it and starts the agent on it."""
    st = _state(request)
    source = key.split(":", 1)[0]
    if body.machine == source:
        raise HTTPException(400, "that is the machine it is on")
    if body.machine not in st.nodes:
        raise HTTPException(409, f"{body.machine} is offline")
    exported = await _node_call(
        request,
        source,
        "session.export",
        {"session_key": key, "stop": body.stop_old, "force": body.force},
        timeout=900,
    )
    if not exported.get("ok"):
        return exported
    blob = str(exported.get("blob") or "")
    try:
        spec = {
            **body.model_dump(),
            "harness": exported["harness"],
            "session_id": exported["session_id"],
            "cwd": body.cwd or exported.get("cwd") or "",
            "blob": blob,
            "mode": "tmux",
            "name": body.name or exported.get("name") or "",
        }
        imported = await _node_call(request, body.machine, "session.import", spec, timeout=900)
    finally:
        blobs.remove(request.app.state.settings.state_dir, blob)
    if imported.get("ok") and imported.get("tmux"):
        t = imported["tmux"]
        imported["terminal_key"] = f"{body.machine}:tmux:{t['socket']}:{t['target']}"
    imported["stopped"] = bool(exported.get("stopped"))
    imported["source"] = source
    return imported


def _pane_of(key: str, sess) -> dict:
    """tmux coordinates of a session, or of a pane key "<machine>:tmux:<server>:<target>"."""
    tmux = (sess.extra.get("tmux") if sess else None) or {}
    parts = key.split(":", 3)
    if not tmux and len(parts) == 4 and parts[1] == "tmux":
        tmux = {"socket": parts[2], "target": parts[3]}
    return tmux


@router.get("/panes/{key:path}/screen")
async def pane_screen(key: str, request: Request):
    """What a pane asks for right now (a login screen read into stage, link and options)."""
    st = _state(request)
    tmux = _pane_of(key, await st.find_session(key))
    if not tmux:
        raise HTTPException(404, "not a tmux pane")
    return await _node_call(request, key.split(":", 1)[0], "pane.screen", tmux, timeout=20)


class KeysBody(BaseModel):
    text: str = ""
    keys: list[str] = []


@router.post("/panes/{key:path}/keys")
async def pane_keys(key: str, body: KeysBody, request: Request):
    """Answer a menu or paste a code into a pane, without a terminal."""
    st = _state(request)
    tmux = _pane_of(key, await st.find_session(key))
    if not tmux:
        raise HTTPException(404, "not a tmux pane")
    payload = {**tmux, "text": body.text[:4000], "keys": body.keys[:10]}
    return await _node_call(request, key.split(":", 1)[0], "pane.keys", payload, timeout=20)


class TitleBody(BaseModel):
    title: str = ""


@router.put("/sessions/{key}/title")
async def set_title(key: str, body: TitleBody, request: Request):
    """Rename a session; an empty title hands naming back to the model."""
    st = _state(request)
    await st.titler.rename(st.db, key, body.title)
    await _publish_title(st, key)
    return {"ok": True, "title": st.titler.titles.get(key, "")}


@router.post("/sessions/{key}/title/regenerate")
async def regenerate_title(key: str, request: Request):
    st = _state(request)
    s = await st.db.get_session(key)
    if not s:
        raise HTTPException(404, "unknown session")
    st.titler.basis.pop(key, None)
    n = await st.titler.name(st, await _agents(request), [s])
    await _publish_title(st, key)
    return {"ok": n > 0, "title": st.titler.titles.get(key, "")}


@router.post("/titles/regenerate")
async def regenerate_titles(request: Request):
    """Name every live session again, except the ones the owner named."""
    st = _state(request)
    live = [
        s
        for s in await st.db.list_sessions(active_only=True)
        if s.harness != "tmux" and st.titler.basis.get(s.key) != ck.MANUAL
    ]
    return {"ok": True, "renamed": await st.titler.name(st, await _agents(request), live[:25])}


async def _publish_title(st: HubState, key: str) -> None:
    s = await st.db.get_session(key)
    if s:
        s.extra.pop("title", None)
        st.titler.apply([s])
        await st.db.update_session(s)
        st.bus.publish("session.updated", s.model_dump())


class AgentsBody(BaseModel):
    advice: dict = {}
    personal: dict = {}
    titles: dict = {}


_AGENT_FIELDS = {
    "advice": {"machine", "harness", "model", "login", "endpoint", "effort"},
    "personal": {"machine", "backend", "model", "login", "endpoint"},
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
    if payload.get("op") in ("run", "ask"):
        payload = {
            **payload,
            "agent": agents["personal"],
            "endpoints": await ck.endpoints_setting(st.db),
        }
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


class PAAskBody(BaseModel):
    history: list[dict[str, str]] = []
    question: str
    topic: str = "briefing"
    week: str = ""
    member: str = ""


@router.get("/pa/group")
async def pa_group(request: Request):
    return await _pa(request, {"op": "group"}, timeout=60)


@router.post("/pa/ask")
async def pa_ask(body: PAAskBody, request: Request):
    return await _pa(request, {**body.model_dump(), "op": "ask"}, timeout=240)


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


_PA_NAME = re.compile(r"^[a-z][a-z_]{0,31}$")


@router.get("/pa/panel/{name}")
async def pa_panel(name: str, request: Request, fresh: bool = False):
    """One panel of the Personal tab, computed on the node by a script in the PA repo."""
    if not _PA_NAME.match(name):
        raise HTTPException(400, "unknown panel")
    return await _pa(request, {"op": "panel", "name": name, "fresh": fresh}, timeout=90)


class PAActBody(BaseModel):
    act: str
    args: dict[str, Any] = {}


@router.post("/pa/act")
async def pa_act(body: PAActBody, request: Request):
    """An edit from a panel (tick a todo, add a reminder). The node validates and runs it."""
    if not _PA_NAME.match(body.act):
        raise HTTPException(400, "unknown action")
    return await _pa(request, {"op": "act", "act": body.act, "args": body.args}, timeout=330)


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
