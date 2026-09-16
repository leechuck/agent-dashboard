"""Node main loop: run collectors, tail subscribed transcripts, talk to the hub."""

from __future__ import annotations

import asyncio
import logging
import platform
from pathlib import Path
from typing import Any

from .. import __version__
from ..config import Settings
from ..models import (
    HUB_ARM,
    HUB_DECISION_ANSWER,
    HUB_PING,
    HUB_SEND_PROMPT,
    HUB_SESSION_ACTION,
    HUB_SESSION_START,
    HUB_SUBSCRIBE,
    HUB_UNSUBSCRIBE,
    NODE_DECISION_CREATED,
    NODE_DECISION_RESOLVED,
    NODE_EVENT,
    NODE_MESSAGES,
    NODE_PONG,
    NODE_SESSIONS,
    NODE_USAGE,
    Decision,
    DecisionStatus,
    Frame,
    Harness,
    Session,
    SessionStatus,
    now_ms,
)
from .adapters.claude_cli import job_action, kill_process, start_background
from .adapters.claude_socket import SocketSendError, send_user_message
from .adapters.claude_transcript import TranscriptTail, read_last
from .collectors.claude import ClaudeCollector
from .collectors.usage import UsageCollector
from .decisions import DecisionManager
from .hookserver import serve_hooks
from .hubclient import HubClient

log = logging.getLogger(__name__)

NOT_ANSWERABLE = {"AskUserQuestion"}


class Node:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.machine = settings.machine_id
        self.claude = ClaudeCollector(self.machine, settings.claude_config_dirs)
        self.usage = UsageCollector(self.machine, settings.claude_config_dirs, settings.state_dir)
        self.sessions: dict[str, Session] = {}
        self.tails: dict[str, TranscriptTail] = {}
        self.hub = HubClient(settings.hub_url, settings.node_token, self.on_hub_frame)
        self.decisions = DecisionManager()
        self.armed = False
        self.armed_until = 0
        self.bridge: dict[str, str] = {}  # claude session_id -> remote-control URL
        self.status_hint: dict[str, tuple[SessionStatus, str, int]] = {}  # key -> (status, why, ts)
        self._refresh = asyncio.Event()

    # arming -----------------------------------------------------------
    @property
    def armed_now(self) -> bool:
        if (self.s.state_dir / "away").exists():
            return True
        return self.armed and (self.armed_until == 0 or now_ms() < self.armed_until)

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
        elif frame.type == HUB_SESSION_ACTION:
            await self.session_action(p)
        elif frame.type == HUB_SESSION_START:
            await self.session_start(p)
        elif frame.type == HUB_ARM:
            self.armed = bool(p.get("armed"))
            self.armed_until = int(p.get("armed_until") or 0)
            log.info("armed=%s until=%s", self.armed, self.armed_until)
        elif frame.type == HUB_DECISION_ANSWER:
            ok = self.decisions.answer(
                p.get("decision_id", ""),
                p.get("behavior", "deny"),
                p.get("reason", ""),
                bool(p.get("remember")),
            )
            if not ok:
                log.info("answer for unknown/finished decision %s", p.get("decision_id"))
        elif frame.type == "hello.ok":
            self.armed = bool(p.get("armed"))
            self.armed_until = int(p.get("armed_until") or 0)
            # re-announce anything still pending after a reconnect
            for pend in self.decisions.pending.values():
                await self.hub.send(NODE_DECISION_CREATED, pend.decision.model_dump())
            self._refresh.set()
        else:
            log.debug("unhandled hub frame %s", frame.type)

    # hooks -> node ----------------------------------------------------
    def _session_key(self, payload: dict[str, Any]) -> str:
        return Session.make_key(self.machine, Harness.claude, payload.get("session_id", ""))

    def _note_env(self, payload: dict[str, Any]) -> None:
        env = payload.get("_env") or {}
        sid = payload.get("session_id", "")
        bridge = env.get("CLAUDE_CODE_BRIDGE_SESSION_ID", "")
        if sid and bridge:
            self.bridge[sid] = f"https://claude.ai/code/{bridge}"

    async def on_claude_event(self, payload: dict[str, Any]) -> None:
        self._note_env(payload)
        ev = payload.get("hook_event_name", "")
        key = self._session_key(payload)
        if ev == "UserPromptSubmit":
            self.status_hint[key] = (SessionStatus.busy, "", now_ms())
        elif ev == "Stop":
            self.status_hint[key] = (SessionStatus.idle, "", now_ms())
        elif ev == "Notification":
            ntype = payload.get("notification_type", "")
            if ntype in ("permission_prompt", "agent_needs_input", "elicitation_dialog"):
                self.status_hint[key] = (SessionStatus.waiting, ntype.replace("_", " "), now_ms())
            elif ntype == "idle_prompt":
                self.status_hint[key] = (SessionStatus.idle, "", now_ms())
            await self.hub.send(
                NODE_EVENT,
                {
                    "kind": f"claude.{ntype or 'notification'}",
                    "session_key": key,
                    "message": payload.get("message", ""),
                    "armed": self.armed_now,
                },
            )
        elif ev == "SessionEnd":
            self.status_hint.pop(key, None)
            self.decisions.forget_session(key)
        self._refresh.set()

    async def on_claude_permission(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._note_env(payload)
        key = self._session_key(payload)
        tool = payload.get("tool_name", "")
        sess = self.sessions.get(key)
        if not self.armed_now or not self.hub.connected.is_set():
            return {}
        if self.decisions.is_remembered(key, tool):
            return _decision_reply("allow", "remembered for this session")
        d = Decision(
            id=self.decisions.new_id(),
            machine=self.machine,
            session_key=key,
            harness=Harness.claude,
            kind="question" if tool in NOT_ANSWERABLE else "permission",
            tool_name=tool,
            tool_input=payload.get("tool_input")
            if isinstance(payload.get("tool_input"), dict)
            else None,
            question=str(payload.get("ask_user_question") or ""),
            reason=str(payload.get("permission_decision_reason") or ""),
            cwd=payload.get("cwd", ""),
            session_name=sess.name if sess else "",
            native_url=self.bridge.get(payload.get("session_id", ""), ""),
            expires_at=now_ms() + int(self.s.decision_timeout * 1000),
        )
        if d.kind == "question":
            # cannot be answered through the hook; surface it and let the dialog show
            d.status = DecisionStatus.answered
            await self.hub.send(NODE_DECISION_CREATED, d.model_dump())
            self.status_hint[key] = (SessionStatus.waiting, "your answer", now_ms())
            self._refresh.set()
            return {}
        pend = self.decisions.add(d)
        self.status_hint[key] = (SessionStatus.waiting, f"permission: {tool}", now_ms())
        self._refresh.set()
        await self.hub.send(NODE_DECISION_CREATED, d.model_dump())
        try:
            result = await asyncio.wait_for(pend.future, self.s.decision_timeout)
        except TimeoutError:
            d.status = DecisionStatus.expired
            d.answered_at = now_ms()
            result = {}
        finally:
            self.decisions.finish(d.id)
            self.status_hint.pop(key, None)
            self._refresh.set()
            await self.hub.send(NODE_DECISION_RESOLVED, d.model_dump())
        if not result.get("behavior"):
            return {}
        return _decision_reply(result["behavior"], result.get("reason", ""))

    # commands ---------------------------------------------------------
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
        result: dict[str, Any] = {"session_key": key, "request_id": request_id, "ok": False}
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

    async def session_action(self, p: dict[str, Any]) -> None:
        key, action, rid = p.get("session_key", ""), p.get("action", ""), p.get("request_id", "")
        sess = self.sessions.get(key)
        result: dict[str, Any] = {"session_key": key, "request_id": rid, "ok": False}
        if not sess:
            result["error"] = "unknown session"
        elif sess.harness != "claude":
            result["error"] = f"no actions for {sess.harness} yet"
        elif action in ("stop", "rm", "respawn", "logs"):
            job_id = sess.extra.get("job_id")
            if job_id:
                result.update(await job_action(action, job_id, sess.extra.get("config_dir")))
            elif action == "stop" and sess.pid:
                result.update(kill_process(sess.pid))
            else:
                result["error"] = "not a background session"
        elif action in ("terminate", "kill"):
            if sess.pid:
                result.update(kill_process(sess.pid, hard=action == "kill"))
            else:
                result["error"] = "no live process"
        else:
            result["error"] = f"unknown action {action}"
        self._refresh.set()
        await self.hub.send(NODE_EVENT, {"kind": "action.result", "action": action, **result})

    async def session_start(self, p: dict[str, Any]) -> None:
        rid = p.get("request_id", "")
        config_dir = None
        for d in self.s.claude_config_dirs:
            if p.get("provider", "anthropic") == (d.name.removeprefix(".claude-") or "anthropic"):
                config_dir = str(d)
        result = await start_background(
            p.get("cwd", ""),
            p.get("prompt", ""),
            name=p.get("name", ""),
            resume=p.get("resume", ""),
            permission_mode=p.get("permission_mode", ""),
            config_dir=config_dir,
        )
        self._refresh.set()
        await self.hub.send(NODE_EVENT, {"kind": "start.result", "request_id": rid, **result})

    # loops ------------------------------------------------------------
    def _apply_hints(self, sessions: list[Session]) -> None:
        cutoff = now_ms() - 6 * 3600 * 1000
        for s in sessions:
            s.native_url = self.bridge.get(s.session_id, s.native_url)
            hint = self.status_hint.get(s.key)
            if not hint or hint[2] < cutoff:
                continue
            status, why, ts = hint
            if status == SessionStatus.waiting:
                s.status, s.waiting_for = status, why
                s.updated_at = max(s.updated_at, ts)
            elif s.status == SessionStatus.waiting and s.waiting_for == "":
                s.status = status

    async def roster_loop(self) -> None:
        while True:
            try:
                sessions = await self.claude.collect()
                self._apply_hints(sessions)
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

    async def usage_loop(self) -> None:
        import random

        await asyncio.sleep(5)
        while True:
            try:
                windows = await self.usage.collect()
                if windows:
                    await self.hub.send(NODE_USAGE, {"windows": [w.model_dump() for w in windows]})
            except Exception:  # noqa: BLE001
                log.exception("usage collection failed")
            await asyncio.sleep(self.s.usage_interval + random.uniform(0, 60))

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
            self.hub.run(hello),
            self.roster_loop(),
            self.tail_loop(),
            self.registry_watch(),
            self.usage_loop(),
            serve_hooks(self, self.s.node_host, self.s.node_port),
        )


def _decision_reply(behavior: str, reason: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {"behavior": behavior},
        }
    }
    if reason:
        out["hookSpecificOutput"]["decisionReason"] = reason
    return out


def _extend_path() -> None:
    """Make user-installed agent binaries visible even under systemd's minimal PATH."""
    import os

    home = Path.home()
    extra = [home / ".local" / "bin", home / ".opencode" / "bin", home / ".cargo" / "bin"]
    nvm = home / ".nvm" / "versions" / "node"
    if nvm.exists():
        extra += sorted((d / "bin" for d in nvm.iterdir()), reverse=True)
    current = os.environ.get("PATH", "")
    parts = [str(p) for p in extra if p.exists() and str(p) not in current]
    if parts:
        os.environ["PATH"] = ":".join(parts + [current])


async def run_node(settings: Settings) -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    _extend_path()
    await Node(settings).run()
