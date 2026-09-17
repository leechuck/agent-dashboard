"""`agentdash doctor`: check this machine's wiring and print a short report."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

from .config import Settings


@dataclass
class Check:
    name: str
    ok: bool | None  # None = not applicable / info
    detail: str = ""

    def line(self) -> str:
        mark = {True: "ok  ", False: "FAIL", None: "info"}[self.ok]
        return f"{mark}  {self.name:34} {self.detail}"


def _hooks_installed(settings_path: Path, mark: str) -> tuple[bool, str]:
    try:
        d = json.loads(settings_path.read_text())
    except (OSError, json.JSONDecodeError):
        return False, f"{settings_path} unreadable"
    hooks = d.get("hooks") or {}
    have = [
        ev
        for ev, entries in hooks.items()
        if any(mark in h.get("command", "") for e in entries for h in e.get("hooks", []))
    ]
    return bool(have), ", ".join(sorted(have)) or "none"


def run(s: Settings) -> list[Check]:
    out: list[Check] = []
    env = s.state_dir / ".env"
    out.append(Check("env file", env.exists(), str(env)))
    out.append(Check("node token", bool(s.node_token), "set" if s.node_token else "missing"))
    out.append(Check("web token", bool(s.web_token), "set" if s.web_token else "missing"))

    # node
    try:
        r = httpx.get(f"http://{s.node_host}:{s.node_port}/healthz", timeout=3)
        j = r.json()
        out.append(
            Check(
                "node running",
                r.status_code == 200,
                f"armed={j.get('armed')} pending={j.get('pending')}",
            )
        )
    except httpx.HTTPError:
        out.append(
            Check(
                "node running",
                False,
                f"nothing on {s.node_host}:{s.node_port}; systemctl --user status agentdash-node",
            )
        )

    # hub
    hub_http = (
        s.hub_url.replace("wss://", "https://").replace("ws://", "http://").rsplit("/nodes", 1)[0]
    )
    try:
        r = httpx.get(f"{hub_http}/healthz", timeout=8)
        nodes = r.json().get("nodes", [])
        out.append(Check("hub reachable", r.status_code == 200, f"{hub_http} nodes={nodes}"))
        out.append(Check("this node linked", s.machine_id in nodes, s.machine_id))
    except httpx.HTTPError as e:
        out.append(Check("hub reachable", False, f"{hub_http}: {e.__class__.__name__}"))

    # harness wiring
    for d in s.claude_config_dirs:
        if not d.exists():
            continue
        ok, detail = _hooks_installed(d / "settings.json", "agentdash.hooks.claude")
        out.append(Check(f"claude hooks ({d.name})", ok, detail))
        try:
            sl = (json.loads((d / "settings.json").read_text()).get("statusLine") or {}).get(
                "command", ""
            )
        except (OSError, json.JSONDecodeError):
            sl = ""
        out.append(
            Check(
                f"claude statusline sidecar ({d.name})",
                "agentdash.hooks.statusline" in sl,
                "installed"
                if "agentdash.hooks.statusline" in sl
                else "run: agentdash install statusline",
            )
        )
    codex_hooks = Path.home() / ".codex" / "hooks.json"
    if shutil.which("codex"):
        ok, detail = (
            _hooks_installed(codex_hooks, "agentdash.hooks.codex")
            if codex_hooks.exists()
            else (False, "run: agentdash install codex")
        )
        out.append(
            Check("codex hooks", ok, detail + " (trust with /hooks in Codex)" if ok else detail)
        )
    if shutil.which("pi"):
        ext = Path.home() / ".pi" / "agent" / "extensions" / "agentdash.ts"
        out.append(
            Check(
                "pi extension",
                ext.exists(),
                str(ext) if ext.exists() else "run: agentdash install pi",
            )
        )
    out.append(
        Check(
            "tmux",
            shutil.which("tmux") is not None,
            shutil.which("tmux") or "not installed; terminals need it",
        )
    )

    # tailscale
    if shutil.which("tailscale"):
        try:
            r = subprocess.run(
                ["tailscale", "status", "--json"], capture_output=True, text=True, timeout=10
            )
            st = json.loads(r.stdout or "{}")
            self_ = st.get("Self") or {}
            out.append(
                Check(
                    "tailscale",
                    st.get("BackendState") == "Running",
                    f"{self_.get('DNSName', '')} {st.get('BackendState', '')}",
                )
            )
        except (OSError, json.JSONDecodeError, subprocess.SubprocessError):
            out.append(Check("tailscale", None, "status unavailable (needs operator or sudo)"))
    else:
        out.append(Check("tailscale", None, "not installed"))

    # history (only meaningful where the hub runs)
    if s.history_url:
        try:
            r = httpx.get(
                f"{s.history_url.rstrip('/')}/api/v1/machines",
                timeout=8,
                headers={"Host": httpx.URL(s.history_url).netloc.decode()},
            )
            machines = r.json().get("machines") if r.status_code == 200 else r.status_code
            out.append(
                Check(
                    "history (agentsview)",
                    r.status_code == 200,
                    f"{s.history_url} machines={machines}",
                )
            )
        except httpx.HTTPError as e:
            out.append(
                Check(
                    "history (agentsview)",
                    None,
                    f"{s.history_url}: {e.__class__.__name__} (fine on a node-only machine)",
                )
            )
    return out


def main(s: Settings) -> int:
    checks = run(s)
    for c in checks:
        print(c.line())
    bad = [c for c in checks if c.ok is False]
    print(f"\n{len(bad)} problem(s)" if bad else "\nall good")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(Settings()))
