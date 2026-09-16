"""Rate-limit / credit windows for the providers this machine is logged into.

Sources
- Claude (per config dir): ~/.agentdash/claude-rate-limits.json written by the
  statusline sidecar (free, live), else GET api.anthropic.com/api/oauth/usage
  with the OAuth token from <config>/.credentials.json.
- Codex: `codex app-server` stdio JSON-RPC `account/rateLimits/read`.
- OpenRouter: GET /api/v1/key and /api/v1/credits with OPENROUTER_API_KEY.
Only percentages and reset times leave the machine.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from ...models import UsageWindow, now_ms

log = logging.getLogger(__name__)


def _iso_ms(v: Any) -> int | None:
    if not v:
        return None
    if isinstance(v, int | float):
        return int(v * 1000) if v < 10**12 else int(v)
    try:
        return int(datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


class UsageCollector:
    def __init__(self, machine: str, claude_config_dirs: list[Path], state_dir: Path) -> None:
        self.machine = machine
        self.claude_dirs = [d for d in claude_config_dirs if d.exists()]
        self.state_dir = state_dir
        self._claude_version = ""
        self._backoff_until: dict[str, float] = {}

    # ---- Claude -------------------------------------------------------
    def _claude_version_str(self) -> str:
        if not self._claude_version:
            try:
                out = subprocess.run(
                    ["claude", "--version"], capture_output=True, text=True, timeout=15
                )
                self._claude_version = out.stdout.split()[0] if out.stdout.strip() else "2.1.0"
            except (OSError, subprocess.SubprocessError):
                self._claude_version = "2.1.0"
        return self._claude_version

    def _claude_from_statusline(self, config_dir: Path) -> list[UsageWindow]:
        suffix = "" if config_dir.name == ".claude" else "-" + config_dir.name
        f = self.state_dir / f"claude-rate-limits{suffix}.json"
        try:
            data = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            return []
        if now_ms() - int(data.get("_written", 0)) > 20 * 60 * 1000:
            return []
        out = []
        for key, label in (
            ("five_hour", "5 hours"),
            ("seven_day", "7 days"),
            ("spend_limit", "spend limit"),
        ):
            w = data.get("rate_limits", {}).get(key)
            if w and w.get("used_percentage") is not None:
                out.append(
                    UsageWindow(
                        provider="anthropic",
                        account=data.get("account", ""),
                        window=key,
                        label=label,
                        used_pct=float(w["used_percentage"]),
                        resets_at=_iso_ms(w.get("resets_at")),
                        source="statusline",
                    )
                )
        return out

    async def _claude_from_api(self, config_dir: Path) -> list[UsageWindow]:
        cred_file = config_dir / ".credentials.json"
        try:
            cred = json.loads(cred_file.read_text())
        except (OSError, json.JSONDecodeError):
            return []
        oauth = cred.get("claudeAiOauth") or {}
        token = oauth.get("accessToken")
        if not token:
            return []
        if time.time() < self._backoff_until.get("anthropic", 0):
            return []
        headers = {
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": f"claude-code/{self._claude_version_str()}",
            "accept": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.get("https://api.anthropic.com/api/oauth/usage", headers=headers)
        except httpx.HTTPError as e:
            log.warning("claude usage: %s", e)
            return []
        if r.status_code == 429:
            self._backoff_until["anthropic"] = time.time() + 30 * 60
            log.warning("claude usage: rate limited, backing off 30 min")
            return []
        if r.status_code != 200:
            log.warning("claude usage: HTTP %s", r.status_code)
            return []
        data = r.json()
        account = oauth.get("subscriptionType", "")
        out = []
        labels = {
            "five_hour": "5 hours",
            "seven_day": "7 days",
            "seven_day_opus": "7 days (Opus)",
            "seven_day_sonnet": "7 days (Sonnet)",
        }
        for key, label in labels.items():
            w = data.get(key)
            if isinstance(w, dict) and w.get("utilization") is not None:
                out.append(
                    UsageWindow(
                        provider="anthropic",
                        account=account,
                        window=key,
                        label=label,
                        used_pct=float(w["utilization"]),
                        resets_at=_iso_ms(w.get("resets_at")),
                        source="oauth",
                    )
                )
        extra = data.get("extra_usage") or {}
        if extra.get("is_enabled"):
            out.append(
                UsageWindow(
                    provider="anthropic",
                    account=account,
                    window="extra_usage",
                    label="extra usage (month)",
                    used_pct=float(extra.get("utilization") or 0),
                    resets_at=None,
                    source="oauth",
                    detail={"used": extra.get("used_credits"), "limit": extra.get("monthly_limit")},
                )
            )
        return out

    async def claude(self) -> list[UsageWindow]:
        out: list[UsageWindow] = []
        for d in self.claude_dirs:
            if d.name != ".claude":
                continue  # API-key config dirs (openrouter) have no subscription windows
            wins = self._claude_from_statusline(d)
            if not wins:
                wins = await self._claude_from_api(d)
            out += wins
        return out

    # ---- Codex --------------------------------------------------------
    async def codex(self) -> list[UsageWindow]:
        if time.time() < self._backoff_until.get("openai", 0):
            return []
        try:
            proc = await asyncio.create_subprocess_exec(
                "codex",
                "app-server",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError:
            return []
        assert proc.stdin and proc.stdout

        def send(o: dict[str, Any]) -> None:
            proc.stdin.write((json.dumps(o) + "\n").encode())  # type: ignore[union-attr]

        result: dict[str, Any] = {}
        try:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "clientInfo": {
                            "name": "agentdash",
                            "title": "agentdash",
                            "version": "0.1.0",
                        }
                    },
                }
            )
            await proc.stdin.drain()
            deadline = time.time() + 25
            sent_read = False
            while time.time() < deadline:
                line = await asyncio.wait_for(proc.stdout.readline(), 10)
                if not line:
                    break
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if o.get("id") == 1 and not sent_read:
                    send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
                    send(
                        {
                            "jsonrpc": "2.0",
                            "id": 2,
                            "method": "account/rateLimits/read",
                            "params": {},
                        }
                    )
                    await proc.stdin.drain()
                    sent_read = True
                elif o.get("id") == 2:
                    result = o.get("result") or {}
                    break
        except (TimeoutError, OSError, BrokenPipeError) as e:
            log.warning("codex usage: %s", e)
        finally:
            proc.kill()
        rl = result.get("rateLimits") or {}
        if not rl:
            return []
        plan = str(rl.get("planType") or "")
        out = []
        for key in ("primary", "secondary"):
            w = rl.get(key)
            if not isinstance(w, dict) or w.get("usedPercent") is None:
                continue
            mins = int(w.get("windowDurationMins") or 0)
            label = f"{mins // 60} hours" if mins < 1440 else f"{mins // 1440} days"
            out.append(
                UsageWindow(
                    provider="openai",
                    account=plan,
                    window=f"{key}_{mins}m",
                    label=label,
                    used_pct=float(w["usedPercent"]),
                    resets_at=_iso_ms(w.get("resetsAt")),
                    source="app-server",
                )
            )
        credits = rl.get("credits") or {}
        if credits.get("hasCredits"):
            out.append(
                UsageWindow(
                    provider="openai",
                    account=plan,
                    window="credits",
                    label="credits balance",
                    used_pct=0.0,
                    resets_at=None,
                    source="app-server",
                    detail={"balance": credits.get("balance")},
                )
            )
        return out

    # ---- OpenRouter ---------------------------------------------------
    async def openrouter(self) -> list[UsageWindow]:
        key = os.environ.get("OPENROUTER_API_KEY", "")
        if not key:
            return []
        headers = {"Authorization": f"Bearer {key}"}
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                k = await c.get("https://openrouter.ai/api/v1/key", headers=headers)
                cr = await c.get("https://openrouter.ai/api/v1/credits", headers=headers)
        except httpx.HTTPError as e:
            log.warning("openrouter usage: %s", e)
            return []
        out = []
        if k.status_code == 200:
            d = k.json().get("data", {})
            limit, remaining = d.get("limit"), d.get("limit_remaining")
            used_pct = 0.0
            if limit:
                used_pct = max(0.0, 100.0 * (1 - (remaining or 0) / limit))
            out.append(
                UsageWindow(
                    provider="openrouter",
                    account=d.get("label", ""),
                    window="key_limit",
                    label=f"key limit ({d.get('limit_reset') or 'no reset'})",
                    used_pct=used_pct,
                    resets_at=None,
                    source="api",
                    detail={
                        "limit": limit,
                        "remaining": remaining,
                        "usage_daily": d.get("usage_daily"),
                        "usage_weekly": d.get("usage_weekly"),
                        "usage_monthly": d.get("usage_monthly"),
                    },
                )
            )
        if cr.status_code == 200:
            d = cr.json().get("data", {})
            total, used = float(d.get("total_credits") or 0), float(d.get("total_usage") or 0)
            out.append(
                UsageWindow(
                    provider="openrouter",
                    account="",
                    window="credits",
                    label="credits",
                    used_pct=(100.0 * used / total) if total else 0.0,
                    resets_at=None,
                    source="api",
                    detail={"total": total, "used": used, "remaining": total - used},
                )
            )
        return out

    async def collect(self) -> list[UsageWindow]:
        results = await asyncio.gather(
            self.claude(), self.codex(), self.openrouter(), return_exceptions=True
        )
        out: list[UsageWindow] = []
        for r in results:
            if isinstance(r, BaseException):
                log.warning("usage collector failed: %s", r)
            else:
                out += r
        for w in out:
            w.machine = self.machine
        return out
