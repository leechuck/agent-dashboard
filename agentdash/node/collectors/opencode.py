"""Read-only roster of opencode sessions active recently."""

from __future__ import annotations

from pathlib import Path

from ...models import Harness, Session, SessionStatus, now_ms
from ..adapters.opencode_store import list_sessions
from . import procs


class OpencodeCollector:
    def __init__(self, machine: str, root: Path | None = None) -> None:
        self.machine = machine
        self.root = root or Path.home() / ".local" / "share" / "opencode" / "storage"

    def collect(self) -> list[Session]:
        if not self.root.exists():
            return []
        now = now_ms()
        running_dirs = {procs.cwd_of(p) for p in procs.find({"opencode"})}
        out: list[Session] = []
        for s in list_sessions(self.root, now - 3 * 3600 * 1000):
            t = s.get("time") or {}
            updated = int(t.get("updated") or t.get("created") or 0)
            if now - updated > 3 * 3600 * 1000:
                continue
            cwd = s.get("directory", "")
            alive = cwd in running_dirs
            if alive:
                status = SessionStatus.busy if now - updated < 120_000 else SessionStatus.idle
            else:
                status = SessionStatus.done
            out.append(
                Session(
                    key=Session.make_key(self.machine, Harness.opencode, s["id"]),
                    machine=self.machine,
                    harness=Harness.opencode,
                    provider="",
                    session_id=s["id"],
                    name=str(s.get("title") or s.get("slug") or s["id"])[:72],
                    cwd=cwd,
                    kind="interactive" if alive else "unknown",
                    status=status,
                    started_at=int(t.get("created") or 0) or None,
                    updated_at=updated or now,
                    transcript_path=str(self.root),  # messages come from the store, not one file
                    extra={"store": True},
                )
            )
        return out
