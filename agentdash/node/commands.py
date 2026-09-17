"""Slash commands a session accepts, so the browser can offer and complete them.

Three sources: what the harness itself provides, the command files the owner or a plugin
installed, and skills (Claude lists those as commands too). Only names and one-line
descriptions leave the machine.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# What each harness answers to. Kept short: the ones worth pressing from a phone.
BUILTIN: dict[str, list[tuple[str, str]]] = {
    "claude": [
        ("compact", "Summarise the conversation and free context"),
        ("clear", "Start over, forgetting this conversation"),
        ("model", "Change the model"),
        ("effort", "Change how hard it thinks"),
        ("usage", "Show plan limits"),
        ("cost", "Show what this session has cost"),
        ("status", "Version, login, working directory"),
        ("context", "Show what fills the context window"),
        ("resume", "Pick another conversation"),
        ("agents", "List the running agents"),
        ("permissions", "Pre-approve or deny tools"),
        ("hooks", "Show and trust hooks"),
        ("mcp", "Manage MCP servers"),
        ("memory", "Edit the memory files"),
        ("init", "Write a CLAUDE.md for this repository"),
        ("review", "Review the current changes"),
        ("login", "Log in to another account"),
        ("logout", "Log out"),
        ("help", "List every command"),
    ],
    "codex": [
        ("model", "Change the model and reasoning effort"),
        ("approvals", "Change what it may do without asking"),
        ("compact", "Summarise the conversation and free context"),
        ("new", "Start a new conversation"),
        ("init", "Write an AGENTS.md for this repository"),
        ("diff", "Show the working tree diff"),
        ("status", "Account, model, token usage"),
        ("mcp", "Manage MCP servers"),
        ("review", "Review the current changes"),
        ("quit", "Leave"),
    ],
    "pi": [
        ("model", "Change the model"),
        ("thinking", "Change the thinking level"),
        ("compact", "Summarise the conversation"),
        ("new", "Start a new session"),
        ("help", "List every command"),
    ],
    "opencode": [
        ("model", "Change the model"),
        ("new", "Start a new session"),
        ("help", "List every command"),
    ],
}

_FRONT = re.compile(r"^---\n(.*?)\n---", re.S)


@dataclass
class Command:
    name: str  # without the slash
    description: str = ""
    source: str = "builtin"  # builtin | user | project | plugin | skill
    args: str = ""  # a hint such as "<level>" when the command takes one


def _describe(path: Path) -> tuple[str, str]:
    """Description and argument hint of a command or skill file, from its front matter."""
    try:
        text = path.read_text(errors="replace")[:4000]
    except OSError:
        return "", ""
    desc, args = "", ""
    m = _FRONT.search(text)
    if m:
        for line in m.group(1).splitlines():
            key, _, value = line.partition(":")
            value = value.strip().strip("\"'")
            if key.strip() == "description" and value:
                desc = value
            elif key.strip() in ("argument-hint", "argument_hint") and value:
                args = value
        text = text[m.end() :]
    if not desc:
        for line in text.splitlines():
            line = line.strip().lstrip("# ").strip()
            if line:
                desc = line
                break
    return " ".join(desc.split())[:140], args


def _from_dir(folder: Path, source: str, prefix: str = "") -> list[Command]:
    out = []
    try:
        files = sorted(folder.rglob("*.md"))
    except OSError:
        return out
    for f in files:
        rel = f.relative_to(folder).with_suffix("")
        name = prefix + ":".join(rel.parts)
        desc, args = _describe(f)
        out.append(Command(name=name, description=desc, source=source, args=args))
    return out


def _plugins(home: Path) -> list[Command]:
    """Commands of the plugins that are actually installed, not everything a marketplace
    happens to carry. Their install paths are in the registry Claude keeps."""
    out: list[Command] = []
    try:
        data = json.loads((home / ".claude" / "plugins" / "installed_plugins.json").read_text())
    except (OSError, json.JSONDecodeError):
        return out
    for full, entries in (data.get("plugins") or {}).items():
        name = str(full).split("@")[0]
        for entry in entries if isinstance(entries, list) else []:
            path = entry.get("installPath")
            if path:
                out += _from_dir(Path(path) / "commands", "plugin", prefix=f"{name}:")
    return out


def _skills(config_dir: Path, cwd: Path) -> list[Command]:
    out = []
    for base, source in ((config_dir / "skills", "skill"), (cwd / ".claude" / "skills", "skill")):
        try:
            folders = sorted(d for d in base.iterdir() if d.is_dir())
        except OSError:
            continue
        for d in folders:
            f = d / "SKILL.md"
            if f.exists():
                desc, _ = _describe(f)
                out.append(Command(name=d.name, description=desc, source=source))
    return out


def for_session(harness: str, cwd: str, config_dir: str = "") -> list[dict[str, Any]]:
    """Everything this session would accept after a slash, most useful first."""
    home = Path.home()
    work = Path(cwd).expanduser() if cwd else home
    found: list[Command] = [
        Command(name=n, description=d) for n, d in BUILTIN.get(harness, BUILTIN["claude"])
    ]
    if harness == "claude":
        cfg = Path(config_dir).expanduser() if config_dir else home / ".claude"
        found += _from_dir(cfg / "commands", "user")
        found += _from_dir(work / ".claude" / "commands", "project")
        found += _plugins(home)
        found += _skills(cfg, work)
    elif harness == "codex":
        found += _from_dir(home / ".codex" / "prompts", "user")
    elif harness == "pi":
        found += _from_dir(home / ".pi" / "agent" / "commands", "user")
    rank = {"builtin": 0, "project": 1, "user": 2, "skill": 3, "plugin": 4}
    seen: set[str] = set()
    out = []
    for c in sorted(found, key=lambda c: (rank.get(c.source, 9), c.name)):
        if c.name and c.name not in seen:
            seen.add(c.name)
            out.append(asdict(c))
    return out
