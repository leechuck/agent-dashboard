"""In-process event bus fanning hub events out to SSE subscribers."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from typing import Any


class EventBus:
    def __init__(self, history: int = 200) -> None:
        self._subs: set[asyncio.Queue[str]] = set()
        self._history: deque[str] = deque(maxlen=history)

    def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=500)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        self._subs.discard(q)

    def publish(self, kind: str, data: dict[str, Any]) -> None:
        msg = json.dumps({"kind": kind, "data": data})
        self._history.append(msg)
        for q in list(self._subs):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                self._subs.discard(q)
