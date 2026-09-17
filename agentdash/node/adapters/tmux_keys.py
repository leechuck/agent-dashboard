"""Type into an agent that runs inside tmux: the only way to run a slash command remotely.

The text goes in as a bracketed paste (so newlines do not submit early), then Enter.
"""

from __future__ import annotations

import asyncio


class TmuxSendError(Exception):
    pass


async def _tmux(socket: str, *args: str) -> None:
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
