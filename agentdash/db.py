"""SQLite persistence for the hub (machines, sessions, events)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiosqlite

from .models import Machine, Session, SessionStatus, now_ms

TERMINAL = ("done", "failed", "stopped", "offline")

SCHEMA = """
CREATE TABLE IF NOT EXISTS machines (
  id TEXT PRIMARY KEY,
  hostname TEXT, os TEXT, harnesses TEXT, node_version TEXT,
  online INTEGER DEFAULT 0, armed INTEGER DEFAULT 0, last_seen INTEGER
);
CREATE TABLE IF NOT EXISTS sessions (
  key TEXT PRIMARY KEY,
  machine TEXT, harness TEXT, session_id TEXT, status TEXT,
  updated_at INTEGER, started_at INTEGER, data TEXT
);
CREATE INDEX IF NOT EXISTS sessions_machine ON sessions(machine);
CREATE INDEX IF NOT EXISTS sessions_updated ON sessions(updated_at);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER, machine TEXT, session_key TEXT, kind TEXT, payload TEXT
);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = aiosqlite.connect(self.path)
        conn.daemon = True  # never keep the process alive on an unclean exit
        self._db = await conn
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.execute("UPDATE machines SET online = 0")
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("database not open")
        return self._db

    # machines ---------------------------------------------------------
    async def upsert_machine(self, m: Machine) -> None:
        await self.db.execute(
            """INSERT INTO machines(id,hostname,os,harnesses,node_version,online,armed,last_seen)
               VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET hostname=excluded.hostname, os=excluded.os,
                 harnesses=excluded.harnesses, node_version=excluded.node_version,
                 online=excluded.online, last_seen=excluded.last_seen""",
            (
                m.id,
                m.hostname,
                m.os,
                json.dumps(m.harnesses),
                m.node_version,
                int(m.online),
                int(m.armed),
                m.last_seen,
            ),
        )
        await self.db.commit()

    async def set_machine_online(self, machine_id: str, online: bool) -> None:
        await self.db.execute(
            "UPDATE machines SET online=?, last_seen=? WHERE id=?",
            (int(online), now_ms(), machine_id),
        )
        if not online:
            cur = await self.db.execute(
                "SELECT key, data FROM sessions WHERE machine=? "
                "AND status IN ('busy','idle','waiting')",
                (machine_id,),
            )
            for row in await cur.fetchall():
                s = Session.model_validate_json(row["data"])
                s.status = SessionStatus.offline
                s.updated_at = now_ms()
                await self.db.execute(
                    "UPDATE sessions SET status=?, updated_at=?, data=? WHERE key=?",
                    (s.status, s.updated_at, s.model_dump_json(), row["key"]),
                )
        await self.db.commit()

    async def set_machine_armed(self, machine_id: str, armed: bool) -> None:
        await self.db.execute("UPDATE machines SET armed=? WHERE id=?", (int(armed), machine_id))
        await self.db.commit()

    async def list_machines(self) -> list[Machine]:
        cur = await self.db.execute("SELECT * FROM machines ORDER BY id")
        rows = await cur.fetchall()
        return [
            Machine(
                id=r["id"],
                hostname=r["hostname"] or "",
                os=r["os"] or "",
                harnesses=json.loads(r["harnesses"] or "[]"),
                node_version=r["node_version"] or "",
                online=bool(r["online"]),
                armed=bool(r["armed"]),
                last_seen=r["last_seen"] or 0,
            )
            for r in rows
        ]

    # sessions ---------------------------------------------------------
    async def replace_sessions(self, machine_id: str, sessions: list[Session]) -> list[Session]:
        """Store the node's full roster; sessions no longer reported become `done`.

        Returns the sessions whose stored form changed (for SSE fan-out).
        """
        cur = await self.db.execute("SELECT key, data FROM sessions WHERE machine=?", (machine_id,))
        old = {r["key"]: r["data"] for r in await cur.fetchall()}
        changed: list[Session] = []
        seen: set[str] = set()
        for s in sessions:
            seen.add(s.key)
            data = s.model_dump_json()
            if old.get(s.key) == data:
                continue
            changed.append(s)
            await self.db.execute(
                """INSERT INTO sessions(key,machine,harness,session_id,status,
                     updated_at,started_at,data) VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(key) DO UPDATE SET status=excluded.status,
                     updated_at=excluded.updated_at, started_at=excluded.started_at,
                     data=excluded.data""",
                (
                    s.key,
                    s.machine,
                    s.harness,
                    s.session_id,
                    s.status,
                    s.updated_at,
                    s.started_at,
                    data,
                ),
            )
        for key, data in old.items():
            if key in seen:
                continue
            s = Session.model_validate_json(data)
            if s.status in TERMINAL:
                continue
            s.status = SessionStatus.done
            s.updated_at = now_ms()
            changed.append(s)
            await self.db.execute(
                "UPDATE sessions SET status=?, updated_at=?, data=? WHERE key=?",
                (s.status, s.updated_at, s.model_dump_json(), key),
            )
        await self.db.commit()
        return changed

    async def get_session(self, key: str) -> Session | None:
        cur = await self.db.execute("SELECT data FROM sessions WHERE key=?", (key,))
        row = await cur.fetchone()
        return Session.model_validate_json(row["data"]) if row else None

    async def list_sessions(
        self, machine: str | None = None, active_only: bool = False, limit: int = 500
    ) -> list[Session]:
        sql = "SELECT data FROM sessions"
        cond: list[str] = []
        args: list[Any] = []
        if machine:
            cond.append("machine=?")
            args.append(machine)
        if active_only:
            cond.append("status IN ('busy','idle','waiting')")
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        args.append(limit)
        cur = await self.db.execute(sql, args)
        return [Session.model_validate_json(r["data"]) for r in await cur.fetchall()]

    # events -----------------------------------------------------------
    async def add_event(
        self, machine: str, session_key: str, kind: str, payload: dict[str, Any]
    ) -> int:
        cur = await self.db.execute(
            "INSERT INTO events(ts,machine,session_key,kind,payload) VALUES(?,?,?,?,?)",
            (now_ms(), machine, session_key, kind, json.dumps(payload)),
        )
        await self.db.commit()
        return int(cur.lastrowid or 0)

    async def list_events(self, limit: int = 100) -> list[dict[str, Any]]:
        cur = await self.db.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
        return [
            {
                "id": r["id"],
                "ts": r["ts"],
                "machine": r["machine"],
                "session_key": r["session_key"],
                "kind": r["kind"],
                "payload": json.loads(r["payload"] or "{}"),
            }
            for r in await cur.fetchall()
        ]
