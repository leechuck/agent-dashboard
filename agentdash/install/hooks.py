"""Install/uninstall agentdash hooks into Claude Code settings."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

MARK = "agentdash.hooks.claude"

EVENT_HOOKS = ("SessionStart", "SessionEnd", "UserPromptSubmit", "Stop", "Notification")


def hook_command(mode: str) -> str:
    return f"{sys.executable} -m agentdash.hooks.claude {mode}"


def desired_hooks(permission_timeout: int) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for ev in EVENT_HOOKS:
        out[ev] = [{"hooks": [{"type": "command", "command": hook_command("event"), "timeout": 5}]}]
    out["PermissionRequest"] = [
        {
            "hooks": [
                {
                    "type": "command",
                    "command": hook_command("permission"),
                    "timeout": permission_timeout,
                }
            ]
        }
    ]
    return out


def _is_ours(entry: dict[str, Any]) -> bool:
    return any(MARK in h.get("command", "") for h in entry.get("hooks", []))


def install(settings_path: Path, permission_timeout: int = 1800) -> dict[str, Any]:
    settings: dict[str, Any] = {}
    if settings_path.exists():
        shutil.copy2(settings_path, settings_path.with_suffix(".json.bak-agentdash"))
        settings = json.loads(settings_path.read_text() or "{}")
    hooks: dict[str, list[dict[str, Any]]] = settings.get("hooks") or {}
    for ev, entries in desired_hooks(permission_timeout).items():
        kept = [e for e in hooks.get(ev, []) if not _is_ours(e)]
        hooks[ev] = kept + entries
    settings["hooks"] = hooks
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    return settings


def uninstall(settings_path: Path) -> dict[str, Any]:
    if not settings_path.exists():
        return {}
    settings = json.loads(settings_path.read_text() or "{}")
    hooks = settings.get("hooks") or {}
    for ev in list(hooks):
        hooks[ev] = [e for e in hooks[ev] if not _is_ours(e)]
        if not hooks[ev]:
            del hooks[ev]
    if hooks:
        settings["hooks"] = hooks
    else:
        settings.pop("hooks", None)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    return settings
