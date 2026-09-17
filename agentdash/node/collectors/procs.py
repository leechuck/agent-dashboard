"""Process helpers shared by collectors (Linux /proc)."""

from __future__ import annotations

import os
from pathlib import Path


def cmdline(pid: int) -> list[str]:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return []
    return [a.decode(errors="replace") for a in raw.split(b"\0") if a]


def cwd_of(pid: int) -> str:
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return ""


def start_ms(pid: int) -> int | None:
    try:
        st = os.stat(f"/proc/{pid}")
        return int(st.st_ctime * 1000)
    except OSError:
        return None


def ppid(pid: int) -> int:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        return int(stat.rsplit(")", 1)[1].split()[1])
    except (OSError, ValueError, IndexError):
        return 0


def env_of(pid: int, name: str) -> str:
    """One variable from a process' initial environment (what its parent gave it)."""
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return ""
    prefix = name.encode() + b"="
    for item in raw.split(b"\0"):
        if item.startswith(prefix):
            return item[len(prefix) :].decode(errors="replace")
    return ""


def ancestors(pid: int, depth: int = 12) -> list[int]:
    out: list[int] = []
    for _ in range(depth):
        pid = ppid(pid)
        if pid <= 1:
            break
        out.append(pid)
    return out


def children(pid: int) -> list[int]:
    try:
        raw = Path(f"/proc/{pid}/task/{pid}/children").read_text().split()
        return [int(x) for x in raw]
    except (OSError, ValueError):
        return []


def descendants(pid: int, depth: int = 6) -> list[int]:
    out: list[int] = []
    frontier = [pid]
    for _ in range(depth):
        nxt: list[int] = []
        for p in frontier:
            for c in children(p):
                out.append(c)
                nxt.append(c)
        frontier = nxt
        if not frontier:
            break
    return out


def comm(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/comm").read_text().strip()
    except OSError:
        return ""


def find(names: set[str]) -> list[int]:
    """Pids whose argv[0] basename (or comm) is one of names."""
    out = []
    for d in Path("/proc").iterdir():
        if not d.name.isdigit():
            continue
        pid = int(d.name)
        argv = cmdline(pid)
        base = os.path.basename(argv[0]) if argv else comm(pid)
        if base in names:
            out.append(pid)
    return out


AGENT_BINARIES = {"claude", "codex", "pi", "opencode", "hermes", "gemini", "aider"}


def agent_in_tree(pid: int) -> tuple[str, int] | None:
    """First agent binary found under a process (e.g. a shell in a tmux pane)."""
    for p in [pid, *descendants(pid)]:
        argv = cmdline(p)
        base = os.path.basename(argv[0]) if argv else comm(p)
        if base in AGENT_BINARIES:
            # `codex app-server`, `opencode acp` etc. are servers, not sessions
            if base == "codex" and len(argv) > 1 and argv[1] in ("app-server", "exec-server"):
                continue
            return base, p
        if base.startswith("node") and len(argv) > 1:
            b1 = os.path.basename(argv[1])
            if b1 in AGENT_BINARIES:
                return b1, p
        if base.startswith("python") and any("hermes_cli" in a for a in argv[1:4]):
            if "gateway" in argv:  # the messaging gateway is a service, not a session
                continue
            return "hermes", p
    return None
