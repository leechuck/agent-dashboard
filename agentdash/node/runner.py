"""Node main loop: run collectors, tail subscribed transcripts, talk to the hub."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import secrets
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

import httpx

from .. import __version__
from ..config import Settings, discover_claude_dirs
from ..install.account import install_account
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
    Message,
    Session,
    SessionStatus,
    now_ms,
)
from . import briefing, commands, login_screen, past, transfer_http, workspace_transfer
from .adapters import codex_rollout, hermes_state, opencode_store, pi_session
from .adapters.claude_cli import job_action, kill_process
from .adapters.claude_socket import SocketSendError, send_user_message
from .adapters.claude_transcript import TranscriptTail, read_last
from .adapters.claude_transcript import iter_messages as claude_iter
from .adapters.tmux_keys import TmuxSendError, capture, send_keys, socket_path, type_prompt
from .catalog import Catalog
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
from .launcher import (
    Endpoint,
    LaunchError,
    LaunchSpec,
    build,
    claude_config_dir,
    handover_prompt,
    launch,
)
from .lineage import link_parents
from .pa import PersonalAssistant
from .past import TransferError
from .terminal import TerminalSession

log = logging.getLogger(__name__)

NOT_ANSWERABLE = {"AskUserQuestion"}


class Node:
    def __init__(self, settings: Settings) -> None:
        self._tasks: set[asyncio.Task[Any]] = set()
        self.pa = PersonalAssistant(settings)
        self.catalog = Catalog(settings)
        self.pending_subs: set[str] = set()
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
        self._dead_told: set[str] = set()  # Claude logins already reported as expired
        self._credentials_changed = asyncio.Event()

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
            self._spawn(self.session_start(p))
        elif frame.type == "commands.list":
            self._spawn(self.commands_list(p))
        elif frame.type == "catalog.get":
            self._spawn(self.catalog_get(p))
        elif frame.type == "login.open":
            self._spawn(self.login_open(p))
        elif frame.type == "session.switch":
            self._spawn(self.session_switch(p))
        elif frame.type == "past.list":
            self._spawn(self.past_list(p))
        elif frame.type == "session.export":
            self._spawn(self.session_export(p))
        elif frame.type == "session.prepare":
            self._spawn(self.session_import({**p, "prepare_only": True}))
        elif frame.type == "session.import":
            self._spawn(self.session_import(p))
        elif frame.type == "pane.screen":
            self._spawn(self.pane_screen(p))
        elif frame.type == "pane.keys":
            self._spawn(self.pane_keys(p))
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
        if not sess:
            # not running: its transcript may still be on disk (a past session being read,
            # or a live one asked for right after a reconnect, before the first roster)
            sess = await asyncio.to_thread(self.find_past, key)
        if not sess:
            # answer when the session is known, never with an empty transcript that would
            # be cached
            self.pending_subs.add(key)
            return
        self.pending_subs.discard(key)
        if not sess.transcript_path:
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

    async def _show_queued(self, key: str, text: str, request_id: str) -> None:
        """The agent takes a message between turns; a dialog or a long turn can hold it for
        a while. Show it now so the board does not look deaf; the transcript's own record
        replaces it once the agent has read it."""
        queued = Message(
            id=f"pending:{request_id or secrets.token_hex(8)}",
            ts=now_ms(),
            role="user",
            kind="text",
            text=text,
            sender="dashboard",
            pending=True,
        )
        await self.hub.send(NODE_MESSAGES, {"session_key": key, "messages": [queued.model_dump()]})

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
        # Typed into its terminal whenever it has one: that is the owner speaking, and the
        # agent answers the owner. The inbox socket wraps a message as one from another
        # Claude session ("not typed by your user"), the agent then reports back to a peer
        # (any other session on the host) and that session starts working on it. Slash
        # commands only work typed anyway.
        typed = bool(pane)
        if not sess:
            result["error"] = "unknown session"
        elif typed:
            try:
                await type_prompt(pane.get("socket", ""), pane.get("target", ""), text)
                result.update(ok=True, via="tmux")
                # a busy agent, or one at a dialog, takes the text later: show it queued
                await self._show_queued(key, text, request_id)
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
                await self._show_queued(key, text, request_id)
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
        sock = socket_path(str(p.get("socket", "")))
        t = TerminalSession(
            term_id,
            sock,
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
        elif sess.harness != "claude" and action not in ("terminate", "kill"):
            result["error"] = f"{action} is only available for Claude background jobs"
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
            self.s, p.get("system", ""), p.get("digest") or {}, p.get("agent"), self._endpoints(p)
        )
        await self.hub.send(
            NODE_EVENT, {"kind": "brief.result", "request_id": p.get("request_id", ""), **result}
        )

    def _endpoints(self, p: dict[str, Any]) -> list[Endpoint]:
        out = []
        for raw in p.get("endpoints") or []:
            try:
                out.append(Endpoint.parse(raw))
            except (LaunchError, TypeError):
                continue
        return out

    async def _reply(self, kind: str, p: dict[str, Any], result: dict[str, Any]) -> None:
        await self.hub.send(
            NODE_EVENT, {"kind": kind, "request_id": p.get("request_id", ""), **result}
        )

    async def session_start(self, p: dict[str, Any]) -> None:
        try:
            spec = LaunchSpec.parse(p)
            # new config dirs (a login added a minute ago) count without a node restart
            self.s.claude_config_dirs = discover_claude_dirs()
            result = await launch(spec, self.s, self._endpoints(p))
        except LaunchError as e:
            result = {"ok": False, "error": str(e)}
        self._refresh.set()
        await self._reply("start.result", p, result)

    async def commands_list(self, p: dict[str, Any]) -> None:
        sess = self.sessions.get(p.get("session_key", ""))
        harness = str(p.get("harness") or (sess.harness if sess else "claude"))
        cwd = str(p.get("cwd") or (sess.cwd if sess else ""))
        config_dir = str((sess.extra.get("config_dir") if sess else "") or "")
        try:
            # the catalog already knows this machine's models, and keeps them for a while
            cat = await self.catalog.get(self._endpoints(p))
            models = (cat.get("models") or {}).get(harness) or []
            found = await asyncio.to_thread(commands.for_session, harness, cwd, config_dir, models)
            result = {"ok": True, "harness": harness, "commands": found}
        except OSError as e:
            result = {"ok": False, "error": str(e)}
        await self._reply("commands.result", p, result)

    async def catalog_get(self, p: dict[str, Any]) -> None:
        self.s.claude_config_dirs = discover_claude_dirs()
        self.claude.config_dirs = [d for d in self.s.claude_config_dirs if d.exists()]
        result = await self.catalog.get(
            self._endpoints(p), fresh=bool(p.get("fresh")), quick=bool(p.get("quick"))
        )
        for login in result.get("logins") or []:
            if str(Path.home() / login.get("dir", "")) in self.usage.dead_logins:
                login["logged_in"], login["expired"] = False, True  # its token no longer works
        await self._reply("catalog.result", p, result)

    async def login_open(self, p: dict[str, Any]) -> None:
        """Create (if needed) a Claude login directory and open Claude on it in tmux, so the
        owner can run /login there from the dashboard's terminal. The name `default` is the
        main ~/.claude login (for when it expired)."""
        try:
            name = str(p.get("name") or "")
            main = name in ("", "default", ".claude")
            done = [] if main else await asyncio.to_thread(install_account, name)
            self.s.claude_config_dirs = discover_claude_dirs()
            self.claude.config_dirs = [d for d in self.s.claude_config_dirs if d.exists()]
            spec = LaunchSpec(
                harness="claude",
                cwd=str(Path.home()),
                backend="default" if main else "login",
                login="" if main else f".claude-{name}",
                name=f"login-{name or 'default'}",
            )
            config = Path.home() / (".claude" if main else f".claude-{name}")
            fresh = not _credentials_alive(config / ".credentials.json")
            existing = await _tmux_session_like(f"claude-login-{name or 'default'}-")
            if existing:
                # clicking "Log in" again returns to the login already open, not a new one
                done.append(f"reusing {existing}")
                result = {
                    "ok": True,
                    "tmux": {"name": existing, "socket": "default", "target": f"{existing}:0.0"},
                    "attach": f"tmux attach -t {existing}",
                    "fresh": fresh,
                    "notes": done,
                }
                self._refresh.set()
                await self._reply("login.result", p, result)
                return
            result = await launch(spec, self.s, [])
            if result.get("ok") and result.get("tmux") and not fresh:
                # an empty slot asks by itself; a slot that is already logged in needs the
                # command, and typing into the first screen of an empty one could answer it
                await asyncio.sleep(7)
                pane = result["tmux"]
                with suppress(TmuxSendError):
                    await type_prompt(pane["socket"], pane["target"], "/login")
            result["fresh"] = fresh
            result["notes"] = done
        except (LaunchError, ValueError) as e:
            result = {"ok": False, "error": str(e)}
        self._refresh.set()
        await self._reply("login.result", p, result)

    async def pane_screen(self, p: dict[str, Any]) -> None:
        """What a tmux pane shows, and what a login screen in it asks for."""
        try:
            text = await capture(str(p.get("socket") or ""), str(p.get("target") or ""))
            result = {"ok": True, "login": login_screen.read(text), "screen": text}
            if result["login"]["stage"] == "done":
                self._credentials_changed.set()  # the plan's numbers can be fetched now
        except (TmuxSendError, OSError, TimeoutError) as e:
            result = {"ok": False, "error": str(e)[:200]}
        await self._reply("pane.screen", p, result)

    async def pane_keys(self, p: dict[str, Any]) -> None:
        try:
            await send_keys(
                str(p.get("socket") or ""),
                str(p.get("target") or ""),
                [str(k) for k in p.get("keys") or []],
                str(p.get("text") or ""),
            )
            result: dict[str, Any] = {"ok": True}
        except TmuxSendError as e:
            result = {"ok": False, "error": str(e)}
        await self._reply("pane.keys", p, result)

    async def session_switch(self, p: dict[str, Any]) -> None:
        try:
            result = await self._switch(p)
        except LaunchError as e:
            result = {"ok": False, "error": str(e)}
        self._refresh.set()
        await self._reply("switch.result", p, result)

    async def _stop(self, sess: Session) -> None:
        """End a session's process and wait until it is gone: two agents must never write
        to one transcript."""
        job = sess.extra.get("job_id")
        if job and sess.harness == "claude":
            await job_action("stop", job, sess.extra.get("config_dir"))
        if sess.pid:
            kill_process(sess.pid)
            for _ in range(60):
                if not Path(f"/proc/{sess.pid}").exists():
                    return
                await asyncio.sleep(0.25)
            kill_process(sess.pid, hard=True)
            await asyncio.sleep(0.5)

    def find_past(self, key: str) -> Session | None:
        """A session that is not running, from what its harness keeps on disk."""
        try:
            machine, harness, sid = key.split(":", 2)
        except ValueError:
            return None
        if machine != self.machine or not sid:
            return None
        if harness == "claude":
            return past.find_claude(self.machine, self.claude.config_dirs, sid)
        if harness == "codex":
            t = self.codex._thread(sid)
            return past.codex_session(self.machine, t, self.codex.home) if t else None
        if harness == "pi":
            return past.find_pi(self.machine, self.pi.dir, sid)
        return None

    async def locate(self, key: str) -> Session | None:
        """Running, or on disk."""
        return self.sessions.get(key) or await asyncio.to_thread(self.find_past, key)

    async def past_list(self, p: dict[str, Any]) -> None:
        """Sessions this machine could resume, newest first."""
        harness = str(p.get("harness") or "")
        limit = max(1, min(int(p.get("limit") or 120), 100_001))
        q = str(p.get("q") or "")
        found: list[Session] = []
        try:
            if harness in ("", "claude"):
                found += await asyncio.to_thread(
                    past.list_claude, self.machine, self.claude.config_dirs, None
                )
            if harness in ("", "codex"):
                rows = await asyncio.to_thread(self.codex._query, "1=1", (), None)
                found += [past.codex_session(self.machine, t, self.codex.home) for t in rows]
            if harness in ("", "pi"):
                found += await asyncio.to_thread(past.list_pi, self.machine, self.pi.dir, None)
            live = {k for k, s in self.sessions.items() if s.status in ("busy", "idle", "waiting")}
            found = await asyncio.to_thread(
                past.search_sessions, found, q, live, p.get("titles") or {},
                bool(p.get("content")),
            )
            result: dict[str, Any] = {
                "ok": True,
                "sessions": [s.model_dump() for s in found[:limit]],
            }
        except Exception as e:  # noqa: BLE001
            log.exception("listing past sessions failed")
            result = {"ok": False, "error": f"{e.__class__.__name__}: {e}"[:300]}
        await self._reply("past.result", p, result)

    def _hub_http(self) -> str:
        base = self.s.hub_url.rsplit("/nodes", 1)[0]
        return "http" + base[2:] if base.startswith("ws") else base

    async def _move_progress(self, p, stage, completed=None, total=None):
        if p.get("move_key"):
            await self.hub.send(NODE_EVENT, {
                "kind": "move.progress", "key": p["move_key"],
                "stage": stage, "phase": p.get("move_phase", "Moving"),
                "completed": completed, "total": total,
            })

    async def session_export(self, p: dict[str, Any]) -> None:
        """Pack a session's transcript and hand it to the hub for another machine."""
        bundle: Path | None = None
        stopped = False
        try:
            sess = await self.locate(p.get("session_key", ""))
            if not sess:
                raise TransferError("unknown session")
            if sess.harness == "tmux":
                sess = _pane_as_agent(sess)
            live = sess.status in ("busy", "idle", "waiting") and sess.pid
            if live and sess.status == "busy" and not p.get("force"):
                raise TransferError(
                    "the session is working; wait for it to finish its turn, or force"
                )
            if live and p.get("stop", True):
                await self._stop(sess)
                stopped = True
            await self._move_progress(p, "Packing environment")
            bundle, meta = await asyncio.to_thread(
                past.pack, sess, self.s.state_dir / "transfer", bool(p.get("environment"))
            )
            blob = secrets.token_hex(16)
            await transfer_http.upload(
                bundle, f"{self._hub_http()}/nodes/blob/{blob}", self.s.node_token,
                progress=lambda done, total: self._move_progress(p, "Uploading", done, total)
            )
            result = {"ok": True, "blob": blob, "stopped": stopped, **meta}
            result["extra"] = {
                k: sess.extra[k] for k in ("config_dir", "account", "first_user") if k in sess.extra
            }
        except (TransferError, LaunchError, httpx.HTTPError, OSError) as e:
            result = {"ok": False, "error": str(e)[:300]}
        except Exception as e:  # noqa: BLE001
            log.exception("export failed")
            result = {"ok": False, "error": f"{e.__class__.__name__}: {e}"[:300]}
        finally:
            if bundle:
                bundle.unlink(missing_ok=True)
        self._refresh.set()
        result["stopped"] = stopped
        await self._reply("export.result", p, result)

    async def session_import(self, p: dict[str, Any]) -> None:
        """Fetch a bundle from the hub, file it where the harness looks, and resume it."""
        bundle = self.s.state_dir / "transfer" / f"in-{secrets.token_hex(8)}.tgz"
        try:
            harness, sid = str(p.get("harness") or ""), str(p.get("session_id") or "")
            if harness not in past.RESUMABLE or not sid:
                raise TransferError(f"{harness or 'this'} sessions cannot be resumed here")
            self.s.claude_config_dirs = discover_claude_dirs()
            spec = LaunchSpec.parse({**p, "harness": harness, "resume": sid})
            spec.prompt = str(p.get("note") or "")
            import shutil

            _, launch_env = build(spec, self.s, self._endpoints(p))
            if not shutil.which("tmux"):
                raise TransferError("tmux is not installed on this machine")
            if not spec.cwd.strip() or not Path(spec.cwd).expanduser().is_absolute():
                raise TransferError("an absolute destination project directory is required")
            destination = Path(spec.cwd).expanduser()
            if not destination.exists() and not p.get("create_dir", True):
                raise TransferError(
                    f"no such destination directory: {destination}; "
                    "enable Create destination directory if missing"
                )
            if destination.exists() and not destination.is_dir():
                raise TransferError(f"destination is not a directory: {destination}")
            if harness == "claude":
                root = (
                    claude_config_dir(self.s, spec.login)
                    if spec.backend == "login" and spec.login
                    else Path.home() / ".claude"
                )
            elif harness == "codex":
                root = self.codex.home
            else:
                root = self.pi.dir
            login_root = root
            if harness == "claude" and spec.backend != "endpoint":
                await _validate_claude_login(root, launch_env)
            if p.get("environment") and harness in ("claude", "codex"):
                # Session-specific config keeps the host's other agents unaffected.
                import hashlib

                identity = sid + str(p.get("transfer_id") or "")
                suffix = hashlib.sha256(identity.encode()).hexdigest()[:16]
                root = Path.home() / f".{harness}-move-{suffix}"
            bundle.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if p.get("size"):
                await transfer_http.download(
                    bundle, f"{self._hub_http()}/nodes/blob/{p.get('blob', '')}",
                    self.s.node_token, int(p["size"]),
                    progress=lambda done, total: self._move_progress(p, "Downloading", done, total),
                )
            else:
                async with httpx.AsyncClient(timeout=600) as client:
                    async with client.stream(
                        "GET",
                        f"{self._hub_http()}/nodes/blob/{p.get('blob', '')}",
                        headers={"Authorization": f"Bearer {self.s.node_token}"},
                    ) as r:
                        if r.status_code >= 300:
                            raise TransferError(f"the hub has no such bundle ({r.status_code})")
                        with bundle.open("wb") as f:
                            async for chunk in r.aiter_bytes():
                                f.write(chunk)
            await self._move_progress(
                p, "Checking destination" if p.get("prepare_only") else "Restoring environment"
            )
            await asyncio.to_thread(
                past.unpack, bundle, harness, sid, spec.cwd, root, check_only=True,
            )
            has_environment = await asyncio.to_thread(
                workspace_transfer.restore, bundle, Path(spec.cwd).expanduser(), Path.home(), root,
                check_only=bool(p.get("prepare_only")),
            )
            if p.get("environment") and not has_environment:
                raise TransferError("source node did not include the environment; update that node")
            if not has_environment and not Path(spec.cwd).expanduser().is_dir():
                raise TransferError(f"no such directory: {spec.cwd}")
            if p.get("prepare_only"):
                await self._reply("import.result", p, {"ok": True, "prepared": True})
                return
            path = await asyncio.to_thread(past.unpack, bundle, harness, sid, spec.cwd, root)
            if harness in ("claude", "codex"):
                past.record_move(root, sid, spec.cwd)
            if harness == "claude" and root not in self.claude.config_dirs:
                self.claude.config_dirs.append(root)
                self.s.claude_config_dirs = list(self.claude.config_dirs)
            if harness == "pi":
                spec.resume = str(path)  # pi resumes by file
            if has_environment and harness in ("claude", "codex"):
                credential = ".credentials.json" if harness == "claude" else "auth.json"
                local_credential = login_root / credential
                if local_credential.exists() and not (root / credential).exists():
                    (root / credential).symlink_to(local_credential)
            if has_environment and harness == "claude":
                workspace_transfer.bootstrap_claude_login(login_root, root, Path.home())
            spec.prompt = workspace_transfer.relocation_message(
                str(p.get("source_machine") or "source host"), self.machine,
                str(p.get("source_cwd") or ""), spec.cwd, has_environment, spec.prompt,
            )
            spec.prompt += f"\nAgent configuration and session storage on this host: {root}\n"
            await self._move_progress(p, "Starting resumed agent")
            result = await launch(
                spec, self.s, self._endpoints(p),
                runtime_home=root if has_environment and harness in ("claude", "codex") else None,
            )
            result["session_key"] = Session.make_key(self.machine, harness, sid)
            result["transcript_path"] = str(path)
        except (TransferError, LaunchError, httpx.HTTPError, OSError, ValueError) as e:
            result = {"ok": False, "error": str(e)[:300]}
        except Exception as e:  # noqa: BLE001
            log.exception("import failed")
            result = {"ok": False, "error": f"{e.__class__.__name__}: {e}"[:300]}
        finally:
            with suppress(OSError):
                bundle.unlink(missing_ok=True)
        self._refresh.set()
        await self._reply("import.result", p, result)

    async def _switch(self, p: dict[str, Any]) -> dict[str, Any]:
        sess = await self.locate(p.get("session_key", ""))
        if not sess:
            raise LaunchError("unknown session")
        if sess.harness == "tmux":
            sess = _pane_as_agent(sess)
        if sess.harness not in ("claude", "codex", "pi", "opencode"):
            raise LaunchError(f"{sess.harness} sessions cannot be restarted from here")
        self.s.claude_config_dirs = discover_claude_dirs()
        saved_cwd = sess.cwd
        store = sess.extra.get("config_dir" if sess.harness == "claude" else "codex_home")
        if sess.harness in ("claude", "codex") and store:
            saved_cwd = past.relocated_cwd(Path(store), sess.session_id, saved_cwd)
        target = LaunchSpec.parse(
            {**p, "cwd": p.get("cwd") or saved_cwd, "harness": p.get("harness") or sess.harness}
        )
        # Validate the executable, endpoint and model before stopping the source agent.
        build(target, self.s, self._endpoints(p))
        if not Path(target.cwd).expanduser().is_dir():
            raise LaunchError(f"no such directory: {target.cwd}")
        import shutil

        if not shutil.which("tmux"):
            raise LaunchError("tmux is not installed on this machine")
        same_harness = target.harness == sess.harness
        if same_harness:
            # same conversation, other login / model / endpoint: restart it with --resume
            if sess.status == "busy" and not p.get("force"):
                raise LaunchError(
                    "the session is working; wait for it to finish its turn, or force"
                )
            if sess.harness == "claude" and target.backend == "login":
                new_dir = claude_config_dir(self.s, target.login)
                old_dir = Path(sess.extra.get("config_dir") or Path.home() / ".claude")
                if (new_dir / "projects").resolve() != (old_dir / "projects").resolve():
                    raise LaunchError(
                        f"{target.login} does not share transcripts with {old_dir.name}; "
                        "create it with `agentdash install account` so a session can move over"
                    )
            # pi resumes by file; the others by id
            target.resume = sess.transcript_path if sess.harness == "pi" else sess.session_id
            target.prompt = str(p.get("note") or "")
            if not sess.session_id:
                # started but never got going (a trust or login question in its way): there
                # is no conversation yet, so start it again with the task it was given
                target.prompt = target.prompt or str(sess.extra.get("launch_prompt") or "")
            target.name = target.name or sess.name
            if sess.pid:
                await self._stop(sess)
        else:
            # another harness cannot load the conversation: it gets a briefing and the transcript
            note = str(p.get("note") or "").strip()
            target.name = target.name or f"{Path(sess.cwd).name}-handover"
            target.prompt = handover_prompt(sess, note)
            if p.get("stop_old") and sess.pid:
                await self._stop(sess)
        runtime = Path(sess.extra.get("codex_home") or "")
        if same_harness and sess.harness == "codex" and runtime.name.startswith(".codex-move-"):
            result = await launch(target, self.s, self._endpoints(p), runtime_home=runtime)
        elif same_harness and sess.harness == "claude" and target.backend == "default":
            config = sess.extra.get("config_dir")
            runtime = Path(config) if config else None
            if runtime == Path.home() / ".claude":
                runtime = None
            result = await launch(
                target, self.s, self._endpoints(p),
                runtime_home=runtime,
            )
        else:
            result = await launch(target, self.s, self._endpoints(p))
        result["resumed"] = same_harness
        result["session_key"] = (
            Session.make_key(self.machine, target.harness, sess.session_id)
            if same_harness and sess.session_id
            else ""
        )
        return result

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
                for key in [k for k in self.pending_subs if k in self.sessions]:
                    await self.subscribe(key)
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
                if windows or self.usage.claude_accounts:
                    await self.hub.send(
                        NODE_USAGE,
                        {
                            "windows": [w.model_dump() for w in windows],
                            "claude_accounts": self.usage.claude_accounts,
                        },
                    )
                for d, account in self.usage.dead_logins.items():
                    if d not in self._dead_told:  # once per node run, not every ten minutes
                        self._dead_told.add(d)
                        await self.hub.send(
                            NODE_EVENT,
                            {
                                "kind": "claude.login_expired",
                                "dir": Path(d).name,
                                "account": account,
                            },
                        )
            except Exception:  # noqa: BLE001
                log.exception("usage collection failed")
            # a login that was added or renewed is polled at once, not at the next round
            waited, limit = 0.0, self.s.usage_interval + random.uniform(0, 60)
            seen = _credentials_signature()
            while waited < limit:
                try:
                    await asyncio.wait_for(self._credentials_changed.wait(), 30)
                except TimeoutError:
                    pass
                waited += 30
                if self._credentials_changed.is_set() or _credentials_signature() != seen:
                    self._credentials_changed.clear()
                    await asyncio.sleep(3)  # let Claude finish writing its files
                    break

    async def pa_reminder_loop(self) -> None:
        """Todos that became due turn into a push. The texts go to the hub to be pushed on,
        never to be stored (the hub treats `pa.reminder` events that way)."""
        minutes = self.s.pa_reminder_minutes
        if minutes <= 0 or not self.pa.available:
            return
        await asyncio.sleep(45)
        while True:
            try:
                for push in await self.pa.due_reminders():
                    await self.hub.send(NODE_EVENT, {"kind": "pa.reminder", **push})
            except Exception as e:  # noqa: BLE001
                log.warning("todo reminders: %s", e)
            await asyncio.sleep(minutes * 60)

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
            self.pa_reminder_loop(),
            serve_hooks(self, self.s.node_host, self.s.node_port),
        )


def _credentials_signature() -> tuple[tuple[str, float], ...]:
    """When each Claude login's credentials last changed."""
    out = []
    for d in discover_claude_dirs():
        try:
            out.append((d.name, (d / ".credentials.json").stat().st_mtime))
        except OSError:
            continue
    return tuple(out)


async def _tmux_session_like(prefix: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "tmux", "list-sessions", "-F", "#{session_name}",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )  # fmt: skip
    out, _ = await proc.communicate()
    names = [n for n in out.decode().split() if n.startswith(prefix)]
    return names[-1] if names else ""


def _pane_as_agent(sess: Session) -> Session:
    """A tmux pane whose agent has no session of its own yet, seen as that agent: what it
    resumes (if anything) is read from its command line."""
    agent = str(sess.extra.get("agent") or "")
    if agent not in ("claude", "codex", "pi", "opencode"):
        return sess
    x = dict(sess.extra)
    resume = str(x.get("resume") or "")
    out = sess.model_copy(
        update={
            "harness": Harness(agent),
            "session_id": resume,
            "name": str(x.get("launch_name") or sess.name),
            "transcript_path": resume if agent == "pi" else "",
            "extra": x,
        }
    )
    return out


async def _validate_claude_login(root: Path, launch_env: dict) -> None:
    """Use the CLI's own credential resolution, including keychain and API-key setups."""
    import shutil

    process = await asyncio.create_subprocess_exec(
        shutil.which("claude") or "claude", "auth", "status", "--json",
        env={**os.environ, **launch_env, "CLAUDE_CONFIG_DIR": str(root)},
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), 20)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise TransferError("destination Claude login check timed out") from None
    try:
        status = json.loads(stdout)
    except ValueError:
        raise TransferError("cannot check destination Claude login; update its CLI") from None
    if not status.get("loggedIn"):
        raise TransferError("Claude is logged out on the destination; sign in there before moving")


def _credentials_alive(path: Path) -> bool:
    """Whether a Claude login can still be used: a token that expired makes Claude ask
    to log in by itself, like an empty slot does."""
    try:
        oauth = json.loads(path.read_text()).get("claudeAiOauth") or {}
    except (OSError, ValueError, AttributeError):
        return False
    if not (oauth.get("accessToken") or oauth.get("refreshToken")):
        return False
    exp = int(oauth.get("expiresAt") or 0)
    # a stale access token is refreshed with the refresh token; only a dead one is fresh
    return bool(oauth.get("refreshToken")) or exp > time.time() * 1000


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
