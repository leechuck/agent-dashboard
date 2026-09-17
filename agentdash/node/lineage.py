"""Which session started which: sub-agents are listed under the agent that spawned them.

Two signals, both from /proc. Claude Code exports CLAUDE_CODE_SESSION_ID to everything it
runs, so a child agent's initial environment names its parent even after nohup/setsid.
For other harnesses the process tree is used: an agent whose ancestor is another
session's process was started by that session.
"""

from __future__ import annotations

from collections.abc import Callable

from ..models import Session
from .collectors import procs


def link_parents(
    sessions: list[Session],
    known: dict[str, str],
    env_of: Callable[[int, str], str] = procs.env_of,
    ancestors: Callable[[int], list[int]] = procs.ancestors,
) -> None:
    """Set extra["parent"] (a session key). `known` remembers links after a process exits."""
    by_sid = {s.session_id: s for s in sessions if s.harness != "tmux"}
    by_pid = {s.pid: s for s in sessions if s.pid and s.harness != "tmux"}
    for s in sessions:
        if s.harness == "tmux" or s.extra.get("parent"):
            continue  # Codex names the parent of its own sub-agents
        parent: Session | None = None
        if s.pid:
            sid = env_of(s.pid, "CLAUDE_CODE_SESSION_ID")
            if sid and sid != s.session_id:
                parent = by_sid.get(sid)
            if parent is None:
                for pid in ancestors(s.pid):
                    cand = by_pid.get(pid)
                    if cand is not None and cand.key != s.key:
                        parent = cand
                        break
        if parent is not None and parent.key != s.key:
            known[s.key] = parent.key
        if s.key in known:
            s.extra["parent"] = known[s.key]
    live = {s.key for s in sessions}
    for key in [k for k in known if k not in live]:
        del known[key]
