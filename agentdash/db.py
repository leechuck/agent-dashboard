"""SQLite persistence for the hub (machines, sessions, events)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiosqlite

from .models import (
    Decision,
    DecisionStatus,
    Machine,
    Session,
    SessionStatus,
    UsageWindow,
    now_ms,
)

TERMINAL = ("done", "failed", "stopped", "offline")

SCHEMA = """
CREATE TABLE IF NOT EXISTS machines (
  id TEXT PRIMARY KEY,
  hostname TEXT, os TEXT, harnesses TEXT, node_version TEXT,
  online INTEGER DEFAULT 0, armed INTEGER DEFAULT 0, armed_until INTEGER DEFAULT 0,
  last_seen INTEGER
);
CREATE TABLE IF NOT EXISTS decisions (
  id TEXT PRIMARY KEY,
  machine TEXT, session_key TEXT, status TEXT, created_at INTEGER, data TEXT
);
CREATE INDEX IF NOT EXISTS decisions_status ON decisions(status, created_at);
CREATE TABLE IF NOT EXISTS usage_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  provider TEXT, machine TEXT, window TEXT, used_pct REAL, resets_at INTEGER,
  fetched_at INTEGER, data TEXT
);
CREATE INDEX IF NOT EXISTS usage_pw ON usage_snapshots(provider, window, fetched_at);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS titles (
  key TEXT PRIMARY KEY, title TEXT, basis TEXT, at INTEGER
);
CREATE TABLE IF NOT EXISTS cockpit_silenced (
  id TEXT PRIMARY KEY, title TEXT, until INTEGER, created_at INTEGER
);
CREATE TABLE IF NOT EXISTS push_subscriptions (
  endpoint TEXT PRIMARY KEY, data TEXT, created_at INTEGER, label TEXT
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
        await self._migrate()
        await self._db.execute("UPDATE machines SET online = 0")
        await self._db.commit()

    async def _migrate(self) -> None:
        """Add columns introduced after the first release (SQLite has no ADD IF NOT EXISTS)."""
        wanted = {
            "machines": {"armed_until": "INTEGER DEFAULT 0"},
            "usage_snapshots": {"account": "TEXT DEFAULT ''"},
        }
        for table, cols in wanted.items():
            cur = await self.db.execute(f"PRAGMA table_info({table})")
            have = {r["name"] for r in await cur.fetchall()}
            for col, decl in cols.items():
                if col not in have:
                    await self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
                    if (table, col) == ("usage_snapshots", "account"):
                        await self.db.execute(
                            "UPDATE usage_snapshots "
                            "SET account = COALESCE(json_extract(data, '$.account'), '')"
                        )
        await self.db.execute(
            "CREATE INDEX IF NOT EXISTS usage_paw "
            "ON usage_snapshots(provider, account, window, fetched_at)"
        )
        await self.db.commit()

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
            """INSERT INTO machines(id,hostname,os,harnesses,node_version,online,armed,
                 armed_until,last_seen) VALUES(?,?,?,?,?,?,?,?,?)
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
                m.armed_until,
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

    async def set_machine_armed(self, machine_id: str, armed: bool, armed_until: int = 0) -> None:
        await self.db.execute(
            "UPDATE machines SET armed=?, armed_until=? WHERE id=?",
            (int(armed), armed_until, machine_id),
        )
        await self.db.commit()

    async def get_machine(self, machine_id: str) -> Machine | None:
        cur = await self.db.execute("SELECT * FROM machines WHERE id=?", (machine_id,))
        r = await cur.fetchone()
        return self._machine(r) if r else None

    @staticmethod
    def _machine(r: Any) -> Machine:
        return Machine(
            id=r["id"],
            hostname=r["hostname"] or "",
            os=r["os"] or "",
            harnesses=json.loads(r["harnesses"] or "[]"),
            node_version=r["node_version"] or "",
            online=bool(r["online"]),
            armed=bool(r["armed"]),
            armed_until=r["armed_until"] or 0,
            last_seen=r["last_seen"] or 0,
        )

    async def list_machines(self) -> list[Machine]:
        cur = await self.db.execute("SELECT * FROM machines ORDER BY id")
        return [self._machine(r) for r in await cur.fetchall()]

    # decisions --------------------------------------------------------
    async def upsert_decision(self, d: Decision) -> None:
        await self.db.execute(
            """INSERT INTO decisions(id,machine,session_key,status,created_at,data)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET status=excluded.status, data=excluded.data""",
            (d.id, d.machine, d.session_key, d.status, d.created_at, d.model_dump_json()),
        )
        await self.db.commit()

    async def get_decision(self, decision_id: str) -> Decision | None:
        cur = await self.db.execute("SELECT data FROM decisions WHERE id=?", (decision_id,))
        r = await cur.fetchone()
        return Decision.model_validate_json(r["data"]) if r else None

    async def list_decisions(self, pending_only: bool = False, limit: int = 100) -> list[Decision]:
        sql = "SELECT data FROM decisions"
        if pending_only:
            sql += " WHERE status='pending'"
        sql += " ORDER BY created_at DESC LIMIT ?"
        cur = await self.db.execute(sql, (limit,))
        return [Decision.model_validate_json(r["data"]) for r in await cur.fetchall()]

    async def expire_decisions(self, machine: str | None = None) -> list[Decision]:
        """Mark pending decisions whose deadline passed (or whose node vanished)."""
        now = now_ms()
        out: list[Decision] = []
        for d in await self.list_decisions(pending_only=True, limit=1000):
            if machine is not None and d.machine != machine:
                continue
            if machine is not None or (d.expires_at and d.expires_at < now):
                d.status = DecisionStatus.expired
                d.answered_at = now
                await self.upsert_decision(d)
                out.append(d)
        return out

    # usage ------------------------------------------------------------
    async def add_usage(self, w: UsageWindow) -> None:
        await self.db.execute(
            """INSERT INTO usage_snapshots(provider,account,machine,window,used_pct,resets_at,
                 fetched_at,data) VALUES(?,?,?,?,?,?,?,?)""",
            (
                w.provider,
                w.account,
                w.machine,
                w.window,
                w.used_pct,
                w.resets_at,
                w.fetched_at,
                w.model_dump_json(),
            ),
        )
        await self.db.commit()

    async def latest_usage(self, max_age_ms: int = 48 * 3600 * 1000) -> list[UsageWindow]:
        """Newest snapshot per (provider, account, window), whichever machine reported it.

        Accounts nobody has reported for two days (a login that went away) drop out.
        """
        cur = await self.db.execute(
            """SELECT data FROM usage_snapshots u
               WHERE fetched_at >= ?
                 AND fetched_at = (SELECT MAX(fetched_at) FROM usage_snapshots
                                   WHERE provider=u.provider AND account=u.account
                                     AND window=u.window)
               GROUP BY provider, account, window
               ORDER BY provider, account, window""",
            (now_ms() - max_age_ms,),
        )
        return [UsageWindow.model_validate_json(r["data"]) for r in await cur.fetchall()]

    async def usage_history(
        self, provider: str, window: str, since_ms: int, account: str | None = None
    ) -> list[tuple[int, float]]:
        sql = "SELECT fetched_at, used_pct FROM usage_snapshots WHERE provider=? AND window=?"
        args: list[Any] = [provider, window]
        if account is not None:
            sql += " AND account=?"
            args.append(account)
        cur = await self.db.execute(
            sql + " AND fetched_at>=? ORDER BY fetched_at", [*args, since_ms]
        )
        return [(r["fetched_at"], r["used_pct"]) for r in await cur.fetchall()]

    async def adopt_legacy_usage(self, provider: str, account: str, plan: str) -> None:
        """Rows written before accounts had names ("" or just the plan) join this account."""
        await self.db.execute(
            """UPDATE usage_snapshots SET account=?, data=json_set(data, '$.account', ?)
               WHERE provider=? AND account IN ('', ?) AND account != ?""",
            (account, account, provider, plan, account),
        )
        await self.db.commit()

    # settings and titles ----------------------------------------------
    async def get_setting(self, key: str) -> dict[str, Any]:
        cur = await self.db.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = await cur.fetchone()
        try:
            return json.loads(row["value"]) if row else {}
        except json.JSONDecodeError:
            return {}

    async def set_setting(self, key: str, value: dict[str, Any]) -> None:
        await self.db.execute(
            "INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, json.dumps(value))
        )
        await self.db.commit()

    async def titles(self) -> dict[str, dict[str, Any]]:
        cur = await self.db.execute("SELECT key, title, basis, at FROM titles")
        return {r["key"]: dict(r) for r in await cur.fetchall()}

    async def set_title(self, key: str, title: str, basis: str) -> None:
        await self.db.execute(
            "INSERT OR REPLACE INTO titles(key,title,basis,at) VALUES(?,?,?,?)",
            (key, title, basis, now_ms()),
        )
        await self.db.commit()

    async def prune_titles(self, keep_ms: int) -> None:
        await self.db.execute("DELETE FROM titles WHERE at < ?", (now_ms() - keep_ms,))
        await self.db.commit()

    # cockpit ----------------------------------------------------------
    async def silence(self, item_id: str, title: str, until: int) -> None:
        """until = 0 silences for good."""
        await self.db.execute(
            "INSERT OR REPLACE INTO cockpit_silenced(id,title,until,created_at) VALUES(?,?,?,?)",
            (item_id, title, until, now_ms()),
        )
        await self.db.commit()

    async def unsilence(self, item_id: str) -> None:
        await self.db.execute("DELETE FROM cockpit_silenced WHERE id=?", (item_id,))
        await self.db.commit()

    async def silenced(self) -> list[dict[str, Any]]:
        await self.db.execute(
            "DELETE FROM cockpit_silenced WHERE until > 0 AND until < ?", (now_ms(),)
        )
        await self.db.commit()
        cur = await self.db.execute(
            "SELECT id, title, until FROM cockpit_silenced ORDER BY created_at DESC"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def prune_events(self, keep_ms: int) -> None:
        await self.db.execute("DELETE FROM events WHERE ts < ?", (now_ms() - keep_ms,))
        await self.db.commit()

    async def prune_decisions(self, keep_ms: int) -> None:
        await self.db.execute(
            "DELETE FROM decisions WHERE status != 'pending' AND created_at < ?",
            (now_ms() - keep_ms,),
        )
        await self.db.commit()

    async def vacuum(self) -> None:
        """Give freed pages back to the disk. SQLite refuses this inside a transaction."""
        await self.db.commit()
        await self.db.execute("VACUUM")

    async def prune_usage(self, keep_ms: int) -> None:
        await self.db.execute(
            "DELETE FROM usage_snapshots WHERE fetched_at < ?", (now_ms() - keep_ms,)
        )
        await self.db.commit()

    # push subscriptions -----------------------------------------------
    async def add_push_subscription(self, sub: dict[str, Any], label: str = "") -> None:
        await self.db.execute(
            """INSERT INTO push_subscriptions(endpoint,data,created_at,label) VALUES(?,?,?,?)
               ON CONFLICT(endpoint) DO UPDATE SET data=excluded.data, label=excluded.label""",
            (sub.get("endpoint", ""), json.dumps(sub), now_ms(), label),
        )
        await self.db.commit()

    async def remove_push_subscription(self, endpoint: str) -> None:
        await self.db.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (endpoint,))
        await self.db.commit()

    async def list_push_subscriptions(self) -> list[dict[str, Any]]:
        cur = await self.db.execute("SELECT data FROM push_subscriptions")
        return [json.loads(r["data"]) for r in await cur.fetchall()]

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

    async def delete_session(self, key: str) -> None:
        await self.db.execute("DELETE FROM sessions WHERE key=?", (key,))
        await self.db.commit()

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
