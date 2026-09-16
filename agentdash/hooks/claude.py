"""Claude Code hook entry points. Standard library only: this must start fast.

Usage (installed into ~/.claude/settings.json by `agentdash install hooks`):
    python -m agentdash.hooks.claude event        # SessionStart, Stop, Notification, ...
    python -m agentdash.hooks.claude permission   # PermissionRequest (may block)

Reads the hook JSON from stdin, posts it to the local node, prints the node's
reply (if any) so Claude Code can act on it. Any failure is silent: the hook
must never break a session.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

NODE_URL = os.environ.get("AGENTDASH_NODE_URL", "http://127.0.0.1:8791")
ENV_KEYS = (
    "CLAUDE_CODE_BRIDGE_SESSION_ID",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CONFIG_DIR",
    "CLAUDE_CODE_REMOTE",
)


def _post(path: str, body: dict, timeout: float) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        NODE_URL + path, data=data, headers={"content-type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        raw = resp.read()
    return json.loads(raw) if raw else {}


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else "event"
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        payload = {}
    payload["_env"] = {k: os.environ.get(k, "") for k in ENV_KEYS}
    payload["_pid"] = os.getppid()
    if mode == "permission":
        timeout = float(os.environ.get("AGENTDASH_HOOK_TIMEOUT", "1780"))
        try:
            reply = _post("/hook/claude/permission", payload, timeout)
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            return 0
        if reply:
            sys.stdout.write(json.dumps(reply))
        return 0
    try:
        _post("/hook/claude/event", payload, 3)
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
