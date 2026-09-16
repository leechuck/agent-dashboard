"""Post a user message into a running Claude Code session through its inbox socket.

Protocol (observed in Claude Code 2.1.27x, see docs/en/cross-session-messaging):
one JSON object per line, then half-close. Optional auth line first.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any


class SocketSendError(RuntimeError):
    pass


async def send_user_message(
    socket_path: Path, text: str, session_id: str = "", sender: str = "agentdash"
) -> dict[str, Any]:
    if not socket_path.exists():
        raise SocketSendError(f"inbox socket missing: {socket_path}")
    frame: dict[str, Any] = {
        "msgV": 1,
        "msg_id": str(uuid.uuid4()),
        "type": "user",
        "message": {"role": "user", "content": text},
        "priority": "next",
        "from": sender,
    }
    if session_id:
        frame["session_id"] = session_id
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(str(socket_path)), 5)
    except (OSError, TimeoutError) as e:
        raise SocketSendError(f"connect failed: {e}") from e
    try:
        writer.write((json.dumps(frame) + "\n").encode())
        await writer.drain()
        writer.write_eof()
        try:
            reply = await asyncio.wait_for(reader.readline(), 5)
        except TimeoutError:
            reply = b""
    finally:
        writer.close()
    if reply:
        try:
            return json.loads(reply.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            return {"raw": reply.decode("utf-8", "replace")}
    return {}
