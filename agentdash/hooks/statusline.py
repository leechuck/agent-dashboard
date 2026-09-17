"""Statusline sidecar: record Claude's rate_limits and context window, then run the original.

Installed as `statusLine.command` in ~/.claude/settings.json by
`agentdash install statusline`; the original command is kept as the argument.
Standard library only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def main(argv: list[str]) -> int:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        data = {}
    rl = data.get("rate_limits")
    if isinstance(rl, dict) and rl:
        state = Path(os.environ.get("AGENTDASH_STATE_DIR", Path.home() / ".agentdash"))
        cfg = os.environ.get("CLAUDE_CONFIG_DIR", "")
        suffix = "" if not cfg or Path(cfg).name == ".claude" else "-" + Path(cfg).name
        out = state / f"claude-rate-limits{suffix}.json"
        try:
            state.mkdir(parents=True, exist_ok=True)
            tmp = out.with_suffix(".tmp")
            tmp.write_text(json.dumps({"rate_limits": rl, "_written": int(time.time() * 1000)}))
            os.replace(tmp, out)
        except OSError:
            pass
    cw, sid = data.get("context_window"), str(data.get("session_id") or "")
    if isinstance(cw, dict) and sid and "/" not in sid:
        state = Path(os.environ.get("AGENTDASH_STATE_DIR", Path.home() / ".agentdash"))
        out = state / "claude-context" / f"{sid}.json"
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(
                    {
                        "context_window_size": cw.get("context_window_size"),
                        "used_percentage": cw.get("used_percentage"),
                        "effort": (data.get("effort") or {}).get("level"),
                        "thinking": (data.get("thinking") or {}).get("enabled"),
                        "fast_mode": data.get("fast_mode"),
                        "model_name": (data.get("model") or {}).get("display_name"),
                        "cost_usd": (data.get("cost") or {}).get("total_cost_usd"),
                        "_written": int(time.time() * 1000),
                    }
                )
            )
            os.replace(tmp, out)
        except OSError:
            pass
    original = argv[1:]
    if original:
        try:
            r = subprocess.run(original, input=raw, text=True, capture_output=True, timeout=10)
            sys.stdout.write(r.stdout)
        except (OSError, subprocess.SubprocessError):
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
