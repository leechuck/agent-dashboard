"""Parse Codex CLI rollout JSONL files into normalized messages and status."""

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


def _texts(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") in (
                "input_text",
                "output_text",
                "text",
                "summary_text",
            ):
                parts.append(str(b.get("text", "")))
        return "\n".join(parts)
    return ""


def parse_record(rec: dict[str, Any]) -> list[Message]:
    rtype = rec.get("type")
    p = rec.get("payload")
    if rtype != "response_item" or not isinstance(p, dict):
        return []
    ptype = p.get("type")
    ts = _ts(rec)
    rid = str(p.get("id") or p.get("call_id") or f"{rec.get('ordinal', '')}")
    if ptype == "message":
        role = p.get("role", "")
        text = _texts(p.get("content"))
        if not text.strip():
            return []
        if role == "developer":
            return []
        if role == "user":
            meta = text.lstrip().startswith("<")  # environment/context blobs
            return [Message(id=rid, ts=ts, role="user", text=text[:20000], is_meta=meta)]
        return [Message(id=rid, ts=ts, role="assistant", text=text[:20000])]
    if ptype == "reasoning":
        summ = p.get("summary")
        text = _texts(summ) if isinstance(summ, list) else str(summ or "")
        if not text.strip():
            return []
        return [Message(id=rid, ts=ts, role="assistant", kind="thinking", text=text[:8000])]
    if ptype in ("function_call", "custom_tool_call"):
        args = p.get("arguments") if ptype == "function_call" else p.get("input")
        tool_input: dict[str, Any] | None
        if isinstance(args, str):
            try:
                parsed = json.loads(args)
                tool_input = parsed if isinstance(parsed, dict) else {"input": args}
            except json.JSONDecodeError:
                tool_input = {"input": args[:4000]}
        elif isinstance(args, dict):
            tool_input = args
        else:
            tool_input = None
        return [
            Message(
                id=rid,
                ts=ts,
                role="assistant",
                kind="tool_use",
                tool_name=str(p.get("name", "")),
                tool_use_id=str(p.get("call_id", "")),
                tool_input=tool_input,
            )
        ]
    if ptype in ("function_call_output", "custom_tool_call_output"):
        out = p.get("output")
        text = _texts(out) if isinstance(out, list) else str(out or "")
        return [
            Message(
                id=rid,
                ts=ts,
                role="tool",
                kind="tool_result",
                tool_use_id=str(p.get("call_id", "")),
                text=text[:4000],
            )
        ]
    return []


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


def scan_status(lines: Iterator[str]) -> dict[str, Any]:
    """Return {cwd, busy, last_agent_message, last_user, context_*, last_ts} from a rollout tail."""
    info: dict[str, Any] = {
        "cwd": "",
        "busy": False,
        "last_agent_message": "",
        "last_user": "",
        "context_tokens": 0,
        "context_window": 0,
        "last_ts": None,
    }
    for line in lines:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        t, p = rec.get("type"), rec.get("payload")
        if not isinstance(p, dict):
            continue
        if t == "session_meta":
            info["cwd"] = p.get("cwd", "")
        elif t == "event_msg":
            et = p.get("type")
            if et == "task_started":
                info["busy"] = True
            elif et in ("task_complete", "turn_aborted", "error"):
                info["busy"] = False
                if p.get("last_agent_message"):
                    info["last_agent_message"] = str(p["last_agent_message"])
            elif et == "user_message" and p.get("message"):
                info["last_user"] = " ".join(str(p["message"]).split())[:300]
            elif et == "token_count" and isinstance(p.get("info"), dict):
                last = p["info"].get("last_token_usage") or {}
                info["context_tokens"] = int(last.get("total_tokens") or 0)
                info["context_window"] = int(p["info"].get("model_context_window") or 0)
        elif t == "turn_context" and p.get("cwd"):
            info["cwd"] = p["cwd"]
        ts = _ts(rec)
        if ts:
            info["last_ts"] = ts
    return info
