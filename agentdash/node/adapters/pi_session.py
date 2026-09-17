"""Parse pi coding agent session JSONL (tree of entries) into normalized messages."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from typing import Any

from ...models import Message


def _ts(rec: dict[str, Any]) -> int | None:
    t = rec.get("timestamp")
    if not t:
        return None
    try:
        return int(datetime.fromisoformat(str(t).replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def parse_record(rec: dict[str, Any]) -> list[Message]:
    if rec.get("type") != "message":
        return []
    m = rec.get("message") or {}
    role = m.get("role", "")
    rid = str(rec.get("id", ""))
    ts = _ts(rec)
    content = m.get("content")
    out: list[Message] = []
    if role == "toolResult":
        text = ""
        if isinstance(content, list):
            text = "\n".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
        elif isinstance(content, str):
            text = content
        out.append(
            Message(
                id=rid,
                ts=ts,
                role="tool",
                kind="tool_result",
                tool_name=str(m.get("toolName", "")),
                tool_use_id=str(m.get("toolCallId", "")),
                text=text[:4000],
                is_error=bool(m.get("isError")),
            )
        )
        return out
    if isinstance(content, str):
        if content.strip():
            out.append(
                Message(id=rid, ts=ts, role="user" if role == "user" else "assistant", text=content)
            )
        return out
    if not isinstance(content, list):
        return out
    for i, b in enumerate(content):
        if not isinstance(b, dict):
            continue
        bid = f"{rid}:{i}"
        bt = b.get("type")
        if bt == "text" and str(b.get("text", "")).strip():
            out.append(
                Message(
                    id=bid, ts=ts, role="user" if role == "user" else "assistant", text=b["text"]
                )
            )
        elif bt == "thinking" and str(b.get("thinking", "")).strip():
            out.append(
                Message(id=bid, ts=ts, role="assistant", kind="thinking", text=b["thinking"])
            )
        elif bt == "toolCall":
            args = b.get("arguments") or b.get("input")
            out.append(
                Message(
                    id=bid,
                    ts=ts,
                    role="assistant",
                    kind="tool_use",
                    tool_name=str(b.get("name", "")),
                    tool_use_id=str(b.get("id", "")),
                    tool_input=args
                    if isinstance(args, dict)
                    else ({"input": args} if args else None),
                )
            )
    return out


def iter_messages(lines: Iterator[str]) -> Iterator[Message]:
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        yield from parse_record(rec)


def session_header(path) -> dict[str, Any]:
    """First record of a pi session file: id, cwd, timestamp."""
    try:
        with open(path, encoding="utf-8") as f:
            first = f.readline()
        rec = json.loads(first)
        return rec if rec.get("type") == "session" else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
