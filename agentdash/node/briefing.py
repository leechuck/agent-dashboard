"""Run the cockpit's model call on this node, so logins and API keys never leave the machine."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from typing import Any

import httpx

from ..config import Settings
from .launcher import secret


def _agent(s: Settings, agent: dict[str, Any] | None) -> dict[str, str]:
    """What runs the call: the dashboard's Settings page wins over this node's .env."""
    a = {k: str(v) for k, v in (agent or {}).items() if v not in (None, "")}
    harness = a.get("harness") or ("claude" if s.cockpit_provider == "claude" else "api")
    default_model = {"claude": s.cockpit_model, "codex": "", "api": s.cockpit_openai_model}
    return {
        "harness": harness,
        "model": a.get("model") or default_model.get(harness, ""),
        "effort": a.get("effort") or "low",
        "login": a.get("login") or "",
        "endpoint": a.get("endpoint") or "",
    }


async def brief(
    s: Settings,
    system: str,
    digest: dict[str, Any],
    agent: dict[str, Any] | None = None,
    endpoints: list[Any] | None = None,
) -> dict[str, Any]:
    a = _agent(s, agent)
    ep = next((e for e in endpoints or [] if e.id == a["endpoint"]), None)
    if a["harness"] == "api" and ep is not None:
        a["base_url"], a["key"] = ep.base_url, secret(s, ep.key_env)
        if ep.key_env and not a["key"]:
            return {"ok": False, "error": "no key"}  # another machine may hold it
    if a["harness"] == "claude":
        return await _brief_claude(s, system, digest, a)
    if a["harness"] == "codex":
        return await _brief_codex(s, system, digest, a)
    return await _brief_openai(s, system, digest, a)


async def _run(args: list[str], stdin: str, cwd: Any, env: dict[str, str]) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=env,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin.encode()), 170)
    except TimeoutError:
        proc.kill()
        raise
    return proc.returncode or 0, out.decode(), err.decode()


async def _brief_claude(
    s: Settings, system: str, digest: dict[str, Any], a: dict[str, str]
) -> dict[str, Any]:
    """One headless turn of the local Claude Code on its subscription login.

    No tools, no user settings (so no hooks fire and nothing recurses into agentdash),
    no MCP, no saved session; it runs in its own directory, which the roster ignores.
    """
    exe = shutil.which("claude")
    if not exe:
        return {"ok": False, "error": "no key"}  # lets the hub try the next node
    cwd = s.state_dir / "cockpit"
    cwd.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("ANTHROPIC_")}
    known = {d.name: d for d in s.claude_config_dirs}
    config_dir = known.get(a["login"]) or s.cockpit_claude_dir or s.claude_config_dirs[0]
    env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    args = [
        exe, "-p", "--model", a["model"] or "sonnet", "--system-prompt", system,
        "--tools", "", "--setting-sources", "", "--strict-mcp-config",
        "--no-session-persistence", "--disable-slash-commands", "--effort", a["effort"],
        "--output-format", "json",
    ]  # fmt: skip
    try:
        rc, out, err = await _run(args, json.dumps(digest, ensure_ascii=False), cwd, env)
    except TimeoutError:
        return {"ok": False, "error": "claude -p timed out"}
    except OSError as e:
        return {"ok": False, "error": f"claude -p: {e}"}
    try:
        j = json.loads(out or "{}")
    except json.JSONDecodeError:
        j = {}
    if rc != 0 or j.get("is_error") or not str(j.get("result") or "").strip():
        detail = str(j.get("result") or err or out)[:200]
        return {"ok": False, "error": f"claude -p failed: {detail}"}
    models = [m for m in (j.get("modelUsage") or {}) if "haiku" not in m] or list(
        j.get("modelUsage") or {}
    )
    return {
        "ok": True,
        "text": j["result"],
        "model": (models[0] if models else a["model"]) + " (Claude subscription)",
        "cost_usd": None,
    }


async def _brief_codex(
    s: Settings, system: str, digest: dict[str, Any], a: dict[str, str]
) -> dict[str, Any]:
    """One headless, read-only, unsaved Codex turn on its ChatGPT login."""
    exe = shutil.which("codex")
    if not exe:
        return {"ok": False, "error": "no key"}
    cwd = s.state_dir / "cockpit"
    cwd.mkdir(parents=True, exist_ok=True)
    args = [exe, "exec", "--ephemeral", "--skip-git-repo-check", "-s", "read-only",
            "--color", "never", "-c", f"model_reasoning_effort={a['effort']}"]  # fmt: skip
    if a["model"]:
        args += ["-m", a["model"]]
    args.append(system + "\n\nThe fleet snapshot is the JSON on stdin.")
    try:
        rc, out, err = await _run(
            args, json.dumps(digest, ensure_ascii=False), cwd, dict(os.environ)
        )
    except TimeoutError:
        return {"ok": False, "error": "codex exec timed out"}
    except OSError as e:
        return {"ok": False, "error": f"codex exec: {e}"}
    if rc != 0 or not out.strip():
        return {"ok": False, "error": f"codex exec failed: {(err or out).strip()[-200:]}"}
    return {
        "ok": True,
        "text": out,
        "model": (a["model"] or "codex default") + " (Codex subscription)",
        "cost_usd": None,
    }


async def _brief_openai(
    s: Settings, system: str, digest: dict[str, Any], a: dict[str, str]
) -> dict[str, Any]:
    key = a.get("key") or s.cockpit_api_key or os.environ.get("OPENROUTER_API_KEY", "")
    base_url = a.get("base_url") or s.cockpit_base_url
    if not key:
        return {"ok": False, "error": "no key"}
    body = {
        "model": a["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(digest, ensure_ascii=False)},
        ],
        "max_tokens": 8000,
    }
    if "openrouter.ai" in base_url:
        # reasoning models otherwise spend the whole budget thinking and return no text
        body["reasoning"] = {"effort": a["effort"]}
        body["usage"] = {"include": True}
    try:
        async with httpx.AsyncClient(timeout=100) as c:
            r = await c.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {key}", "X-Title": "agentdash cockpit"},
                json=body,
            )
        if r.status_code != 200:
            return {"ok": False, "error": f"model endpoint {r.status_code}: {r.text[:200]}"}
        j = r.json()
        choice = j["choices"][0]
        text = choice["message"].get("content") or ""
        if not text.strip():
            why = choice.get("finish_reason") or "unknown"
            return {"ok": False, "error": f"model returned no text (finish_reason={why})"}
        return {
            "ok": True,
            "text": text,
            "model": j.get("model", a["model"]),
            "cost_usd": (j.get("usage") or {}).get("cost"),
        }
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
        return {"ok": False, "error": f"{e.__class__.__name__}: {e}"[:200]}
