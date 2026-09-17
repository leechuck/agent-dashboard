"""Start (or restart) an agent of any harness, on any login or endpoint, with any model.

Everything interactive runs in a detached tmux session: that is the one channel through
which the dashboard can type, show the terminal and run slash commands, whatever the
harness. Claude can alternatively run as its own background job.

Secrets never appear on a command line: the environment for the new process goes through
a 0600 file that the wrapper shell reads and deletes before it execs the agent.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import shlex
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import Settings
from ..install.account import link_shared
from .adapters.claude_cli import start_background

HARNESSES = ("claude", "codex", "pi", "opencode")
PERMISSIONS = ("default", "plan", "acceptEdits", "bypass")
_SLUG = re.compile(r"[^a-z0-9]+")


class LaunchError(Exception):
    pass


@dataclass
class Endpoint:
    """A model server the owner configured. `key_env` names the variable that holds the
    key on the node (in ~/.agentdash/.env); the key itself is never part of this record."""

    id: str
    name: str = ""
    base_url: str = ""  # OpenAI-compatible, usually ends in /v1
    anthropic_base_url: str = ""  # root that serves /v1/messages, for Claude Code
    key_env: str = ""
    wire_api: str = "chat"  # what Codex should speak: chat | responses
    context_window: int = 0
    models: list[str] = field(default_factory=list)  # fixed list; empty = ask the server

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> Endpoint:
        known = {k: raw[k] for k in cls.__dataclass_fields__ if k in raw and raw[k] is not None}
        e = cls(**known)
        e.id = _SLUG.sub("-", str(e.id).lower()).strip("-")
        if not e.id:
            raise LaunchError("an endpoint needs an id")
        return e


@dataclass
class LaunchSpec:
    harness: str = "claude"
    cwd: str = ""
    prompt: str = ""
    name: str = ""
    backend: str = "default"  # default | login | endpoint
    login: str = ""  # Claude config dir name (".claude-team")
    endpoint: str = ""  # endpoint id
    model: str = ""
    effort: str = ""
    permissions: str = "default"
    mode: str = "tmux"  # tmux | background (Claude only)
    resume: str = ""  # session id to continue

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> LaunchSpec:
        spec = cls(**{k: str(raw[k]) for k in cls.__dataclass_fields__ if raw.get(k)})
        if raw.get("config_dir") and not spec.login:  # older clients
            spec.backend, spec.login = "login", str(raw["config_dir"])
        if raw.get("permission_mode") and spec.permissions == "default":
            spec.permissions = str(raw["permission_mode"])
        if spec.harness not in HARNESSES:
            raise LaunchError(f"unknown harness {spec.harness}")
        if spec.permissions not in PERMISSIONS:
            raise LaunchError(f"unknown permission mode {spec.permissions}")
        return spec


def secret(s: Settings, name: str) -> str:
    """A key by variable name: the node's environment, else ~/.agentdash/.env read fresh
    (so a key added a minute ago works without restarting the node)."""
    if not name:
        return ""
    if os.environ.get(name):
        return os.environ[name]
    try:
        for line in (s.state_dir / ".env").read_text().splitlines():
            k, _, v = line.partition("=")
            if k.strip() == name:
                return v.strip().strip("'\"")
    except OSError:
        pass
    return ""


def _endpoint(spec: LaunchSpec, endpoints: list[Endpoint], s: Settings) -> tuple[Endpoint, str]:
    ep = next((e for e in endpoints if e.id == spec.endpoint), None)
    if ep is None:
        raise LaunchError(f"endpoint {spec.endpoint} is not configured")
    key = secret(s, ep.key_env)
    if ep.key_env and not key:
        raise LaunchError(f"{ep.key_env} is not set on this machine (add it to ~/.agentdash/.env)")
    if not spec.model:
        raise LaunchError("choose a model for this endpoint")
    return ep, key


def claude_config_dir(s: Settings, name: str) -> Path:
    known = {d.name: d for d in s.claude_config_dirs}
    if name in known:
        return known[name]
    raise LaunchError(f"no Claude login called {name} on this machine")


def build(
    spec: LaunchSpec, s: Settings, endpoints: list[Endpoint]
) -> tuple[list[str], dict[str, str]]:
    """The command line and the extra environment for one launch."""
    exe = shutil.which(spec.harness)
    if not exe:
        raise LaunchError(f"{spec.harness} is not installed on this machine")
    env: dict[str, str] = {}
    argv = [exe]
    if spec.harness == "claude":
        if spec.backend == "endpoint":
            ep, key = _endpoint(spec, endpoints, s)
            if not ep.anthropic_base_url:
                raise LaunchError(
                    f"{ep.name or ep.id} has no Anthropic-compatible address for Claude Code"
                )
            home = Path.home() / f".claude-{ep.id}"
            link_shared(Path.home() / ".claude", home)
            env |= {
                "CLAUDE_CONFIG_DIR": str(home),
                "ANTHROPIC_BASE_URL": ep.anthropic_base_url,
                "ANTHROPIC_AUTH_TOKEN": key,
                "ANTHROPIC_API_KEY": "",
                "ANTHROPIC_MODEL": spec.model,
                "ANTHROPIC_SMALL_FAST_MODEL": spec.model,
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            }
            for tier in ("OPUS", "SONNET", "HAIKU"):
                env[f"ANTHROPIC_DEFAULT_{tier}_MODEL"] = spec.model
            if ep.context_window:
                env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = str(ep.context_window)
        elif spec.backend == "login" and spec.login:
            env["CLAUDE_CONFIG_DIR"] = str(claude_config_dir(s, spec.login))
        if spec.resume:
            argv += ["--resume", spec.resume]
        if spec.model and spec.backend != "endpoint":
            argv += ["--model", spec.model]
        if spec.effort:
            argv += ["--effort", spec.effort]
        if spec.permissions == "bypass":
            argv.append("--dangerously-skip-permissions")
        elif spec.permissions != "default":
            argv += ["--permission-mode", spec.permissions]
        if spec.name:
            argv += ["--name", spec.name]
    elif spec.harness == "codex":
        if spec.resume:
            argv += ["resume", spec.resume]
        if spec.backend == "endpoint":
            ep, key = _endpoint(spec, endpoints, s)
            p = f"model_providers.{ep.id}"
            argv += [
                "-c", f'model_provider="{ep.id}"',
                "-c", f'{p}.name="{ep.name or ep.id}"',
                "-c", f'{p}.base_url="{ep.base_url}"',
                "-c", f'{p}.env_key="{ep.key_env}"',
                "-c", f'{p}.wire_api="{ep.wire_api or "chat"}"',
            ]  # fmt: skip
            if ep.context_window:
                argv += ["-c", f"model_context_window={ep.context_window}"]
            env[ep.key_env] = key
        if spec.model:
            argv += ["-m", spec.model]
        if spec.effort:
            argv += ["-c", f'model_reasoning_effort="{spec.effort}"']
        if spec.permissions == "bypass":
            argv.append("--dangerously-bypass-approvals-and-sandbox")
    elif spec.harness == "pi":
        model = spec.model
        if spec.backend == "endpoint":
            ep, key = _endpoint(spec, endpoints, s)
            ensure_pi_provider(ep)
            env[ep.key_env] = key
            model = f"{ep.id}/{spec.model}"
        if model:
            argv += ["--model", model]
        if spec.effort:
            argv += ["--thinking", spec.effort]
        if spec.resume:
            argv += ["--session", spec.resume]
    elif spec.harness == "opencode":
        if spec.backend == "endpoint":
            raise LaunchError("opencode takes its endpoints from its own config file")
        if spec.model:
            argv += ["-m", spec.model]
        if spec.prompt:
            argv += ["--prompt", spec.prompt]
    if spec.prompt and spec.harness != "opencode":
        argv.append(spec.prompt)
    return argv, env


def ensure_pi_provider(ep: Endpoint, path: Path | None = None) -> None:
    """pi reads custom providers from models.json; add this endpoint if it is not there."""
    path = path or Path.home() / ".pi" / "agent" / "models.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        data = {}
    providers = data.setdefault("providers", {})
    have = providers.get(ep.id) or {}
    ids = {m.get("id") for m in have.get("models", [])}
    wanted = [m for m in ep.models if m not in ids]
    if have and not wanted:
        return
    have.setdefault("baseUrl", ep.base_url)
    have.setdefault("api", "openai-completions")
    have.setdefault("apiKey", f"${ep.key_env}" if ep.key_env else "none")
    for m in wanted:
        entry: dict[str, Any] = {"id": m, "name": f"{m} ({ep.name or ep.id})"}
        if ep.context_window:
            entry["contextWindow"] = ep.context_window
        have.setdefault("models", []).append(entry)
    providers[ep.id] = have
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def handover_prompt(sess: Any, note: str = "") -> str:
    """Briefing for an agent of another harness, which cannot load the old conversation."""
    x = sess.extra
    lines = [
        f"You are taking over work from a {sess.harness} session in this directory"
        f" ({sess.model or 'unknown model'}). Read the end of its transcript to learn the state"
        f" before you do anything: {sess.transcript_path or '(no transcript)'}",
    ]
    for label, value in (
        ("Its standing goal", x.get("goal")),
        ("The first request was", x.get("first_user")),
        ("The latest request was", x.get("last_user")),
        ("Its last words", sess.last_line),
    ):
        if value:
            lines.append(f"{label}: {value}")
    lines.append(
        f"\nWhat the owner wants from you now: {note}" if note else "\nContinue that work."
    )
    return "\n".join(lines)


def session_name(spec: LaunchSpec) -> str:
    base = _SLUG.sub("-", (spec.name or Path(spec.cwd).name or "agent").lower()).strip("-")[:28]
    return f"{spec.harness}-{base}-{secrets.token_hex(2)}"


async def launch(spec: LaunchSpec, s: Settings, endpoints: list[Endpoint]) -> dict[str, Any]:
    cwd = Path(spec.cwd).expanduser()
    if not cwd.is_dir():
        raise LaunchError(f"no such directory: {spec.cwd}")
    if not spec.prompt and not spec.resume and spec.mode == "background":
        raise LaunchError("a background job needs a task")
    argv, env = build(spec, s, endpoints)
    if spec.harness == "claude" and spec.mode == "background":
        r = await start_background(
            str(cwd),
            spec.prompt,
            name=spec.name,
            resume=spec.resume,
            permission_mode="" if spec.permissions in ("default", "bypass") else spec.permissions,
            config_dir=env.get("CLAUDE_CONFIG_DIR"),
            model="" if spec.backend == "endpoint" else spec.model,
            env_extra={k: v for k, v in env.items() if k != "CLAUDE_CONFIG_DIR"},
        )
        return {
            "ok": bool(r.get("ok")),
            "job_id": r.get("job_id", ""),
            "output": r.get("output", ""),
        }
    if not shutil.which("tmux"):
        raise LaunchError("tmux is not installed on this machine")
    name = session_name(spec)
    run_dir = s.state_dir / "launch"
    run_dir.mkdir(mode=0o700, exist_ok=True)
    env_file = run_dir / f"{name}.env"
    env_file.touch(mode=0o600)
    env_file.write_text("".join(f"export {k}={shlex.quote(v)}\n" for k, v in env.items()))
    wrapper = '. "$1"; rm -f "$1"; shift; exec "$@"'
    proc = await asyncio.create_subprocess_exec(
        "tmux", "new-session", "-d", "-s", name, "-x", "200", "-y", "50", "-c", str(cwd),
        "--", "sh", "-c", wrapper, "sh", str(env_file), *argv,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )  # fmt: skip
    _, err = await asyncio.wait_for(proc.communicate(), 20)
    if proc.returncode != 0:
        env_file.unlink(missing_ok=True)
        raise LaunchError(err.decode().strip()[:300] or "tmux could not start the session")
    return {
        "ok": True,
        "tmux": {"name": name, "socket": "default", "target": f"{name}:0.0"},
        "attach": f"tmux attach -t {name}",
    }
