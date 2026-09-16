"""Hub runtime state: connected nodes, message caches, pending requests."""

from __future__ import annotations

import asyncio
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

from ..db import Database
from ..models import HUB_SEND_PROMPT, HUB_SUBSCRIBE, HUB_UNSUBSCRIBE, Frame, Message
from .bus import EventBus


@dataclass
class NodeLink:
    machine: str
    ws: WebSocket
    subscriptions: set[str] = field(default_factory=set)

    async def send(self, type_: str, payload: dict[str, Any] | None = None) -> None:
        await self.ws.send_text(Frame(type=type_, payload=payload or {}).model_dump_json())


class HubState:
    def __init__(self, db: Database, bus: EventBus) -> None:
        self.db = db
        self.bus = bus
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

    async def send_prompt(self, session_key: str, text: str) -> dict[str, Any]:
        link = self.node_for(session_key)
        if not link:
            return {"ok": False, "error": "machine offline"}
        return await self.request(link, HUB_SEND_PROMPT, {"session_key": session_key, "text": text})
