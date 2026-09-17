"""Roster of Hermes agent sessions (CLI runs, gateway chats) from its state databases."""

from __future__ import annotations

import time
from pathlib import Path

from ...models import Harness, Session, SessionStatus, now_ms
from ..adapters.hermes_state import sessions, state_dbs
from . import procs


class HermesCollector:
    def __init__(self, machine: str, root: Path | None = None) -> None:
        self.machine = machine
        self.root = root or Path.home() / ".hermes"

    def collect(self) -> list[Session]:
        dbs = state_dbs(self.root)
        if not dbs:
            return []
        now = now_ms()
        live_pids = procs.find({"hermes"}) + [
            p
            for p in procs.find({"python", "python3"})
            if any("hermes_cli" in a for a in procs.cmdline(p))
        ]
        by_cwd = {procs.cwd_of(p): p for p in live_pids}
        out: list[Session] = []
        for db in dbs:
            profile = db.parent.name if db.parent.name != ".hermes" else ""
            for s in sessions(db, time.time() - 6 * 3600):
                last = float(s.get("last_activity_at") or s.get("started_at") or 0)
                ended = s.get("ended_at") is not None
                source = str(s.get("source") or "cli")
                if ended:
                    status = SessionStatus.done
                elif now / 1000 - last < 180:
                    status = SessionStatus.busy
                else:
                    status = SessionStatus.idle
                if not ended and source == "cli" and now / 1000 - last > 3600:
                    status = SessionStatus.stopped  # abandoned run, never closed
                pid = by_cwd.get(s.get("cwd") or "", None)
                provider = str(s.get("billing_provider") or "")
                if provider == "custom":
                    provider = "unimatrix"
                title = s.get("title") or s.get("display_name") or f"{source} session"
                out.append(
                    Session(
                        key=Session.make_key(self.machine, Harness.hermes, s["id"]),
                        machine=self.machine,
                        harness=Harness.hermes,
                        provider=provider,
                        session_id=s["id"],
                        name=str(title)[:72],
                        cwd=str(s.get("cwd") or ""),
                        kind="interactive" if source != "cli" else "headless",
                        status=status,
                        pid=pid,
                        started_at=int(float(s["started_at"]) * 1000)
                        if s.get("started_at")
                        else None,
                        updated_at=int(last * 1000) or now,
                        transcript_path=str(db),
                        last_line=str(s.get("last_activity_description") or "")[:160],
                        model=str(s.get("model") or ""),
                        extra={
                            "store": "hermes",
                            "profile": profile,
                            "source": source,
                            "chat": s.get("chat_type") or "",
                            "cost_usd": s.get("estimated_cost_usd"),
                        },
                    )
                )
        return out
