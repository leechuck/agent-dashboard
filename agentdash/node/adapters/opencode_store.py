"""Read opencode's JSON session store (session, message and part files)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...models import Message


def load_json(p: Path) -> dict[str, Any]:
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def list_sessions(root: Path, since_ms: int) -> list[dict[str, Any]]:
    out = []
    for p in (root / "session").glob("*/ses_*.json"):
        try:
            if p.stat().st_mtime * 1000 < since_ms:
                continue
        except OSError:
            continue
        s = load_json(p)
        if s.get("id"):
            out.append(s)
    return out


def messages(root: Path, session_id: str, limit: int = 300) -> list[Message]:
    mdir = root / "message" / session_id
    if not mdir.exists():
        return []
    files = sorted(mdir.glob("msg_*.json"))[-limit:]
    out: list[Message] = []
    for mf in files:
        m = load_json(mf)
        mid, role = m.get("id", mf.stem), m.get("role", "assistant")
        ts = (m.get("time") or {}).get("created")
        pdir = root / "part" / mid
        parts = [load_json(pf) for pf in sorted(pdir.glob("prt_*.json"))] if pdir.exists() else []
        for i, part in enumerate(parts):
            pt = part.get("type")
            pid = f"{mid}:{i}"
            if pt == "text" and str(part.get("text", "")).strip():
                out.append(
                    Message(
                        id=pid,
                        ts=ts,
                        role="user" if role == "user" else "assistant",
                        text=part["text"],
                    )
                )
            elif pt == "reasoning" and str(part.get("text", "")).strip():
                out.append(
                    Message(id=pid, ts=ts, role="assistant", kind="thinking", text=part["text"])
                )
            elif pt == "tool":
                state = part.get("state") or {}
                out.append(
                    Message(
                        id=pid,
                        ts=ts,
                        role="assistant",
                        kind="tool_use",
                        tool_name=str(part.get("tool", "")),
                        tool_use_id=str(part.get("callID", "")),
                        tool_input=state.get("input")
                        if isinstance(state.get("input"), dict)
                        else None,
                    )
                )
                if state.get("output"):
                    out.append(
                        Message(
                            id=pid + ":r",
                            ts=ts,
                            role="tool",
                            kind="tool_result",
                            tool_use_id=str(part.get("callID", "")),
                            text=str(state["output"])[:4000],
                            is_error=state.get("status") == "error",
                        )
                    )
    return out
