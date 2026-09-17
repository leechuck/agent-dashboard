"""Run the cockpit's model call on this node, so logins and API keys never leave the machine."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from typing import Any

import httpx

from ..config import Settings


async def brief(s: Settings, system: str, digest: dict[str, Any]) -> dict[str, Any]:
    if s.cockpit_provider == "claude":
        return await _brief_claude(s, system, digest)
    return await _brief_openai(s, system, digest)


async def _brief_claude(s: Settings, system: str, digest: dict[str, Any]) -> dict[str, Any]:
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
    env["CLAUDE_CONFIG_DIR"] = str(s.cockpit_claude_dir or s.claude_config_dirs[0])
    args = [
        exe, "-p", "--model", s.cockpit_model or "sonnet", "--system-prompt", system,
        "--tools", "", "--setting-sources", "", "--strict-mcp-config",
        "--no-session-persistence", "--disable-slash-commands", "--effort", "low",
        "--output-format", "json",
    ]  # fmt: skip
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )
        out, err = await asyncio.wait_for(
            proc.communicate(json.dumps(digest, ensure_ascii=False).encode()), 100
        )
    except TimeoutError:
        proc.kill()
        return {"ok": False, "error": "claude -p timed out"}
    except OSError as e:
        return {"ok": False, "error": f"claude -p: {e}"}
    try:
        j = json.loads(out.decode() or "{}")
    except json.JSONDecodeError:
        j = {}
    if proc.returncode != 0 or j.get("is_error") or not str(j.get("result") or "").strip():
        detail = str(j.get("result") or err.decode() or out.decode())[:200]
        return {"ok": False, "error": f"claude -p failed: {detail}"}
    models = [m for m in (j.get("modelUsage") or {}) if "haiku" not in m]
    return {
        "ok": True,
        "text": j["result"],
        "model": (models[0] if models else s.cockpit_model) + " (subscription)",
        "cost_usd": None,
    }


async def _brief_openai(s: Settings, system: str, digest: dict[str, Any]) -> dict[str, Any]:
    key = s.cockpit_api_key or os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        return {"ok": False, "error": "no key"}
    body = {
        "model": s.cockpit_openai_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(digest, ensure_ascii=False)},
        ],
        "max_tokens": 8000,
    }
    if "openrouter.ai" in s.cockpit_base_url:
        # reasoning models otherwise spend the whole budget thinking and return no text
        body["reasoning"] = {"effort": "low"}
        body["usage"] = {"include": True}
    try:
        async with httpx.AsyncClient(timeout=100) as c:
            r = await c.post(
                f"{s.cockpit_base_url.rstrip('/')}/chat/completions",
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
            "model": j.get("model", s.cockpit_openai_model),
            "cost_usd": (j.get("usage") or {}).get("cost"),
        }
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
        return {"ok": False, "error": f"{e.__class__.__name__}: {e}"[:200]}
