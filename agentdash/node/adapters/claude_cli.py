"""Drive the Claude Code CLI for session lifecycle actions on this machine."""

from __future__ import annotations

import asyncio
import os
import re
import signal
from pathlib import Path
from typing import Any

_ID_RE = re.compile(r"backgrounded\s+·\s+(\S+)")


async def _run(args: list[str], config_dir: str | None, cwd: str | None, timeout: float = 60):
    env = dict(os.environ)
    if config_dir:
        env["CLAUDE_CONFIG_DIR"] = config_dir
    proc = await asyncio.create_subprocess_exec(
        "claude",
        *args,
        cwd=cwd or None,
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        return 124, "", "timed out"
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


async def job_action(action: str, job_id: str, config_dir: str | None) -> dict[str, Any]:
    """stop | rm | respawn | logs on a background session id."""
    if action not in ("stop", "rm", "respawn", "logs"):
        return {"ok": False, "error": f"unsupported action {action}"}
    rc, out, err = await _run([action, job_id], config_dir, None)
    return {"ok": rc == 0, "output": (out + err).strip()[-2000:], "rc": rc}


def kill_process(pid: int, hard: bool = False) -> dict[str, Any]:
    try:
        os.kill(pid, signal.SIGKILL if hard else signal.SIGTERM)
    except ProcessLookupError:
        return {"ok": False, "error": "process already gone"}
    except PermissionError:
        return {"ok": False, "error": "not permitted"}
    return {"ok": True}


async def start_background(
    cwd: str,
    prompt: str,
    name: str = "",
    resume: str = "",
    permission_mode: str = "",
    config_dir: str | None = None,
) -> dict[str, Any]:
    path = Path(cwd).expanduser()
    if not path.is_dir():
        return {"ok": False, "error": f"no such directory: {cwd}"}
    args = ["--bg"]
    if name:
        args += ["--name", name]
    if resume:
        args += ["--resume", resume]
    if permission_mode:
        args += ["--permission-mode", permission_mode]
    if prompt:
        args.append(prompt)
    elif not resume:
        return {"ok": False, "error": "a prompt is required"}
    rc, out, err = await _run(args, config_dir, str(path), timeout=90)
    m = _ID_RE.search(out)
    return {
        "ok": rc == 0,
        "job_id": m.group(1) if m else "",
        "output": (out + err).strip()[-2000:],
        "rc": rc,
    }
