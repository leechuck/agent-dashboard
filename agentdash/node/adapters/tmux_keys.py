"""Type into an agent that runs inside tmux: the only way to run a slash command remotely.

The text goes in as a bracketed paste (so newlines do not submit early), then Enter.
"""

from __future__ import annotations

import asyncio
import os


class TmuxSendError(Exception):
    pass


def socket_path(socket: str) -> str:
    """`tmux -S` wants a path; a bare name is a server as `tmux -L` names it."""
    if socket and "/" not in socket:
        return f"/tmp/tmux-{os.getuid()}/{socket}"
    return socket


async def _tmux(socket: str, *args: str) -> None:
    socket = socket_path(socket)
    proc = await asyncio.create_subprocess_exec(
        "tmux",
        "-S",
        socket,
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await asyncio.wait_for(proc.communicate(), 10)
    if proc.returncode != 0:
        raise TmuxSendError(err.decode().strip()[:200] or f"tmux exited {proc.returncode}")


async def type_prompt(socket: str, target: str, text: str) -> None:
    if not socket or not target:
        raise TmuxSendError("session is not in tmux")
    try:
        await _tmux(socket, "set-buffer", "-b", "agentdash", "--", text)
        await _tmux(socket, "paste-buffer", "-p", "-d", "-b", "agentdash", "-t", target)
        await asyncio.sleep(0.4)  # let the TUI take the paste before the submit key
        await _tmux(socket, "send-keys", "-t", target, "Enter")
    except (OSError, TimeoutError) as e:
        raise TmuxSendError(str(e) or e.__class__.__name__) from e


# keys the dashboard may press in a pane: enough to answer a menu or a question
KEYS = {"Enter", "Up", "Down", "Left", "Right", "Escape", "Tab", "BSpace", "C-c", "y", "n",
        "1", "2", "3", "4", "5", "6", "7", "8", "9"}  # fmt: skip


async def send_keys(socket: str, target: str, keys: list[str], text: str = "") -> None:
    """Literal text (no Enter) and then named keys, in that order."""
    if not socket or not target:
        raise TmuxSendError("not a tmux pane")
    bad = [k for k in keys if k not in KEYS]
    if bad:
        raise TmuxSendError(f"keys not allowed: {', '.join(bad)}")
    try:
        if text:
            await _tmux(socket, "send-keys", "-t", target, "-l", "--", text)
            await asyncio.sleep(0.2)
        for k in keys:
            await _tmux(socket, "send-keys", "-t", target, k)
            await asyncio.sleep(0.15)
    except (OSError, TimeoutError) as e:
        raise TmuxSendError(str(e) or e.__class__.__name__) from e


async def capture(socket: str, target: str) -> str:
    """What the pane shows now, wrapped lines joined."""
    proc = await asyncio.create_subprocess_exec(
        "tmux", "-S", socket_path(socket), "capture-pane", "-p", "-J", "-t", target,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )  # fmt: skip
    out, err = await asyncio.wait_for(proc.communicate(), 10)
    if proc.returncode != 0:
        raise TmuxSendError(err.decode().strip()[:200] or "no such pane")
    return out.decode("utf-8", "replace")
