"""Websocket link from a node to the hub with reconnect."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import websockets

from ..models import Frame

log = logging.getLogger(__name__)

Handler = Callable[[Frame], Awaitable[None]]


class HubClient:
    def __init__(self, url: str, token: str, on_frame: Handler) -> None:
        self.url = url
        self.token = token
        self.on_frame = on_frame
        self._ws: websockets.ClientConnection | None = None
        self._send_q: asyncio.Queue[str] = asyncio.Queue(maxsize=1000)
        self.connected = asyncio.Event()

    async def send(self, type_: str, payload: dict[str, Any] | None = None) -> None:
        frame = Frame(type=type_, payload=payload or {})
        try:
            self._send_q.put_nowait(frame.model_dump_json())
        except asyncio.QueueFull:
            log.warning("hub send queue full, dropping %s", type_)

    async def run(self, hello: dict[str, Any]) -> None:
        backoff = 1.0
        while True:
            try:
                headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
                async with websockets.connect(
                    self.url, additional_headers=headers, ping_interval=20, max_size=8_000_000
                ) as ws:
                    self._ws = ws
                    backoff = 1.0
                    await ws.send(Frame(type="hello", payload=hello).model_dump_json())
                    self.connected.set()
                    log.info("connected to hub %s", self.url)
                    await asyncio.gather(self._pump_out(ws), self._pump_in(ws))
            except (OSError, websockets.WebSocketException, asyncio.CancelledError) as e:
                if isinstance(e, asyncio.CancelledError):
                    raise
                log.warning("hub link down (%s); retry in %.0fs", e, backoff)
            finally:
                self.connected.clear()
                self._ws = None
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    async def _pump_out(self, ws: websockets.ClientConnection) -> None:
        while True:
            msg = await self._send_q.get()
            await ws.send(msg)

    async def _pump_in(self, ws: websockets.ClientConnection) -> None:
        async for raw in ws:
            try:
                frame = Frame.model_validate(json.loads(raw))
            except (json.JSONDecodeError, ValueError) as e:
                log.warning("bad frame from hub: %s", e)
                continue
            try:
                await self.on_frame(frame)
            except Exception:  # noqa: BLE001
                log.exception("handler failed for %s", frame.type)
