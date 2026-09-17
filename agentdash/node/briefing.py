"""Run the cockpit's model call on this node, so the API key never leaves the machine."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from ..config import Settings


async def brief(s: Settings, system: str, digest: dict[str, Any]) -> dict[str, Any]:
    key = s.cockpit_api_key or os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        return {"ok": False, "error": "no key"}
    body = {
        "model": s.cockpit_model,
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
            "model": j.get("model", s.cockpit_model),
            "cost_usd": (j.get("usage") or {}).get("cost"),
        }
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
        return {"ok": False, "error": f"{e.__class__.__name__}: {e}"[:200]}
