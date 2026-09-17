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
from . import briefing
from .adapters import codex_rollout, hermes_state, opencode_store, pi_session
from .adapters.claude_cli import job_action, kill_process, start_background
from .adapters.claude_socket import SocketSendError, send_user_message
from .adapters.claude_transcript import TranscriptTail, read_last
from .adapters.claude_transcript import iter_messages as claude_iter
from .adapters.tmux_keys import TmuxSendError, type_prompt
from .collectors.claude import ClaudeCollector
from .collectors.codex import CodexCollector
from .collectors.hermes import HermesCollector
from .collectors.opencode import OpencodeCollector
from .collectors.pi import PiCollector
from .collectors.tmux import TmuxCollector
from .collectors.usage import UsageCollector
from .decisions import DecisionManager
from .hookserver import serve_hooks
from .hubclient import HubClient
from .lineage import link_parents
from .pa import PersonalAssistant
from .terminal import TerminalSession

log = logging.getLogger(__name__)

NOT_ANSWERABLE = {"AskUserQuestion"}


class Node:
    def __init__(self, settings: Settings) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()
        self.pa = PersonalAssistant(settings)
        self.parents: dict[str, str] = {}  # session key -> key of the session that spawned it
        self.s = settings
        self.machine = settings.machine_id
        self.claude = ClaudeCollector(self.machine, settings.claude_config_dirs, settings.state_dir)
        self.codex = CodexCollector(self.machine)
        self.pi = PiCollector(self.machine)
        self.opencode = OpencodeCollector(self.machine)
        self.hermes = HermesCollector(self.machine)
        self.tmux = TmuxCollector(self.machine)
        self.usage = UsageCollector(self.machine, settings.claude_config_dirs, settings.state_dir)
        self.pi_inbox: dict[str, asyncio.Queue[str]] = {}  # pi session_id -> prompts to deliver
        self.terminals: dict[str, TerminalSession] = {}
        self.opencode_seen: dict[str, set[str]] = {}
        self.hermes_last: dict[str, int] = {}  # session key -> last message id sent
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
            self.hermes_last.pop(p.get("session_key", ""), None)
            self.opencode_seen.pop(p.get("session_key", ""), None)
        elif frame.type == HUB_SEND_PROMPT:
            await self.send_prompt(
                p.get("session_key", ""), p.get("text", ""), p.get("request_id", "")
            )
        elif frame.type == HUB_SESSION_ACTION:
            await self.session_action(p)
        elif frame.type == HUB_SESSION_START:
            await self.session_start(p)
        elif frame.type == "terminal.open":
            await self.terminal_open(p)
        elif frame.type == "cockpit.brief":
            self._spawn(self.cockpit_brief(p))
        elif frame.type == "pa.request":
            self._spawn(self.pa_request(p))
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

    async def on_claude_permission(
        self, payload: dict[str, Any], harness: Harness = Harness.claude
    ) -> dict[str, Any]:
        self._note_env(payload)
        key = (
            self._session_key(payload)
            if harness == Harness.claude
            else Session.make_key(self.machine, harness, payload.get("session_id", ""))
        )
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
            harness=harness,
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
        if sess.harness == "hermes":
            msgs = await asyncio.to_thread(
                hermes_state.messages,
                Path(sess.transcript_path),
                sess.session_id,
                0,
                self.s.tail_lines,
            )
            self.hermes_last[key] = max((int(m.id.split(":")[0]) for m in msgs), default=0)
            await self.hub.send(
                NODE_MESSAGES,
                {"session_key": key, "messages": [m.model_dump() for m in msgs], "reset": True},
            )
            return
        if sess.harness == "opencode":
            msgs = await asyncio.to_thread(
                opencode_store.messages,
                Path(sess.transcript_path),
                sess.session_id,
                self.s.tail_lines,
            )
            self.opencode_seen[key] = {m.id for m in msgs}
            await self.hub.send(
                NODE_MESSAGES,
                {"session_key": key, "messages": [m.model_dump() for m in msgs], "reset": True},
            )
            return
        parser = {
            "codex": codex_rollout.iter_messages,
            "pi": pi_session.iter_messages,
        }.get(sess.harness, claude_iter)
        path = Path(sess.transcript_path)
        tail = TranscriptTail(path, parser)
        initial = await asyncio.to_thread(read_last, path, self.s.tail_lines, parser)
        tail.offset = path.stat().st_size if path.exists() else 0
        self.tails[key] = tail
        await self.hub.send(
            NODE_MESSAGES,
            {"session_key": key, "messages": [m.model_dump() for m in initial], "reset": True},
        )

    @staticmethod
    def _has_inbox(sess: Session | None) -> bool:
        if sess is None:
            return False
        return bool(
            (sess.harness == "claude" and sess.extra.get("socket"))
            or (sess.harness == "pi" and sess.extra.get("inbox"))
        )

    async def send_prompt(self, key: str, text: str, request_id: str) -> None:
        sess = self.sessions.get(key)
        result: dict[str, Any] = {"session_key": key, "request_id": request_id, "ok": False}
        pane = (sess.extra.get("tmux") or {}) if sess else {}
        # a slash command only works when typed; the inbox socket delivers it as plain text
        typed = bool(pane) and (text.startswith("/") or not self._has_inbox(sess))
        if not sess:
            result["error"] = "unknown session"
        elif typed:
            try:
                await type_prompt(pane.get("socket", ""), pane.get("target", ""), text)
                result.update(ok=True, via="tmux")
            except TmuxSendError as e:
                result["error"] = str(e)
        elif text.startswith("/") and sess.harness == "claude":
            result["error"] = "commands like /compact must be typed; this session is not in tmux"
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
        elif sess.harness == "pi" and sess.extra.get("inbox"):
            self.pi_inbox.setdefault(sess.session_id, asyncio.Queue()).put_nowait(text)
            result["ok"] = True
        else:
            result["error"] = f"no send channel for {sess.harness}"
        await self.hub.send(NODE_EVENT, {"kind": "prompt.result", **result})

    async def terminal_open(self, p: dict[str, Any]) -> None:
        term_id = p.get("term_id", "")
        base = self.s.hub_url.rsplit("/nodes", 1)[0]
        t = TerminalSession(
            term_id,
            p.get("socket", ""),
            p.get("target", ""),
            f"{base}/nodes/terminal/{term_id}",
            self.s.node_token,
        )
        self.terminals[term_id] = t

        async def run() -> None:
            try:
                await t.run(int(p.get("rows", 30)), int(p.get("cols", 100)))
            finally:
                self.terminals.pop(term_id, None)

        asyncio.create_task(run())

    # pi extension -----------------------------------------------------
    def pi_report(self, payload: dict[str, Any]) -> None:
        sid = payload.get("session_id", "")
        if not sid:
            return
        info = self.pi.live.setdefault(sid, {})
        info.update({k: v for k, v in payload.items() if k != "session_id"})
        info["seen"] = now_ms()
        if payload.get("event") == "session_shutdown":
            self.pi.live.pop(sid, None)
        self._refresh.set()

    async def pi_next_prompt(self, sid: str, timeout: float) -> str | None:
        q = self.pi_inbox.setdefault(sid, asyncio.Queue())
        try:
            return await asyncio.wait_for(q.get(), timeout)
        except TimeoutError:
            return None

    async def on_pi_toolcall(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Gate a pi tool call while armed; mirrors the Claude permission flow."""
        key = Session.make_key(self.machine, Harness.pi, payload.get("session_id", ""))
        tool = payload.get("tool_name", "")
        if not self.armed_now or not self.hub.connected.is_set():
            return {}
        if self.decisions.is_remembered(key, tool):
            return {"behavior": "allow"}
        sess = self.sessions.get(key)
        d = Decision(
            id=self.decisions.new_id(),
            machine=self.machine,
            session_key=key,
            harness=Harness.pi,
            kind="permission",
            tool_name=tool,
            tool_input=payload.get("tool_input")
            if isinstance(payload.get("tool_input"), dict)
            else None,
            cwd=payload.get("cwd", ""),
            session_name=sess.name if sess else "",
            expires_at=now_ms() + int(self.s.decision_timeout * 1000),
        )
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
        return {"behavior": result.get("behavior", ""), "reason": result.get("reason", "")}

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

    def _spawn(self, coro: Any) -> None:
        """Run slow work off the frame loop; keep a reference so it is not collected."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def pa_request(self, p: dict[str, Any]) -> None:
        try:
            result = await self.pa.handle(p, self.sessions)
        except Exception as e:  # noqa: BLE001
            log.exception("pa request failed")
            result = {"ok": False, "error": f"{e.__class__.__name__}: {e}"[:300]}
        if p.get("op") == "run":
            self._refresh.set()
        await self.hub.send(
            NODE_EVENT, {"kind": "pa.result", "request_id": p.get("request_id", ""), **result}
        )

    async def cockpit_brief(self, p: dict[str, Any]) -> None:
        result = await briefing.brief(
            self.s, p.get("system", ""), p.get("digest") or {}, p.get("agent")
        )
        await self.hub.send(
            NODE_EVENT, {"kind": "brief.result", "request_id": p.get("request_id", ""), **result}
        )

    async def session_start(self, p: dict[str, Any]) -> None:
        rid = p.get("request_id", "")
        config_dir = None
        for d in self.s.claude_config_dirs:
            if p.get("config_dir"):
                if d.name == p["config_dir"]:  # a named login; only known dirs are accepted
                    config_dir = str(d)
            elif p.get("provider", "anthropic") == (
                d.name.removeprefix(".claude-") if d.name != ".claude" else "anthropic"
            ):
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
                for coll in (self.codex, self.pi, self.opencode, self.hermes):
                    try:
                        sessions += await asyncio.to_thread(coll.collect)
                    except Exception:  # noqa: BLE001
                        log.exception("%s collector failed", type(coll).__name__)
                try:
                    sessions = await asyncio.to_thread(self.tmux.annotate, sessions)
                except Exception:  # noqa: BLE001
                    log.exception("tmux collector failed")
                self._apply_hints(sessions)
                await asyncio.to_thread(link_parents, sessions, self.parents)
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
        tick = 0
        while True:
            tick += 1
            if tick % 5 == 0:
                for key, last_id in list(self.hermes_last.items()):
                    sess = self.sessions.get(key)
                    if not sess:
                        continue
                    new = await asyncio.to_thread(
                        hermes_state.messages,
                        Path(sess.transcript_path),
                        sess.session_id,
                        last_id,
                        200,
                    )
                    if new:
                        self.hermes_last[key] = max(int(m.id.split(":")[0]) for m in new)
                        await self.hub.send(
                            NODE_MESSAGES,
                            {"session_key": key, "messages": [m.model_dump() for m in new]},
                        )
                for key, seen in list(self.opencode_seen.items()):
                    sess = self.sessions.get(key)
                    if not sess:
                        continue
                    msgs = await asyncio.to_thread(
                        opencode_store.messages, Path(sess.transcript_path), sess.session_id, 400
                    )
                    new = [m for m in msgs if m.id not in seen]
                    if new:
                        seen.update(m.id for m in new)
                        await self.hub.send(
                            NODE_MESSAGES,
                            {"session_key": key, "messages": [m.model_dump() for m in new]},
                        )
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
                "harnesses": ["claude", "codex", "pi", "opencode", "hermes", "tmux"],
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
