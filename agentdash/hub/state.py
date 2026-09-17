"""Hub runtime state: connected nodes, message caches, pending requests."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

from ..db import Database
from ..models import (
    HUB_ARM,
    HUB_DECISION_ANSWER,
    HUB_SEND_PROMPT,
    HUB_SESSION_ACTION,
    HUB_SESSION_START,
    HUB_SUBSCRIBE,
    HUB_UNSUBSCRIBE,
    Decision,
    DecisionStatus,
    Frame,
    Message,
    UsageWindow,
    now_ms,
)
from .bus import EventBus
from .cockpit import Briefer, Titler
from .push import Pusher

log = logging.getLogger(__name__)

SLIM_CHARS = 400


def slim(m: dict[str, Any]) -> dict[str, Any]:
    """A message as the transcript list needs it. Tool calls and results are shown folded
    to one line, and they are most of the bytes; the full message is fetched when opened."""
    if m.get("kind") not in ("tool_use", "tool_result"):
        return m
    out, cut = dict(m), False
    text = out.get("text") or ""
    if len(text) > SLIM_CHARS:
        out["text"], cut = text[:SLIM_CHARS], True
    ti = out.get("tool_input")
    if isinstance(ti, dict):
        small = {}
        for k, v in ti.items():
            if isinstance(v, str) and len(v) > SLIM_CHARS:
                small[k], cut = v[:SLIM_CHARS], True
            elif isinstance(v, (dict, list)) and len(str(v)) > SLIM_CHARS:
                small[k], cut = str(v)[:SLIM_CHARS], True
            else:
                small[k] = v
        out["tool_input"] = small
    if cut:
        out["slim"] = True
    return out


@dataclass
class NodeLink:
    machine: str
    ws: WebSocket
    subscriptions: set[str] = field(default_factory=set)

    async def send(self, type_: str, payload: dict[str, Any] | None = None) -> None:
        await self.ws.send_text(Frame(type=type_, payload=payload or {}).model_dump_json())


class HubState:
    def __init__(self, db: Database, bus: EventBus, pusher: Pusher | None = None) -> None:
        self.db = db
        self.bus = bus
        self.pusher = pusher
        self.nodes: dict[str, NodeLink] = {}
        self.caches: dict[str, deque[Message]] = {}
        self.pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._usage_alerted: dict[str, int] = {}  # provider:window -> threshold pushed
        self.briefer = Briefer()
        self.titler = Titler()

    def node_for(self, session_key: str) -> NodeLink | None:
        machine = session_key.split(":", 1)[0]
        return self.nodes.get(machine)

    async def ensure_subscribed(self, session_key: str) -> None:
        link = self.node_for(session_key)
        if link and session_key not in link.subscriptions:
            link.subscriptions.add(session_key)
            await link.send(HUB_SUBSCRIBE, {"session_key": session_key})

    async def unsubscribe(self, session_key: str) -> None:
        link = self.node_for(session_key)
        if link and session_key in link.subscriptions:
            link.subscriptions.discard(session_key)
            await link.send(HUB_UNSUBSCRIBE, {"session_key": session_key})
        self.caches.pop(session_key, None)

    async def prewarm(self, machine: str, sessions: list[Any], limit: int = 24) -> None:
        """Keep the transcripts of live sessions cached, so opening one is instant.

        Sub-agents, stale and finished sessions are fetched only when someone opens them.
        """
        link = self.nodes.get(machine)
        if not link:
            return
        cutoff = now_ms() - 48 * 3600 * 1000
        want = [
            s.key
            for s in sorted(sessions, key=lambda s: -s.updated_at)
            if s.status in ("busy", "idle", "waiting")
            and s.transcript_path
            and not s.extra.get("parent")
            and (s.status == "busy" or s.updated_at > cutoff)
        ][:limit]
        # a node that reconnected has forgotten what it was tailing: anything cached for it
        # is asked for again, or those transcripts would silently stop updating
        listed = {s.key for s in sessions}
        held = [k for k in self.caches if k.split(":", 1)[0] == machine and k in listed]
        for key in [*want, *held]:
            await self.ensure_subscribed(key)
        gone = {s.key for s in sessions if s.status not in ("busy", "idle", "waiting")}
        for key in [k for k in link.subscriptions if k in gone]:
            await self.unsubscribe(key)

    def cache_messages(self, session_key: str, msgs: list[Message], reset: bool) -> None:
        cache = self.caches.setdefault(session_key, deque(maxlen=2000))
        if reset:
            cache.clear()
        cache.extend(msgs)

    async def request(
        self, link: NodeLink, type_: str, payload: dict[str, Any], timeout: float = 15
    ) -> dict[str, Any]:
        rid = str(uuid.uuid4())
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self.pending[rid] = fut
        try:
            await link.send(type_, {**payload, "request_id": rid})
            return await asyncio.wait_for(fut, timeout)
        finally:
            self.pending.pop(rid, None)

    def resolve(self, request_id: str, result: dict[str, Any]) -> bool:
        fut = self.pending.get(request_id)
        if fut and not fut.done():
            fut.set_result(result)
            return True
        return False

    async def notify(self, title: str, body: str, url: str = "", tag: str = "") -> None:
        if not self.pusher or not self.pusher.enabled:
            return
        try:
            subs = await self.db.list_push_subscriptions()
            payload = {"title": title, "body": body, "url": url, "tag": tag}
            for ep in await self.pusher.send(subs, payload):
                await self.db.remove_push_subscription(ep)
        except Exception:  # noqa: BLE001
            log.exception("notification failed")

    async def set_armed(self, machine_id: str, armed: bool, hours: float) -> dict[str, Any]:
        until = int(now_ms() + hours * 3600 * 1000) if armed and hours > 0 else 0
        await self.db.set_machine_armed(machine_id, armed, until)
        link = self.nodes.get(machine_id)
        if link:
            await link.send(HUB_ARM, {"armed": armed, "armed_until": until})
        self.bus.publish(
            "machine.updated", {"id": machine_id, "armed": armed, "armed_until": until}
        )
        return {"armed": armed, "armed_until": until}

    async def decision_created(self, d: Decision) -> None:
        await self.db.upsert_decision(d)
        self.bus.publish("decision.updated", d.model_dump())
        if d.status != DecisionStatus.pending and d.kind != "question":
            return
        where = d.session_name or d.session_key.split(":")[-1][:8]
        if d.kind == "question":
            await self.notify(
                f"{where} asks a question",
                d.question[:140] or "open the session",
                d.native_url or f"/#/session/{d.session_key}",
                tag=d.id,
            )
        else:
            await self.notify(
                f"{where} wants to run {d.tool_name}",
                _summary(d)[:140],
                f"/#/decisions/{d.id}",
                tag=d.id,
            )

    async def decision_resolved(self, d: Decision) -> None:
        await self.db.upsert_decision(d)
        self.bus.publish("decision.updated", d.model_dump())

    async def answer_decision(
        self, decision_id: str, behavior: str, reason: str = "", remember: bool = False
    ) -> dict[str, Any]:
        d = await self.db.get_decision(decision_id)
        if not d:
            return {"ok": False, "error": "unknown decision"}
        if d.status != DecisionStatus.pending:
            return {"ok": False, "error": f"already {d.status}"}
        link = self.nodes.get(d.machine)
        if not link:
            return {"ok": False, "error": "machine offline"}
        await link.send(
            HUB_DECISION_ANSWER,
            {
                "decision_id": decision_id,
                "behavior": behavior,
                "reason": reason,
                "remember": remember,
            },
        )
        d.status = DecisionStatus.allowed if behavior == "allow" else DecisionStatus.denied
        d.answered_at = now_ms()
        d.answer_reason = reason
        d.remember = remember
        await self.db.upsert_decision(d)
        self.bus.publish("decision.updated", d.model_dump())
        return {"ok": True}

    async def usage_snapshot(self, windows: list[UsageWindow]) -> None:
        for w in windows:
            if w.provider == "anthropic" and " · " in w.account:
                await self.db.adopt_legacy_usage(w.provider, w.account, w.account.split(" · ")[0])
            await self.db.add_usage(w)
            key = f"{w.provider}:{w.account}:{w.window}"
            level = 95 if w.used_pct >= 95 else 80 if w.used_pct >= 80 else 0
            if level and self._usage_alerted.get(key, 0) < level:
                self._usage_alerted[key] = level
                when = ""
                if w.resets_at:
                    mins = max(0, (w.resets_at - now_ms()) // 60000)
                    when = f", resets in {mins // 60} h {mins % 60} min"
                await self.notify(
                    f"{w.provider}{f' ({w.account})' if w.account else ''} {w.label} "
                    f"at {w.used_pct:.0f}%",
                    f"{level}% threshold crossed{when}",
                    "/#/limits",
                    tag=f"usage-{key}",
                )
            elif not level:
                self._usage_alerted.pop(key, None)
        self.bus.publish("usage.updated", [w.model_dump() for w in windows])

    async def on_session_transition(self, s: Any, old_status: str | None) -> None:
        """Push when work you dispatched finishes, or a live session starts waiting."""
        if old_status is None or old_status == s.status:
            return
        name = s.name or s.session_id[:8]
        if s.kind == "background" and old_status == "busy" and s.status in ("done", "idle"):
            await self.notify(
                f"{name} finished on {s.machine}",
                s.last_line[:140] or "background session is done",
                f"/#/session/{s.key}",
                tag=f"done-{s.key}",
            )
        elif s.status == "failed" and old_status != "failed":
            await self.notify(
                f"{name} failed on {s.machine}",
                s.last_line[:140],
                f"/#/session/{s.key}",
                tag=f"fail-{s.key}",
            )

    async def node_event(self, machine: str, p: dict[str, Any]) -> None:
        """An event frame from a node: the answer to a request, a reminder, or news."""
        rid = p.get("request_id")
        if rid and self.resolve(rid, p):
            return
        if p.get("kind") == "pa.reminder":
            # personal text: pushed on to the owner's devices, never stored, never
            # broadcast to open pages (ADR 0007)
            await self.notify(
                str(p.get("title") or "Reminder")[:120],
                str(p.get("body") or "")[:300],
                str(p.get("url") or "/#/personal"),
                tag=str(p.get("tag") or "pa"),
            )
            return
        await self.db.add_event(machine, p.get("session_key", ""), p.get("kind", "event"), p)
        self.bus.publish("event", {"machine": machine, **p})
        await self.on_node_event(machine, p)

    async def on_node_event(self, machine: str, p: dict[str, Any]) -> None:
        kind = p.get("kind", "")
        prompts = (
            "claude.permission_prompt",
            "claude.agent_needs_input",
            "claude.elicitation_dialog",
        )
        if kind in prompts and not p.get("armed"):
            key = p.get("session_key", "")
            sess = await self.db.get_session(key)
            where = (sess.name if sess else "") or key.split(":")[-1][:8]
            await self.notify(
                f"{where} needs you at the terminal",
                p.get("message") or "permission prompt on " + machine,
                f"/#/session/{key}",
                tag=key,
            )

    async def session_action(self, session_key: str, action: str) -> dict[str, Any]:
        link = self.node_for(session_key)
        if not link:
            return {"ok": False, "error": "machine offline"}
        result = await self.request(
            link, HUB_SESSION_ACTION, {"session_key": session_key, "action": action}, timeout=90
        )
        if action == "rm" and result.get("ok"):
            await self.db.delete_session(session_key)
            self.caches.pop(session_key, None)
            self.bus.publish("session.removed", {"key": session_key})
        return result

    async def session_start(self, machine: str, spec: dict[str, Any]) -> dict[str, Any]:
        link = self.nodes.get(machine)
        if not link:
            return {"ok": False, "error": "machine offline"}
        return await self.request(link, HUB_SESSION_START, spec, timeout=120)

    async def send_prompt(self, session_key: str, text: str) -> dict[str, Any]:
        link = self.node_for(session_key)
        if not link:
            return {"ok": False, "error": "machine offline"}
        return await self.request(link, HUB_SEND_PROMPT, {"session_key": session_key, "text": text})


def _summary(d: Decision) -> str:
    i = d.tool_input or {}
    if d.tool_name == "Bash":
        return str(i.get("command", ""))
    for k in ("file_path", "path", "url", "description", "skill", "prompt"):
        if isinstance(i.get(k), str):
            return str(i[k])
    return d.reason or ""
