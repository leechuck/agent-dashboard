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
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from ...config import discover_claude_dirs
from ...models import UsageWindow, now_ms
from .claude import account_of

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


_SLUG = re.compile(r"[^a-z0-9]+")


def _scope_label(scope: Any) -> str:
    """What a scoped limit covers: a model ("Fable"), a surface, or both."""
    if not isinstance(scope, dict):
        return ""
    parts = []
    for key in ("model", "surface"):
        v = scope.get(key)
        if isinstance(v, dict):
            name = v.get("display_name") or v.get("id")
            if name:
                parts.append(str(name))
        elif isinstance(v, str) and v:
            parts.append(v)
    return " · ".join(parts)


def parse_claude_usage(data: dict[str, Any], account: str) -> list[UsageWindow]:
    """Every limit the account has, not only the two everybody knows.

    Newer Claude accounts report a `limits` list: a session window, a weekly window over
    everything, and one weekly window per scope (a model such as Fable, or a surface).
    Older ones only have the named blocks, which are read when the list is absent.
    """
    out: list[UsageWindow] = []
    seen: set[str] = set()
    for lim in data.get("limits") or []:
        if not isinstance(lim, dict) or lim.get("percent") is None:
            continue
        group = str(lim.get("group") or lim.get("kind") or "other")
        scope = _scope_label(lim.get("scope"))
        window = f"{group}_{_SLUG.sub('-', scope.lower()).strip('-')}" if scope else group
        if window in seen:
            continue
        seen.add(window)
        label = {"session": "Session · 5 hours", "weekly": "Week · everything"}.get(group, group)
        if scope:
            label = f"{'Week' if group == 'weekly' else group.title()} · {scope}"
        out.append(
            UsageWindow(
                provider="anthropic",
                account=account,
                window=window,
                label=label,
                used_pct=float(lim["percent"]),
                resets_at=_iso_ms(lim.get("resets_at")),
                source="oauth",
                detail={
                    "group": group,
                    "scope": scope,
                    "severity": str(lim.get("severity") or ""),
                    # a scope nobody has touched yet is reported inactive; still worth showing
                    "active": bool(lim.get("is_active")),
                },
            )
        )
    if not out:  # older account: the named blocks
        labels = {
            "five_hour": "Session · 5 hours",
            "seven_day": "Week · everything",
            "seven_day_opus": "Week · Opus",
            "seven_day_sonnet": "Week · Sonnet",
        }
        for key, label in labels.items():
            w = data.get(key)
            if isinstance(w, dict) and w.get("utilization") is not None:
                out.append(
                    UsageWindow(
                        provider="anthropic",
                        account=account,
                        window={"five_hour": "session", "seven_day": "weekly"}.get(key, key),
                        label=label,
                        used_pct=float(w["utilization"]),
                        resets_at=_iso_ms(w.get("resets_at")),
                        source="oauth",
                        detail={"group": "session" if key == "five_hour" else "weekly"},
                    )
                )
    # usage credits: money that covers what the plan's limits do not
    spend = data.get("spend") or {}
    extra = data.get("extra_usage") or {}
    if spend.get("enabled") or extra.get("is_enabled"):
        used = (spend.get("used") or {}).get("amount_minor")
        exp = (spend.get("used") or {}).get("exponent", 2)
        detail: dict[str, Any] = {"group": "credits"}
        if used is not None:
            detail["used_usd"] = float(used) / (10 ** int(exp or 2))
        if spend.get("balance") is not None:
            detail["remaining"] = spend["balance"]
        if extra.get("monthly_limit") is not None:
            detail["limit"] = extra["monthly_limit"]
        out.append(
            UsageWindow(
                provider="anthropic",
                account=account,
                window="credits",
                label="Usage credits",
                used_pct=float(spend.get("percent") or extra.get("utilization") or 0),
                resets_at=None,
                source="oauth",
                detail=detail,
            )
        )
    return out


class UsageCollector:
    def __init__(self, machine: str, claude_config_dirs: list[Path], state_dir: Path) -> None:
        self.machine = machine
        self.claude_dirs = list(claude_config_dirs)
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

    def _claude_from_statusline(self, config_dir: Path, account: str) -> list[UsageWindow]:
        suffix = "" if config_dir.name == ".claude" else "-" + config_dir.name
        f = self.state_dir / f"claude-rate-limits{suffix}.json"
        try:
            data = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError):
            return []
        if now_ms() - int(data.get("_written", 0)) > 20 * 60 * 1000:
            return []
        out = []
        for key, label, group in (
            ("five_hour", "Session · 5 hours", "session"),
            ("seven_day", "Week · everything", "weekly"),
            ("spend_limit", "Usage credits", "credits"),
        ):
            w = data.get("rate_limits", {}).get(key)
            if w and w.get("used_percentage") is not None:
                out.append(
                    UsageWindow(
                        provider="anthropic",
                        account=account,
                        window={"five_hour": "session", "seven_day": "weekly"}.get(key, key),
                        label=label,
                        used_pct=float(w["used_percentage"]),
                        resets_at=_iso_ms(w.get("resets_at")),
                        source="statusline",
                        detail={"group": group},
                    )
                )
        return out

    async def _claude_from_api(self, config_dir: Path, account: str) -> list[UsageWindow]:
        cred_file = config_dir / ".credentials.json"
        try:
            cred = json.loads(cred_file.read_text())
        except (OSError, json.JSONDecodeError):
            return []
        oauth = cred.get("claudeAiOauth") or {}
        token = oauth.get("accessToken")
        if not token:
            return []
        backoff = f"anthropic:{account}"
        if time.time() < self._backoff_until.get(backoff, 0):
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
            self._backoff_until[backoff] = time.time() + 30 * 60
            log.warning("claude usage: rate limited, backing off 30 min")
            return []
        if r.status_code != 200:
            log.warning("claude usage: HTTP %s", r.status_code)
            return []
        return parse_claude_usage(r.json(), account)

    async def claude(self) -> list[UsageWindow]:
        """One set of windows per subscription login; two config dirs on one login count once."""
        out: list[UsageWindow] = []
        seen: set[str] = set()
        # a login added from the dashboard appears on the next poll, not the next restart
        dirs = dict.fromkeys([*self.claude_dirs, *discover_claude_dirs()])
        for d in (d for d in dirs if d.exists()):
            acct = account_of(d)
            if acct["provider"] != "anthropic" or not acct["account"] or acct["account"] in seen:
                continue  # API-key or gateway dirs have no subscription windows
            seen.add(acct["account"])
            wins = await self._claude_from_api(d, acct["account"])
            if not wins:  # rate limited or offline: the status line still knows the basics
                wins = self._claude_from_statusline(d, acct["account"])
            for w in wins:
                w.detail = {**w.detail, "config_dir": d.name, "plan": acct["plan"]}
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
