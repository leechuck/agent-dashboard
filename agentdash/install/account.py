"""Set up a second Claude Code login next to ~/.claude (e.g. a team seat).

Claude Code keeps one login per config directory. A sibling directory with its own
credentials, sharing instructions, skills and transcripts through symlinks, lets two
subscriptions run side by side; `claude-<name>` starts Claude on it. Sharing `projects`
means a conversation started on one login can be resumed on the other
(`claude-<name> --resume <id>`), which is how work moves when one plan runs low.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

SHARED = ("CLAUDE.md", "skills", "agents", "commands", "projects")

WRAPPER = """#!/usr/bin/env bash
# claude-{name}: Claude Code on the "{name}" login (config in ~/.claude-{name}).
# Written by `agentdash install account {name}`. Log in once with /login.
set -euo pipefail
export CLAUDE_CONFIG_DIR="$HOME/.claude-{name}"
exec "{claude}" "$@"
"""


def link_shared(base: Path, target: Path) -> list[str]:
    """Make `target` a Claude config dir that shares instructions, skills and transcripts
    with `base` but keeps its own login and sessions. Safe to call again."""
    done: list[str] = []
    target.mkdir(mode=0o700, exist_ok=True)
    for item in SHARED:
        src, dst = base / item, target / item
        if src.exists() and not dst.exists() and not dst.is_symlink():
            dst.symlink_to(src)
            done.append(f"linked {dst} -> {src}")
    settings = target / "settings.json"
    if (base / "settings.json").exists() and not settings.exists():
        shutil.copy2(base / "settings.json", settings)
        done.append(f"copied settings (hooks, status line) to {settings}")
    return done


def install_account(name: str, home: Path | None = None, bin_dir: Path | None = None) -> list[str]:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,30}", name):
        raise ValueError("name must be lowercase letters, digits, - or _")
    home = home or Path.home()
    base, target = home / ".claude", home / f".claude-{name}"
    bin_dir = bin_dir or home / ".local" / "bin"
    done = link_shared(base, target)
    claude = shutil.which("claude") or str(bin_dir / "claude")
    wrapper = bin_dir / f"claude-{name}"
    if not wrapper.exists():
        bin_dir.mkdir(parents=True, exist_ok=True)
        wrapper.write_text(WRAPPER.format(name=name, claude=claude))
        wrapper.chmod(0o755)
        done.append(f"wrote {wrapper}")
    return done
