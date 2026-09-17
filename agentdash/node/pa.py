"""Personal briefing: the PA agent in ~/pa writes it, the dashboard shows and acts on it.

Everything personal stays on this machine. The briefing file, the state of each item and
the workspace list are read here on request and relayed by the hub without being stored.
Mail is sent only by asking the running Emacs (Gnus) to send a draft, through the same
helper functions the PA agent uses; nothing else in this module can send anything.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from ..config import Settings
from ..models import Session, now_ms
from .adapters.claude_cli import start_background
from .pa_panels import PanelError, Panels

log = logging.getLogger(__name__)

SESSION_NAME = "pa-briefing"
_BUFFER = re.compile(r"^\*claude-mail-\d+\*$")
_MSGID = re.compile(r"^[^\s\"\\<>]{3,300}$")
STATUSES = {"open", "done", "ignored", "sent", "delegated"}
URGENCIES = ("now", "today", "week", "fyi")


class PAError(Exception):
    pass


def _el(s: str) -> str:
    """A Python string as an Emacs Lisp string literal."""
    return json.dumps(s, ensure_ascii=False)


def normalise(briefing: Any) -> dict[str, Any]:
    """The briefing is written by a model: keep what fits the schema, drop the rest."""
    if not isinstance(briefing, dict):
        raise PAError("dashboard_briefing.json is not a JSON object")
    items = []
    for n, raw in enumerate(briefing.get("items") or []):
        if not isinstance(raw, dict):
            continue
        item = {k: raw.get(k) for k in ("channel", "from", "subject", "received", "ask", "link")}
        item = {k: str(v) for k, v in item.items() if v not in (None, "")}
        item["id"] = str(raw.get("id") or f"item:{n}")
        item["urgency"] = raw.get("urgency") if raw.get("urgency") in URGENCIES else "week"
        item["needs_decision"] = str(raw.get("needs_decision") or "")
        draft, task = raw.get("draft"), raw.get("task")
        if isinstance(draft, dict) and str(draft.get("body") or "").strip():
            item["draft"] = {
                **draft,
                "kind": str(draft.get("kind") or ""),
                "body": str(draft["body"]),
            }
        if isinstance(task, dict) and str(task.get("prompt") or "").strip():
            item["task"] = {
                **task,
                "title": str(task.get("title") or item.get("subject") or "task"),
                "prompt": str(task["prompt"]),
            }
        items.append(item)

    def rows(key: str, fields: tuple[str, ...]) -> list[dict[str, str]]:
        out = []
        for r in briefing.get(key) or []:
            if isinstance(r, dict):
                out.append({f: str(r.get(f) or "") for f in fields})
        return out

    return {
        "generated_at": str(briefing.get("generated_at") or ""),
        "summary": str(briefing.get("summary") or ""),
        "schedule": rows("schedule", ("when", "what", "note")),
        "deadlines": rows("deadlines", ("date", "what", "project")),
        "items": items,
    }


class PersonalAssistant:
    def __init__(self, s: Settings) -> None:
        self.s = s
        self.dir = s.pa_dir.expanduser()
        self.file = self.dir / "data" / "dashboard_briefing.json"
        self.state_file = s.state_dir / "pa-state.json"
        self.panels = Panels(self.dir)
        self._lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        return (self.dir / "CLAUDE.md").exists()

    # ---- reading -----------------------------------------------------
    def _state(self) -> dict[str, dict[str, Any]]:
        try:
            return json.loads(self.state_file.read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_state(self, state: dict[str, dict[str, Any]]) -> None:
        cutoff = now_ms() - 60 * 24 * 3600 * 1000
        state = {k: v for k, v in state.items() if int(v.get("at", 0)) > cutoff}
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(state))
        tmp.replace(self.state_file)

    def workspaces(self) -> dict[str, Any]:
        try:
            data = yaml.safe_load((self.dir / "configs" / "workspaces.yaml").read_text()) or {}
        except (OSError, yaml.YAMLError) as e:
            log.warning("workspaces.yaml: %s", e)
            data = {}
        return {
            "workspaces": [w for w in data.get("workspaces") or [] if isinstance(w, dict)],
            "roots": [r for r in data.get("roots") or [] if isinstance(r, dict)],
        }

    def _session(self, sessions: dict[str, Session]) -> Session | None:
        mine = [
            s
            for s in sessions.values()
            if s.harness == "claude"
            and s.name.startswith(SESSION_NAME)
            and s.status in ("busy", "idle", "waiting")
        ]
        return max(mine, key=lambda s: s.started_at or 0) if mine else None

    def get(self, sessions: dict[str, Session]) -> dict[str, Any]:
        if not self.available:
            return {"ok": False, "error": "no pa"}
        briefing: dict[str, Any] | None = None
        try:
            briefing = normalise(json.loads(self.file.read_text()))
        except OSError:
            pass
        except json.JSONDecodeError as e:
            return {"ok": False, "error": f"dashboard_briefing.json is not valid JSON: {e}"}
        except (PAError, PanelError) as e:
            return {"ok": False, "error": str(e)}
        state = self._state()
        if briefing:
            for item in briefing.get("items") or []:
                st = state.get(str(item.get("id")), {})
                item["status"] = st.get("status", "open")
                item["status_note"] = st.get("note", "")
            briefing["file_mtime"] = int(self.file.stat().st_mtime * 1000)
        sess = self._session(sessions)
        return {
            "ok": True,
            "briefing": briefing,
            "session": {"key": sess.key, "status": sess.status, "name": sess.name}
            if sess
            else None,
            **self.workspaces(),
        }

    def _item(self, item_id: str) -> dict[str, Any]:
        try:
            items = json.loads(self.file.read_text()).get("items") or []
        except (OSError, json.JSONDecodeError) as e:
            raise PAError("no briefing on disk") from e
        for item in items:
            if str(item.get("id")) == item_id:
                return item
        raise PAError("that item is not in the current briefing")

    def mark(self, item_id: str, status: str, note: str = "") -> dict[str, Any]:
        if status not in STATUSES:
            raise PAError(f"unknown status {status}")
        state = self._state()
        state[item_id] = {"status": status, "note": note[:300], "at": now_ms()}
        self._save_state(state)
        return {"ok": True, "status": status}

    # ---- running the agent -------------------------------------------
    async def run(
        self, sessions: dict[str, Session], focus: str = "", agent: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if not self.available:
            return {"ok": False, "error": "no pa"}
        sess = self._session(sessions)
        if sess and sess.status == "busy":
            return {"ok": True, "already_running": True, "session_key": sess.key}
        prompt = resources.files("agentdash.node").joinpath("pa_prompt.md").read_text()
        if focus.strip():
            prompt += f"\n\nRobert's note for this run: {focus.strip()[:1000]}\n"
        agent = agent or {}
        known = {d.name: d for d in self.s.claude_config_dirs}
        config_dir = known.get(str(agent.get("login") or "")) or self.s.claude_config_dirs[0]
        r = await start_background(
            str(self.dir),
            prompt,
            name=SESSION_NAME,
            config_dir=str(config_dir),
            model=str(agent.get("model") or ""),
        )
        return {"ok": bool(r.get("ok")), "error": "" if r.get("ok") else r.get("output", "failed")}

    # ---- Emacs -------------------------------------------------------
    async def _emacs(self, form: str, timeout: float = 60) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                "emacsclient",
                "-s",
                self.s.pa_emacs_server,
                "--eval",
                form,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as e:
            raise PAError("emacsclient is not installed here") from e
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except TimeoutError as e:
            proc.kill()
            raise PAError(
                "Emacs did not answer; it is probably asking a question in the Gnus frame"
            ) from e
        if proc.returncode != 0:
            raise PAError((err.decode() or out.decode()).strip()[:300] or "emacsclient failed")
        return out.decode().strip()

    async def _ensure_helpers(self) -> None:
        if await self._emacs("(fboundp 'claude-email-send-buffer)", 15) != "t":
            await self._emacs(f"(load {_el(str(self.s.pa_helpers_el.expanduser()))} nil t)", 30)

    async def _buffer_alive(self, name: str) -> bool:
        return await self._emacs(f"(if (get-buffer {_el(name)}) t nil)", 15) == "t"

    async def _discard(self, name: str) -> None:
        """Standard Gnus draft removal (message-kill-buffer), answering its questions with yes."""
        await self._emacs(
            "(cl-letf (((symbol-function 'yes-or-no-p) (lambda (&rest _) t))"
            " ((symbol-function 'y-or-n-p) (lambda (&rest _) t)))"
            f" (claude-email-discard-buffer {_el(name)}) t)"
        )

    async def _compose(self, draft: dict[str, Any], body: str) -> str:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(body.rstrip("\n") + "\n")
            body_file = f.name
        try:
            if draft.get("kind") == "email_reply":
                mid = str(draft.get("message_id") or "").strip("<>")
                if not _MSGID.match(mid):
                    raise PAError("the draft has no usable message-id to reply to")
                wide = "t" if draft.get("wide") else "nil"
                form = f"(claude-email-reply {_el(mid)} {_el(body_file)} {wide})"
            else:
                to, subject = str(draft.get("to") or ""), str(draft.get("subject") or "")
                if not to or not subject:
                    raise PAError("a new mail needs a recipient and a subject")
                cc = _el(str(draft["cc"])) if draft.get("cc") else "nil"
                form = f"(claude-email-compose {_el(to)} {_el(subject)} {_el(body_file)} {cc})"
            name = (await self._emacs(form, 90)).strip('"')
        finally:
            Path(body_file).unlink(missing_ok=True)
        if not _BUFFER.match(name):
            raise PAError(f"Emacs did not return a draft buffer: {name[:120]}")
        return name

    async def send_email(self, item_id: str, body: str | None) -> dict[str, Any]:
        """Send the draft of one briefing item through Gnus. Recipients come from the
        briefing file, never from the request; only the body may be edited."""
        async with self._lock:
            item = self._item(item_id)
            draft = item.get("draft") or {}
            if draft.get("kind") not in ("email_reply", "email_new"):
                raise PAError("this item has no mail draft")
            if self._state().get(item_id, {}).get("status") == "sent":
                raise PAError("already sent")
            await self._ensure_helpers()
            name = str(draft.get("buffer") or "")
            alive = bool(_BUFFER.match(name)) and await self._buffer_alive(name)
            original = str(draft.get("body") or "")
            text = original if body is None else body
            if not text.strip():
                raise PAError("empty body")
            if alive and text.strip() != original.strip():
                await self._discard(name)
                alive = False
            if not alive:
                name = await self._compose(draft, text)
            await self._emacs(f"(progn (claude-email-send-buffer {_el(name)}) t)", 90)
            if await self._buffer_alive(name):
                raise PAError(
                    f"Gnus kept the draft {name} open; look at it in Emacs before retrying"
                )
            self.mark(item_id, "sent", f"sent through Gnus to {draft.get('to', '')}")
            return {"ok": True, "status": "sent"}

    async def discard_draft(self, item_id: str) -> dict[str, Any]:
        async with self._lock:
            draft = self._item(item_id).get("draft") or {}
            name = str(draft.get("buffer") or "")
            await self._ensure_helpers()
            if _BUFFER.match(name) and await self._buffer_alive(name):
                await self._discard(name)
            return self.mark(item_id, "ignored", "draft discarded")

    # ---- dispatch ----------------------------------------------------
    async def handle(self, p: dict[str, Any], sessions: dict[str, Session]) -> dict[str, Any]:
        op = p.get("op", "get")
        try:
            if op == "get":
                return await asyncio.to_thread(self.get, sessions)
            if not self.available:
                return {"ok": False, "error": "no pa"}
            if op == "panel":
                return await self.panels.panel(str(p.get("name")), bool(p.get("fresh")))
            if op == "act":
                return await self.panels.act(str(p.get("act")), p.get("args") or {})
            if op == "run":
                return await self.run(sessions, str(p.get("focus") or ""), p.get("agent"))
            if op == "send_email":
                body = p.get("body")
                return await self.send_email(
                    str(p.get("id")), body if isinstance(body, str) else None
                )
            if op == "discard":
                return await self.discard_draft(str(p.get("id")))
            if op == "mark":
                return self.mark(str(p.get("id")), str(p.get("status")), str(p.get("note") or ""))
            return {"ok": False, "error": f"unknown op {op}"}
        except (PAError, PanelError) as e:
            return {"ok": False, "error": str(e)}
