"""Parse and tail Claude Code transcript JSONL files into normalized messages."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from ...models import Message

_SKIP_TYPES = {
    "last-prompt",
    "mode",
    "permission-mode",
    "atis-latch",
    "ai-title",
    "file-history-snapshot",
    "queue-operation",
    "attachment",
}


def _ts(rec: dict[str, Any]) -> int | None:
    t = rec.get("timestamp")
    if not t:
        return None
    try:
        return int(datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return ""


_PLUMBING = ("<task-notification", "<local-command", "<command-name", "<command-message")


def _is_plumbing(rtype: str, text: str) -> bool:
    """Harness messages filed under the user's name: task notices, slash-command echoes."""
    return rtype == "user" and text.lstrip().startswith(_PLUMBING)


def parse_record(rec: dict[str, Any]) -> list[Message]:
    """Turn one transcript record into zero or more messages."""
    rtype = rec.get("type")
    if rtype in _SKIP_TYPES or rtype is None:
        return []
    uuid = rec.get("uuid") or rec.get("messageId") or ""
    ts = _ts(rec)
    agent_id = rec.get("agentId", "") if rec.get("isSidechain") else ""
    if rtype == "system":
        text = rec.get("content") or rec.get("subtype") or ""
        if not text or rec.get("isMeta"):
            return []
        # bare subtypes (turn_duration, stop_hook_summary) are bookkeeping, not conversation
        return [
            Message(
                id=uuid,
                ts=ts,
                role="system",
                kind="system",
                text=str(text)[:2000],
                is_meta=not rec.get("content"),
            )
        ]
    if rtype not in ("user", "assistant"):
        return []
    msg = rec.get("message") or {}
    content = msg.get("content")
    out: list[Message] = []
    if isinstance(content, str):
        if content.strip():
            out.append(
                Message(
                    id=uuid,
                    ts=ts,
                    role="user" if rtype == "user" else "assistant",
                    text=content,
                    is_meta=bool(rec.get("isMeta")) or _is_plumbing(rtype, content),
                    agent_id=agent_id,
                )
            )
        return out
    if not isinstance(content, list):
        return out
    for i, block in enumerate(content):
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        bid = f"{uuid}:{i}" if len(content) > 1 else uuid
        if btype == "text":
            text = block.get("text", "")
            if not text.strip():
                continue
            out.append(
                Message(
                    id=bid,
                    ts=ts,
                    role="user" if rtype == "user" else "assistant",
                    text=text,
                    is_meta=bool(rec.get("isMeta")) or _is_plumbing(rtype, text),
                    agent_id=agent_id,
                )
            )
        elif btype == "thinking":
            text = block.get("thinking", "")
            if not text.strip():
                continue
            out.append(
                Message(
                    id=bid, ts=ts, role="assistant", kind="thinking", text=text, agent_id=agent_id
                )
            )
        elif btype == "tool_use":
            out.append(
                Message(
                    id=bid,
                    ts=ts,
                    role="assistant",
                    kind="tool_use",
                    tool_name=str(block.get("name", "")),
                    tool_use_id=str(block.get("id", "")),
                    tool_input=block.get("input") if isinstance(block.get("input"), dict) else None,
                    agent_id=agent_id,
                )
            )
        elif btype == "tool_result":
            text = _content_text(block.get("content"))
            out.append(
                Message(
                    id=bid,
                    ts=ts,
                    role="tool",
                    kind="tool_result",
                    tool_use_id=str(block.get("tool_use_id", "")),
                    text=text[:4000],
                    is_error=bool(block.get("is_error")),
                    agent_id=agent_id,
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


class TranscriptTail:
    """Incremental reader: remembers the byte offset and yields new messages."""

    def __init__(self, path: Path, parser=None) -> None:
        self.path = path
        self.offset = 0
        self._buf = b""
        self._parser = parser or iter_messages

    def read_new(self) -> list[Message]:
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            return []
        if size < self.offset:  # truncated / rewritten
            self.offset = 0
            self._buf = b""
        if size == self.offset:
            return []
        with self.path.open("rb") as f:
            f.seek(self.offset)
            chunk = f.read()
            self.offset = f.tell()
        data = self._buf + chunk
        lines = data.split(b"\n")
        self._buf = lines.pop()  # incomplete last line
        return list(self._parser(line.decode("utf-8", "replace") for line in lines))


def read_last(path: Path, n: int, parser=None, chunk: int = 2_000_000) -> list[Message]:
    """The last n messages, for the first view of a session.

    Long sessions reach tens of megabytes; parsing all of it took seconds. Read from the
    end instead, doubling the window until it holds n messages or the whole file.
    """
    parser = parser or iter_messages
    try:
        size = path.stat().st_size
    except OSError:
        return []
    while True:
        start = max(0, size - chunk)
        try:
            with path.open("rb") as f:
                f.seek(start)
                data = f.read()
        except OSError:
            return []
        lines = data.split(b"\n")
        if start > 0:
            lines = lines[1:]  # the first line is cut in half
        msgs = list(parser(line.decode("utf-8", "replace") for line in lines))
        if len(msgs) >= n or start == 0 or chunk >= 64_000_000:
            return msgs[-n:]
        chunk *= 2


def tail_facts(path: Path, max_bytes: int = 200_000) -> dict[str, Any]:
    """One cheap pass over the tail of a transcript.

    Returns last_line (last assistant text or user prompt), last_user (the most recent
    real user prompt), context_tokens (prompt size of the last main-thread model call,
    which is what fills the context window) and model.
    """
    facts: dict[str, Any] = {
        "last_line": "",
        "last_user": "",
        "context_tokens": 0,
        "model": "",
        "effort": "",
    }
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            f.seek(max(0, size - max_bytes))
            data = f.read()
    except OSError:
        return facts
    raw = data.split(b"\n")[1:] if size > max_bytes else data.split(b"\n")
    lines = [line.decode("utf-8", "replace") for line in raw]
    for m in iter_messages(iter(lines)):
        if m.kind != "text" or m.is_meta or m.agent_id:
            continue
        if m.text.lstrip().startswith(("<local-command", "<command-", "<task-notification")):
            continue  # slash-command plumbing, not something anyone said
        if m.role in ("assistant", "user"):
            facts["last_line"] = m.text
            if m.role == "user":
                facts["last_user"] = m.text
    for line in reversed(lines):
        if '"usage"' not in line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = rec.get("message")
        if rec.get("type") != "assistant" or rec.get("isSidechain") or not isinstance(msg, dict):
            continue
        u = msg.get("usage")
        if not isinstance(u, dict):
            continue
        facts["context_tokens"] = sum(
            int(u.get(k) or 0)
            for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
        )
        facts["model"] = str(msg.get("model") or "")
        facts["effort"] = str(rec.get("effort") or "")
        break
    facts["last_line"] = " ".join(facts["last_line"].split())[:160]
    facts["last_user"] = " ".join(facts["last_user"].split())[:300]
    return facts


_NOT_A_REQUEST = (
    "<local-command",
    "<command-",
    "<task-notification",
    "This session is being continued",
)


def first_request(path: Path, max_bytes: int = 400_000) -> str:
    """The first thing the user asked for: what the session was started to do."""
    try:
        with path.open("rb") as f:
            data = f.read(max_bytes)
    except OSError:
        return ""
    lines = (line.decode("utf-8", "replace") for line in data.split(b"\n")[:-1])
    for m in iter_messages(lines):
        if m.kind == "text" and m.role == "user" and not m.is_meta and not m.agent_id:
            if not m.text.lstrip().startswith(_NOT_A_REQUEST):
                return " ".join(m.text.split())[:300]
    return ""


def last_line_preview(path: Path, max_bytes: int = 200_000) -> str:
    """Cheap preview: last assistant text or user prompt in the tail of the file."""
    return tail_facts(path, max_bytes)["last_line"]
