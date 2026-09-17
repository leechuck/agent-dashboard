"""Read Hermes agent sessions and messages from its SQLite state databases."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ...models import Message


def state_dbs(root: Path | None = None) -> list[Path]:
    root = root or Path.home() / ".hermes"
    out = []
    if (root / "state.db").exists():
        out.append(root / "state.db")
    out += sorted(p for p in (root / "profiles").glob("*/state.db") if p.exists())
    return out


def _connect(db: Path) -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
    c.row_factory = sqlite3.Row
    return c


def sessions(db: Path, since_s: float) -> list[dict[str, Any]]:
    """Sessions that are open or were active after `since_s` (epoch seconds)."""
    try:
        c = _connect(db)
        rows = c.execute(
            """SELECT id, source, title, model, started_at, ended_at, end_reason, cwd,
                      message_count, tool_call_count, last_activity_at, profile_name,
                      billing_provider, estimated_cost_usd, chat_type, display_name,
                      last_activity_description
               FROM sessions
               WHERE hidden = 0
                 AND (ended_at IS NULL OR COALESCE(last_activity_at, started_at) >= ?)
               ORDER BY COALESCE(last_activity_at, started_at) DESC LIMIT 100""",
            (since_s,),
        ).fetchall()
        c.close()
    except sqlite3.Error:
        return []
    return [dict(r) for r in rows]


def messages(db: Path, session_id: str, after_id: int = 0, limit: int = 400) -> list[Message]:
    try:
        c = _connect(db)
        rows = c.execute(
            """SELECT id, role, content, tool_calls, tool_name, tool_call_id, reasoning,
                      reasoning_content, timestamp
               FROM messages WHERE session_id = ? AND id > ? AND active = 1
               ORDER BY id LIMIT ?""",
            (session_id, after_id, limit),
        ).fetchall()
        c.close()
    except sqlite3.Error:
        return []
    out: list[Message] = []
    for r in rows:
        mid = str(r["id"])
        ts = int(float(r["timestamp"]) * 1000) if r["timestamp"] else None
        role = r["role"] or "assistant"
        reasoning = r["reasoning_content"] or r["reasoning"]
        if reasoning and str(reasoning).strip():
            out.append(
                Message(
                    id=mid + ":r",
                    ts=ts,
                    role="assistant",
                    kind="thinking",
                    text=str(reasoning)[:8000],
                )
            )
        if role == "tool":
            out.append(
                Message(
                    id=mid,
                    ts=ts,
                    role="tool",
                    kind="tool_result",
                    tool_name=str(r["tool_name"] or ""),
                    tool_use_id=str(r["tool_call_id"] or ""),
                    text=str(r["content"] or "")[:4000],
                )
            )
            continue
        content = r["content"]
        if content and str(content).strip():
            meta = role == "user" and str(content).startswith("You've reached the maximum")
            out.append(
                Message(
                    id=mid,
                    ts=ts,
                    role="user"
                    if role == "user"
                    else "assistant"
                    if role == "assistant"
                    else "system",
                    kind="text" if role in ("user", "assistant") else "system",
                    text=str(content)[:20000],
                    is_meta=meta or role == "system",
                )
            )
        calls = r["tool_calls"]
        if calls:
            try:
                parsed = json.loads(calls) if isinstance(calls, str) else calls
            except json.JSONDecodeError:
                parsed = []
            for i, call in enumerate(parsed if isinstance(parsed, list) else []):
                fn = call.get("function") or {}
                args = fn.get("arguments")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"input": args[:4000]}
                out.append(
                    Message(
                        id=f"{mid}:c{i}",
                        ts=ts,
                        role="assistant",
                        kind="tool_use",
                        tool_name=str(fn.get("name") or call.get("name") or ""),
                        tool_use_id=str(call.get("id") or ""),
                        tool_input=args if isinstance(args, dict) else None,
                    )
                )
    return out
