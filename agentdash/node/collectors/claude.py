"""Roster of Claude Code sessions on this machine.

Sources: `claude agents --json` (interactive + background) and the per-pid
registry `~/.claude/sessions/<pid>.json` (adds messaging socket, name, status).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from ...models import Harness, Session, SessionStatus, now_ms
from ..adapters.claude_transcript import last_line_preview

log = logging.getLogger(__name__)

_STATUS_MAP = {
    "busy": SessionStatus.busy,
    "idle": SessionStatus.idle,
    "waiting": SessionStatus.waiting,
}
_STATE_MAP = {
    "working": SessionStatus.busy,
    "blocked": SessionStatus.waiting,
    "done": SessionStatus.done,
    "failed": SessionStatus.failed,
    "stopped": SessionStatus.stopped,
}


def provider_for_config_dir(config_dir: Path) -> str:
    name = config_dir.name
    if name == ".claude":
        return "anthropic"
    return name.removeprefix(".claude-") or "anthropic"


def find_transcript(config_dir: Path, session_id: str, cwd: str) -> Path | None:
    projects = config_dir / "projects"
    slug = cwd.replace("/", "-").replace(".", "-") if cwd else ""
    if slug:
        p = projects / slug / f"{session_id}.jsonl"
        if p.exists():
            return p
    for p in projects.glob(f"*/{session_id}.jsonl"):
        return p
    return None


class ClaudeCollector:
    def __init__(self, machine: str, config_dirs: list[Path]) -> None:
        self.machine = machine
        self.config_dirs = [d for d in config_dirs if d.exists()]
        self._transcripts: dict[str, Path] = {}

    async def _agents_json(self, config_dir: Path) -> list[dict[str, Any]]:
        env = dict(os.environ, CLAUDE_CONFIG_DIR=str(config_dir))
        try:
            proc = await asyncio.create_subprocess_exec(
                "claude",
                "agents",
                "--json",
                "--all",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            out, err = await asyncio.wait_for(proc.communicate(), 20)
        except (FileNotFoundError, TimeoutError) as e:
            log.warning("claude agents --json failed: %s", e)
            return []
        if proc.returncode != 0:
            log.warning("claude agents --json rc=%s: %s", proc.returncode, err.decode()[:200])
            return []
        try:
            data = json.loads(out.decode() or "[]")
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []

    def _registry(self, config_dir: Path) -> dict[int, dict[str, Any]]:
        out: dict[int, dict[str, Any]] = {}
        for p in (config_dir / "sessions").glob("*.json"):
            try:
                rec = json.loads(p.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(rec, dict) and "pid" in rec:
                out[int(rec["pid"])] = rec
        return out

    async def collect(self) -> list[Session]:
        sessions: list[Session] = []
        for config_dir in self.config_dirs:
            provider = provider_for_config_dir(config_dir)
            agents, registry = await self._agents_json(config_dir), self._registry(config_dir)
            seen_ids: set[str] = set()
            for a in agents:
                sid = a.get("sessionId") or a.get("id") or ""
                if not sid:
                    continue
                seen_ids.add(sid)
                pid = a.get("pid")
                reg = registry.get(int(pid)) if pid else None
                kind = a.get("kind", "unknown")
                status = _STATUS_MAP.get(a.get("status", ""), None)
                if status is None:
                    status = _STATE_MAP.get(a.get("state", ""), SessionStatus.idle)
                if a.get("waitingFor"):
                    status = SessionStatus.waiting
                cwd = a.get("cwd", "")
                tpath = self._transcripts.get(sid) or find_transcript(config_dir, sid, cwd)
                if tpath:
                    self._transcripts[sid] = tpath
                extra: dict[str, Any] = {"config_dir": str(config_dir)}
                if a.get("id"):
                    extra["job_id"] = a["id"]
                if reg:
                    extra["socket"] = reg.get("messagingSocketPath", "")
                    extra["version"] = reg.get("version", "")
                sessions.append(
                    Session(
                        key=Session.make_key(self.machine, Harness.claude, sid),
                        machine=self.machine,
                        harness=Harness.claude,
                        provider=provider,
                        session_id=sid,
                        name=_short_name(a.get("name") or (reg or {}).get("name", "") or ""),
                        cwd=cwd,
                        kind=kind if kind in ("interactive", "background") else "unknown",
                        status=status,
                        waiting_for=a.get("waitingFor", "") or "",
                        pid=int(pid) if pid else None,
                        started_at=a.get("startedAt"),
                        updated_at=_activity_ms(tpath, reg, a.get("startedAt")),
                        transcript_path=str(tpath) if tpath else "",
                        last_line=last_line_preview(tpath) if tpath else "",
                        extra=extra,
                    )
                )
            # registry-only sessions (agents --json may hide some interactive ones)
            for pid, reg in registry.items():
                sid = reg.get("sessionId", "")
                if not sid or sid in seen_ids or not _pid_alive(pid):
                    continue
                cwd = reg.get("cwd", "")
                tpath = find_transcript(config_dir, sid, cwd)
                sessions.append(
                    Session(
                        key=Session.make_key(self.machine, Harness.claude, sid),
                        machine=self.machine,
                        harness=Harness.claude,
                        provider=provider,
                        session_id=sid,
                        name=_short_name(reg.get("name", "")),
                        cwd=cwd,
                        kind="interactive" if reg.get("kind") == "interactive" else "unknown",
                        status=_STATUS_MAP.get(reg.get("status", ""), SessionStatus.idle),
                        pid=pid,
                        started_at=reg.get("startedAt"),
                        updated_at=_activity_ms(tpath, reg, reg.get("startedAt")),
                        transcript_path=str(tpath) if tpath else "",
                        last_line=last_line_preview(tpath) if tpath else "",
                        extra={
                            "config_dir": str(config_dir),
                            "socket": reg.get("messagingSocketPath", ""),
                        },
                    )
                )
        return sessions


def _short_name(name: str, limit: int = 72) -> str:
    """Background sessions are named after their prompt; keep the first line, capped."""
    first = name.strip().split("\n", 1)[0]
    return first if len(first) <= limit else first[: limit - 1].rstrip() + "…"


def _activity_ms(tpath: Path | None, reg: dict[str, Any] | None, started: Any) -> int:
    """Last activity: transcript mtime, else registry updatedAt, else start time."""
    if tpath:
        try:
            return int(tpath.stat().st_mtime * 1000)
        except OSError:
            pass
    if reg and reg.get("updatedAt"):
        return int(reg["updatedAt"])
    return int(started) if started else now_ms()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
