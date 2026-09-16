"""Hub runtime state: connected nodes, message caches, pending requests."""

from __future__ import annotations

import asyncio
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
    HUB_SUBSCRIBE,
    HUB_UNSUBSCRIBE,
    Decision,
    DecisionStatus,
    Frame,
    Message,
    now_ms,
)
from .bus import EventBus
from .push import Pusher


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
        subs = await self.db.list_push_subscriptions()
        gone = await self.pusher.send(subs, {"title": title, "body": body, "url": url, "tag": tag})
        for ep in gone:
            await self.db.remove_push_subscription(ep)

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

    async def on_node_event(self, machine: str, p: dict[str, Any]) -> None:
        kind = p.get("kind", "")
        if kind in ("claude.permission_prompt", "claude.agent_needs_input") and not p.get("armed"):
            key = p.get("session_key", "")
            sess = await self.db.get_session(key)
            where = (sess.name if sess else "") or key.split(":")[-1][:8]
            await self.notify(
                f"{where} needs you at the terminal",
                p.get("message") or "permission prompt on " + machine,
                f"/#/session/{key}",
                tag=key,
            )

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
