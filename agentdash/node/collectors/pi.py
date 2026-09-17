"""Roster of pi coding agent sessions (running processes + recent session files)."""

from __future__ import annotations

import json
from pathlib import Path

from ...models import Harness, Session, SessionStatus, now_ms
from ..adapters.claude_transcript import read_last
from ..adapters.pi_session import iter_messages, session_header
from . import procs


def _settings_of(path: Path, max_bytes: int = 400_000) -> dict[str, str]:
    """Last model and thinking level recorded in a pi session file."""
    out = {"provider": "", "model": "", "effort": ""}
    try:
        with path.open("rb") as f:
            data = f.read(max_bytes)
    except OSError:
        return out
    for line in data.split(b"\n"):
        if b'"model_change"' not in line and b'"thinking_level_change"' not in line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("type") == "model_change":
            out["provider"] = str(rec.get("provider") or "")
            out["model"] = str(rec.get("modelId") or "")
        elif rec.get("type") == "thinking_level_change":
            out["effort"] = str(rec.get("thinkingLevel") or "")
    return out


class PiCollector:
    def __init__(self, machine: str, sessions_dir: Path | None = None) -> None:
        self.machine = machine
        self.dir = sessions_dir or Path.home() / ".pi" / "agent" / "sessions"
        self.live: dict[str, dict] = {}  # session_id -> info from the agentdash pi extension

    def _recent_files(self, since_ms: int) -> list[Path]:
        if not self.dir.exists():
            return []
        out = []
        for p in self.dir.glob("*/*.jsonl"):
            try:
                if p.stat().st_mtime * 1000 >= since_ms:
                    out.append(p)
            except OSError:
                continue
        return sorted(out, key=lambda p: p.stat().st_mtime, reverse=True)

    def collect(self) -> list[Session]:
        now = now_ms()
        running: dict[str, int] = {}  # cwd -> pid
        for pid in procs.find({"pi"}):
            running.setdefault(procs.cwd_of(pid), pid)
        sessions: list[Session] = []
        seen_cwd: set[str] = set()
        for path in self._recent_files(now - 6 * 3600 * 1000):
            head = session_header(path)
            sid = head.get("id") or path.stem.split("_")[-1]
            cwd = head.get("cwd", "")
            pid = running.get(cwd) if cwd not in seen_cwd else None
            if pid:
                seen_cwd.add(cwd)
            live = self.live.get(sid, {})
            mtime = int(path.stat().st_mtime * 1000)
            if live:
                status = SessionStatus(live.get("status", "idle"))
            elif pid:
                status = SessionStatus.busy if now - mtime < 90_000 else SessionStatus.idle
            else:
                status = SessionStatus.done
            cfg = _settings_of(path)
            last = ""
            for m in reversed(read_last(path, 30, iter_messages)):
                if m.kind == "text" and m.role in ("assistant", "user"):
                    last = " ".join(m.text.split())[:160]
                    break
            sessions.append(
                Session(
                    key=Session.make_key(self.machine, Harness.pi, sid),
                    machine=self.machine,
                    harness=Harness.pi,
                    provider=str(live.get("provider") or cfg["provider"]),
                    session_id=sid,
                    name=str(live.get("name") or Path(cwd).name or sid[:8]),
                    cwd=cwd,
                    kind="interactive" if pid else "unknown",
                    status=status,
                    waiting_for=str(live.get("waiting_for", "")),
                    pid=pid or live.get("pid"),
                    started_at=int(path.stat().st_ctime * 1000),
                    updated_at=mtime,
                    transcript_path=str(path),
                    last_line=last,
                    model=str(live.get("model") or cfg["model"]),
                    extra={
                        "inbox": bool(live),
                        **({"effort": cfg["effort"]} if cfg["effort"] else {}),
                    },
                )
            )
        return sessions
