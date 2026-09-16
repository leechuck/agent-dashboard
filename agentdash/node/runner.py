"""Node main loop: run collectors, tail subscribed transcripts, talk to the hub."""

from __future__ import annotations

import asyncio
import logging
import platform
from pathlib import Path

from .. import __version__
from ..config import Settings
from ..models import (
    HUB_PING,
    HUB_SEND_PROMPT,
    HUB_SUBSCRIBE,
    HUB_UNSUBSCRIBE,
    NODE_EVENT,
    NODE_MESSAGES,
    NODE_PONG,
    NODE_SESSIONS,
    Frame,
    Session,
)
from .adapters.claude_socket import SocketSendError, send_user_message
from .adapters.claude_transcript import TranscriptTail, read_last
from .collectors.claude import ClaudeCollector
from .hubclient import HubClient

log = logging.getLogger(__name__)


class Node:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.machine = settings.machine_id
        self.claude = ClaudeCollector(self.machine, settings.claude_config_dirs)
        self.sessions: dict[str, Session] = {}
        self.tails: dict[str, TranscriptTail] = {}
        self.hub = HubClient(settings.hub_url, settings.node_token, self.on_hub_frame)
        self._refresh = asyncio.Event()

    # hub -> node ------------------------------------------------------
    async def on_hub_frame(self, frame: Frame) -> None:
        p = frame.payload
        if frame.type == HUB_PING:
            await self.hub.send(NODE_PONG)
        elif frame.type == HUB_SUBSCRIBE:
            await self.subscribe(p.get("session_key", ""))
        elif frame.type == HUB_UNSUBSCRIBE:
            self.tails.pop(p.get("session_key", ""), None)
        elif frame.type == HUB_SEND_PROMPT:
            await self.send_prompt(
                p.get("session_key", ""), p.get("text", ""), p.get("request_id", "")
            )
        elif frame.type == "hello.ok":
            self._refresh.set()
        else:
            log.debug("unhandled hub frame %s", frame.type)

    async def subscribe(self, key: str) -> None:
        sess = self.sessions.get(key)
        if not sess or not sess.transcript_path:
            await self.hub.send(NODE_MESSAGES, {"session_key": key, "messages": [], "reset": True})
            return
        path = Path(sess.transcript_path)
        tail = TranscriptTail(path)
        initial = await asyncio.to_thread(read_last, path, self.s.tail_lines)
        tail.offset = path.stat().st_size if path.exists() else 0
        self.tails[key] = tail
        await self.hub.send(
            NODE_MESSAGES,
            {"session_key": key, "messages": [m.model_dump() for m in initial], "reset": True},
        )

    async def send_prompt(self, key: str, text: str, request_id: str) -> None:
        sess = self.sessions.get(key)
        result: dict = {"session_key": key, "request_id": request_id, "ok": False}
        if not sess:
            result["error"] = "unknown session"
        elif sess.harness == "claude" and sess.extra.get("socket"):
            try:
                reply = await send_user_message(
                    Path(sess.extra["socket"]),
                    text,
                    sess.session_id,
                    sender=f"agentdash@{self.machine}",
                )
                result.update(ok=True, reply=reply)
            except SocketSendError as e:
                result["error"] = str(e)
        else:
            result["error"] = f"no send channel for {sess.harness}"
        await self.hub.send(NODE_EVENT, {"kind": "prompt.result", **result})

    # loops ------------------------------------------------------------
    async def roster_loop(self) -> None:
        while True:
            try:
                sessions = await self.claude.collect()
                self.sessions = {s.key: s for s in sessions}
                await self.hub.send(NODE_SESSIONS, {"sessions": [s.model_dump() for s in sessions]})
            except Exception:  # noqa: BLE001
                log.exception("roster collection failed")
            try:
                await asyncio.wait_for(self._refresh.wait(), self.s.roster_interval)
            except TimeoutError:
                pass
            self._refresh.clear()

    async def tail_loop(self) -> None:
        while True:
            for key, tail in list(self.tails.items()):
                try:
                    new = await asyncio.to_thread(tail.read_new)
                except OSError as e:
                    log.warning("tail %s: %s", key, e)
                    continue
                if new:
                    await self.hub.send(
                        NODE_MESSAGES,
                        {"session_key": key, "messages": [m.model_dump() for m in new]},
                    )
            await asyncio.sleep(1.0)

    async def registry_watch(self) -> None:
        """Refresh the roster promptly when Claude's per-pid registry changes."""
        try:
            from watchfiles import awatch
        except ImportError:
            return
        dirs = [d / "sessions" for d in self.s.claude_config_dirs if (d / "sessions").exists()]
        if not dirs:
            return
        async for _changes in awatch(*dirs, debounce=500):
            self._refresh.set()

    async def run(self) -> None:
        hello = {
            "machine": {
                "id": self.machine,
                "hostname": platform.node(),
                "os": f"{platform.system()} {platform.release()}",
                "harnesses": ["claude"],
                "node_version": __version__,
            }
        }
        await asyncio.gather(
            self.hub.run(hello), self.roster_loop(), self.tail_loop(), self.registry_watch()
        )


async def run_node(settings: Settings) -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    await Node(settings).run()
