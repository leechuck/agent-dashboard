"""What this machine can run: harnesses, Claude logins, models, and the owner's endpoints."""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path
from typing import Any

import httpx

from ..config import Settings
from .collectors.claude import account_of
from .launcher import HARNESSES, Endpoint, secret

CLAUDE_MODELS = [
    {"id": "", "label": "as configured"},
    {"id": "fable", "label": "Fable"},
    {"id": "opus", "label": "Opus"},
    {"id": "sonnet", "label": "Sonnet"},
    {"id": "haiku", "label": "Haiku"},
]
EFFORTS = {
    "claude": ["low", "medium", "high", "xhigh", "max"],
    "codex": ["low", "medium", "high", "xhigh"],
    "pi": ["off", "minimal", "low", "medium", "high", "xhigh", "max"],
    "opencode": [],
}


def claude_logins(s: Settings, gateway_ids: set[str] | None = None) -> list[dict[str, Any]]:
    """Claude config directories that are, or are meant to become, subscription logins.
    Directories that belong to an endpoint (~/.claude-openrouter) are not logins."""
    out = []
    for d in s.claude_config_dirs:
        if not d.is_dir():
            continue
        name = d.name.removeprefix(".claude-") if d.name != ".claude" else "main"
        a = account_of(d)
        logged_in = a["provider"] == "anthropic" and bool(a["account"])
        if not logged_in and name in (gateway_ids or set()):
            continue
        out.append(
            {
                "dir": d.name,
                "name": name,
                "account": a["account"],
                "plan": a["plan"],
                "logged_in": logged_in,
            }
        )
    return out


def codex_models(home: Path | None = None) -> list[dict[str, Any]]:
    path = (home or Path.home() / ".codex") / "models_cache.json"
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    models = raw.get("models") if isinstance(raw, dict) else raw
    out = [{"id": "", "label": "as configured"}]
    for m in models if isinstance(models, list) else []:
        mid = m.get("slug") or m.get("id")
        if mid and "review" not in str(mid):
            efforts = [
                e.get("effort") if isinstance(e, dict) else e
                for e in m.get("supported_reasoning_levels") or []
            ]
            out.append(
                {"id": str(mid), "label": str(m.get("display_name") or mid), "efforts": efforts}
            )
    return out


async def _lines(*argv: str, timeout: float = 15) -> list[str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except (OSError, TimeoutError):
        return []
    return out.decode(errors="replace").splitlines()


async def pi_models() -> list[dict[str, Any]]:
    """Every model pi can reach, grouped by provider. The router lists run to hundreds, so
    only providers with a handful of models, plus a curated slice of the big ones, are kept."""
    rows = [ln.split() for ln in (await _lines("pi", "--list-models"))[1:]]
    by: dict[str, list[str]] = {}
    for r in rows:
        if len(r) >= 2:
            by.setdefault(r[0], []).append(r[1])
    out = [{"id": "", "label": "as configured"}]
    for provider, models in sorted(by.items()):
        if len(models) > 40:
            models = [
                m
                for m in models
                if any(
                    k in m
                    for k in (
                        "claude",
                        "gpt-5",
                        "gpt-6",
                        "gemini-3",
                        "qwen3",
                        "glm",
                        "kimi",
                        "deepseek",
                    )
                )
            ][:40]  # noqa: E501
        out += [{"id": f"{provider}/{m}", "label": f"{provider} · {m}"} for m in models]
    return out


async def opencode_models() -> list[dict[str, Any]]:
    rows = [ln.strip() for ln in await _lines("opencode", "models") if "/" in ln]
    return [{"id": "", "label": "as configured"}] + [{"id": r, "label": r} for r in rows[:80]]


async def probe_endpoint(ep: Endpoint, s: Settings) -> dict[str, Any]:
    key = secret(s, ep.key_env)
    out: dict[str, Any] = {
        "id": ep.id,
        "key_present": bool(key) or not ep.key_env,
        "reachable": False,
        "models": list(ep.models),
        "error": "",
    }
    if not ep.base_url:
        out["error"] = "no address"
        return out
    try:
        async with httpx.AsyncClient(timeout=6) as c:
            r = await c.get(
                f"{ep.base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {key}"} if key else {},
            )
        out["reachable"] = r.status_code in (200, 401, 403)
        if r.status_code == 200:
            ids = [str(m.get("id")) for m in (r.json().get("data") or []) if m.get("id")]
            if not ep.models:
                out["models"] = ids[:300]
        elif r.status_code in (401, 403):
            out["error"] = "the key was rejected" if key else f"{ep.key_env} is not set here"
        else:
            out["error"] = f"HTTP {r.status_code}"
    except (httpx.HTTPError, ValueError) as e:
        out["error"] = f"not reachable from here ({e.__class__.__name__})"
    return out


class Catalog:
    def __init__(self, s: Settings) -> None:
        self.s = s
        self._cache: tuple[float, str, dict[str, Any]] | None = None

    async def get(
        self, endpoints: list[Endpoint], fresh: bool = False, quick: bool = False
    ) -> dict[str, Any]:
        if quick:
            # Switching subscriptions must not wait for unrelated network/model probes.
            installed = {h: bool(shutil.which(h)) for h in HARNESSES}
            return {
                "ok": True,
                "harnesses": installed,
                "tmux": bool(shutil.which("tmux")),
                "logins": claude_logins(self.s, {e.id for e in endpoints}),
                "models": {
                    "claude": CLAUDE_MODELS,
                    "codex": codex_models() if installed["codex"] else [],
                },
                "efforts": EFFORTS,
                "endpoints": [],
            }
        sig = json.dumps([e.__dict__ for e in endpoints], sort_keys=True)
        if (
            not fresh
            and self._cache
            and self._cache[1] == sig
            and time.time() - self._cache[0] < 180
        ):
            return self._cache[2]
        installed = {h: bool(shutil.which(h)) for h in HARNESSES}
        pi, oc, probes = await asyncio.gather(
            pi_models() if installed["pi"] else asyncio.sleep(0, []),
            opencode_models() if installed["opencode"] else asyncio.sleep(0, []),
            asyncio.gather(*(probe_endpoint(e, self.s) for e in endpoints)),
        )
        result = {
            "ok": True,
            "harnesses": installed,
            "tmux": bool(shutil.which("tmux")),
            "logins": claude_logins(self.s, {e.id for e in endpoints}),
            "models": {
                "claude": CLAUDE_MODELS,
                "codex": codex_models() if installed["codex"] else [],
                "pi": pi,
                "opencode": oc,
            },
            "efforts": EFFORTS,
            "endpoints": list(probes),
        }
        self._cache = (time.time(), sig, result)
        return result
