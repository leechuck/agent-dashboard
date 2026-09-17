"""tmux panes on this machine: which agent runs where, for attach and terminal access."""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ...models import Harness, Session, SessionStatus, now_ms
from . import procs

log = logging.getLogger(__name__)


@dataclass
class Pane:
    socket: str
    target: str  # session:window.pane
    pid: int
    command: str
    path: str
    agent: str = ""
    agent_pid: int = 0


def sockets() -> list[Path]:
    d = Path(f"/tmp/tmux-{os.getuid()}")
    return sorted(p for p in d.iterdir() if p.is_socket()) if d.exists() else []


def list_panes() -> list[Pane]:
    panes: list[Pane] = []
    fmt = (
        "#{session_name}:#{window_index}.#{pane_index}\t#{pane_pid}"
        "\t#{pane_current_command}\t#{pane_current_path}"
    )
    for sock in sockets():
        try:
            r = subprocess.run(
                ["tmux", "-S", str(sock), "list-panes", "-a", "-F", fmt],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if r.returncode != 0:
            continue
        for line in r.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 4:
                continue
            try:
                pid = int(parts[1])
            except ValueError:
                continue
            pane = Pane(socket=str(sock), target=parts[0], pid=pid, command=parts[2], path=parts[3])
            found = procs.agent_in_tree(pid)
            if found:
                pane.agent, pane.agent_pid = found
            panes.append(pane)
    return panes


def capture_tail(sock: str, target: str, lines: int = 3) -> str:
    try:
        r = subprocess.run(
            ["tmux", "-S", sock, "capture-pane", "-p", "-t", target],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    out = [ln for ln in r.stdout.splitlines() if ln.strip()]
    return " ⏎ ".join(out[-lines:])[:160]


class TmuxCollector:
    def __init__(self, machine: str) -> None:
        self.machine = machine

    def annotate(self, sessions: list[Session]) -> list[Session]:
        """Attach tmux coordinates to known sessions; add pane-only sessions for the rest."""
        panes = list_panes()
        if not panes:
            return sessions
        by_pid: dict[int, Pane] = {p.agent_pid: p for p in panes if p.agent_pid}
        claimed: set[int] = set()
        for s in sessions:
            pane = by_pid.get(s.pid or -1)
            if pane is None and s.pid:
                # the agent pid we know may be a child of the pane's agent process
                for cand in panes:
                    if cand.agent_pid and s.pid in procs.descendants(cand.agent_pid, 3):
                        pane = cand
                        break
            if pane:
                s.extra["tmux"] = {"socket": pane.socket, "target": pane.target}
                claimed.add(pane.agent_pid)
        for pane in panes:
            if not pane.agent or pane.agent_pid in claimed:
                continue
            sid = f"{Path(pane.socket).name}:{pane.target}"
            sessions.append(
                Session(
                    key=Session.make_key(self.machine, Harness.tmux, sid),
                    machine=self.machine,
                    harness=Harness.tmux,
                    provider=pane.agent,
                    session_id=sid,
                    name=f"{pane.agent} in {pane.target}",
                    cwd=pane.path,
                    kind="interactive",
                    status=SessionStatus.busy,
                    pid=pane.agent_pid,
                    started_at=procs.start_ms(pane.agent_pid),
                    updated_at=now_ms(),
                    last_line=capture_tail(pane.socket, pane.target),
                    extra={
                        "tmux": {"socket": pane.socket, "target": pane.target},
                        "agent": pane.agent,
                    },
                )
            )
        return sessions
