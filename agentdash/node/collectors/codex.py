"""Roster of Codex CLI sessions: running TUIs/exec runs plus recently active threads."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from ...models import Harness, Session, SessionStatus, now_ms
from ..adapters.codex_rollout import scan_status
from . import procs

log = logging.getLogger(__name__)


def _tail_lines(path: Path, max_bytes: int = 300_000) -> list[str]:
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            f.seek(max(0, size - max_bytes))
            data = f.read()
    except OSError:
        return []
    lines = data.split(b"\n")
    if size > max_bytes:
        lines = lines[1:]
    return [line.decode("utf-8", "replace") for line in lines if line.strip()]


def _spawned_by(source: object) -> str:
    """Parent thread id when the threads.source column describes a sub-agent spawn."""
    if not isinstance(source, str) or not source.startswith("{"):
        return ""
    try:
        spawn = json.loads(source).get("subagent", {}).get("thread_spawn", {})
    except (json.JSONDecodeError, AttributeError):
        return ""
    return str(spawn.get("parent_thread_id") or "") if isinstance(spawn, dict) else ""


class CodexCollector:
    def __init__(self, machine: str, codex_home: Path | None = None) -> None:
        self.machine = machine
        self.home = codex_home or Path.home() / ".codex"
        self.state_db = self.home / "state_5.sqlite"

    _COLS = (
        "id, rollout_path, cwd, title, name, updated_at_ms, created_at_ms, model, "
        "first_user_message, reasoning_effort, source, agent_nickname, agent_role"
    )

    def _query(self, where: str, args: tuple) -> list[dict]:
        if not self.state_db.exists():
            return []
        try:
            c = sqlite3.connect(f"file:{self.state_db}?mode=ro", uri=True, timeout=2)
            c.row_factory = sqlite3.Row
            rows = c.execute(
                f"SELECT {self._COLS} FROM threads WHERE {where} "
                "ORDER BY updated_at_ms DESC LIMIT 200",
                args,
            ).fetchall()
            c.close()
            return [dict(r) for r in rows]
        except sqlite3.Error as e:
            log.warning("codex state db: %s", e)
            return []

    def _threads(self, since_ms: int) -> list[dict]:
        return self._query("updated_at_ms >= ?", (since_ms,))

    def _thread(self, thread_id: str) -> dict | None:
        rows = self._query("id = ?", (thread_id,))
        return rows[0] if rows else None

    def _latest_for_cwd(self, cwd: str, started_ms: int | None) -> dict | None:
        # a process is a main thread; sub-agent threads share its cwd and are often newer
        rows = [r for r in self._query("cwd = ?", (cwd,)) if not _spawned_by(r.get("source"))]
        for r in rows:
            if started_ms is None or int(r.get("created_at_ms") or 0) >= started_ms - 60_000:
                return r
        return rows[0] if rows else None

    def _running(self) -> dict[int, dict]:
        """pid -> {cwd, thread_id?, headless, started}"""
        out: dict[int, dict] = {}
        for pid in procs.find({"codex"}):
            argv = procs.cmdline(pid)
            if len(argv) < 1 or (
                len(argv) > 1 and argv[1] in ("app-server", "exec-server", "agents")
            ):
                continue
            thread = ""
            headless = "exec" in argv[1:3]
            if "resume" in argv:
                i = argv.index("resume")
                if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
                    thread = argv[i + 1]
            out[pid] = {
                "cwd": procs.cwd_of(pid),
                "thread": thread,
                "headless": headless,
                "started": procs.start_ms(pid),
            }
        return out

    def collect(self) -> list[Session]:
        now = now_ms()
        threads = self._threads(now - 6 * 3600 * 1000)
        running = self._running()
        by_id = {t["id"]: t for t in threads}
        sessions: list[Session] = []
        used_threads: set[str] = set()

        def make(t: dict, pid: int | None, kind: str, started: int | None) -> Session:
            path = Path(t["rollout_path"]) if t.get("rollout_path") else None
            info = scan_status(iter(_tail_lines(path))) if path and path.exists() else {}
            status = SessionStatus.busy if info.get("busy") else SessionStatus.idle
            spawn = _spawned_by(t.get("source"))
            if pid is None:
                # Codex runs its sub-agents inside the parent process: no pid of their own
                fresh = now - int(info.get("last_ts") or t.get("updated_at_ms") or 0) < 10 * 60000
                status = (
                    SessionStatus.busy
                    if spawn and info.get("busy") and fresh
                    else SessionStatus.done
                )
            name = (
                (
                    f"{t['agent_nickname']} ({t.get('agent_role') or 'sub-agent'})"
                    if spawn and t.get("agent_nickname")
                    else ""
                )
                or t.get("name")
                or t.get("title")
                or (t.get("first_user_message") or "")[:60]
                or Path(t.get("cwd") or info.get("cwd", "") or "codex").name
            )
            extra: dict = {"codex_home": str(self.home)}
            if info.get("last_user"):
                extra["last_user"] = info["last_user"]
            effort = info.get("effort") or t.get("reasoning_effort")
            if effort:
                extra["effort"] = str(effort)
            if spawn:
                extra["parent"] = Session.make_key(self.machine, Harness.codex, spawn)
            if info.get("context_tokens") and info.get("context_window"):
                extra["context_tokens"] = info["context_tokens"]
                extra["context_window"] = info["context_window"]
                extra["context_pct"] = round(
                    100 * info["context_tokens"] / info["context_window"], 1
                )
            return Session(
                key=Session.make_key(self.machine, Harness.codex, t["id"]),
                machine=self.machine,
                harness=Harness.codex,
                provider="openai",
                session_id=t["id"],
                name=str(name)[:72],
                cwd=t.get("cwd") or info.get("cwd", ""),
                kind=kind,  # type: ignore[arg-type]
                status=status,
                pid=pid,
                started_at=started or t.get("created_at_ms"),
                updated_at=max(int(t.get("updated_at_ms") or 0), int(info.get("last_ts") or 0))
                or now,
                transcript_path=str(path) if path else "",
                last_line=" ".join(str(info.get("last_agent_message", "")).split())[:160],
                model=str(info.get("model") or t.get("model") or ""),
                extra=extra,
            )

        for pid, r in running.items():
            t = (by_id.get(r["thread"]) or self._thread(r["thread"])) if r["thread"] else None
            if t is None:
                t = self._latest_for_cwd(r["cwd"], r["started"])
            if t is None or t["id"] in used_threads:
                continue
            used_threads.add(t["id"])
            sessions.append(
                make(t, pid, "headless" if r["headless"] else "interactive", r["started"])
            )
        for t in threads:
            if t["id"] in used_threads:
                continue
            if int(t.get("updated_at_ms") or 0) < now - 2 * 3600 * 1000:
                continue
            sessions.append(make(t, None, "unknown", None))
        return sessions
