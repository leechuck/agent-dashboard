"""Codex CLI hook entry points (same protocol as the Claude ones, different node path)."""

from __future__ import annotations

import json
import os
import sys
import urllib.error

from . import claude as base


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else "event"
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        payload = {}
    payload["_pid"] = os.getppid()
    try:
        if mode == "permission":
            timeout = float(os.environ.get("AGENTDASH_HOOK_TIMEOUT", "1780"))
            reply = base._post("/hook/codex/permission", payload, timeout)
            if reply:
                sys.stdout.write(json.dumps(reply))
        else:
            base._post("/hook/codex/event", payload, 3)
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
