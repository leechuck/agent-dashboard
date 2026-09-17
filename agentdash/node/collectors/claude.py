"""Roster of Claude Code sessions on this machine.

Sources: `claude agents --json` (interactive + background) and the per-pid
registry `~/.claude/sessions/<pid>.json` (adds messaging socket, name, status).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from ...models import Harness, Session, SessionStatus, now_ms
from ..adapters.claude_transcript import first_request, tail_facts

log = logging.getLogger(__name__)

_STATUS_MAP = {
    "busy": SessionStatus.busy,
    "idle": SessionStatus.idle,
    "waiting": SessionStatus.waiting,
}
_STATE_MAP = {
    "working": SessionStatus.busy,
    "blocked": SessionStatus.waiting,
    "done": SessionStatus.done,
    "failed": SessionStatus.failed,
    "stopped": SessionStatus.stopped,
}


def account_of(config_dir: Path) -> dict[str, str]:
    """Which login a config dir uses: {provider, plan, account}. Never returns a secret.

    A dir with Claude OAuth credentials is an Anthropic subscription, whatever its name;
    `account` tells two subscriptions apart ("max · personal", "team · KAUST").
    Dirs without credentials (API key or gateway wrappers) are named after their suffix.
    """
    plan = ""
    try:
        cred = json.loads((config_dir / ".credentials.json").read_text())
        plan = str((cred.get("claudeAiOauth") or {}).get("subscriptionType") or "")
        has_oauth = bool((cred.get("claudeAiOauth") or {}).get("accessToken"))
    except (OSError, json.JSONDecodeError):
        has_oauth = False
    if not has_oauth:
        name = config_dir.name.removeprefix(".claude-") if config_dir.name != ".claude" else ""
        return {"provider": name or "anthropic", "plan": "", "account": ""}
    profile = config_dir.parent / ".claude.json" if config_dir.name == ".claude" else None
    org = ""
    for f in (config_dir / ".claude.json", profile):
        try:
            acct = json.loads(f.read_text()).get("oauthAccount") if f else None
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(acct, dict) and acct.get("organizationName"):
            org = str(acct["organizationName"])
            break
    who = "personal" if not org or org.endswith("'s Organization") else org
    return {
        "provider": "anthropic",
        "plan": plan,
        "account": " · ".join(x for x in (plan, who) if x),
    }


def provider_for_config_dir(config_dir: Path) -> str:
    return account_of(config_dir)["provider"]


def find_transcript(config_dir: Path, session_id: str, cwd: str) -> Path | None:
    projects = config_dir / "projects"
    slug = cwd.replace("/", "-").replace(".", "-") if cwd else ""
    if slug:
        p = projects / slug / f"{session_id}.jsonl"
        if p.exists():
            return p
    for p in projects.glob(f"*/{session_id}.jsonl"):
        return p
    return None


def _describe(extra: dict[str, Any], facts: dict[str, Any], sid: str, state_dir: Path) -> None:
    """Add what the cockpit needs: last user request and how full the context is.

    The statusline sidecar knows the real window size; without it, assume 200k and
    switch to 1M once the prompt is already larger than that.
    """
    if facts.get("last_user"):
        extra["last_user"] = facts["last_user"]
    if facts.get("first_user"):
        extra["first_user"] = facts["first_user"]
    if facts.get("effort"):
        extra["effort"] = facts["effort"]
    tokens = int(facts.get("context_tokens") or 0)
    window, pct = 0, None
    try:
        side = json.loads((state_dir / "claude-context" / f"{sid}.json").read_text())
        window = int(side.get("context_window_size") or 0)
        for k in ("effort", "model_name", "cost_usd", "fast_mode"):
            if side.get(k) not in (None, ""):
                extra[k] = side[k]
        if side.get("thinking") is False:
            extra["effort"] = "off"
        if side.get("used_percentage") is not None and not tokens:
            pct = float(side["used_percentage"])
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    if not tokens and pct is None:
        return
    if not window:
        window = 1_000_000 if tokens > 200_000 else 200_000
    extra["context_tokens"] = tokens
    extra["context_window"] = window
    extra["context_pct"] = round(pct if pct is not None else 100 * tokens / window, 1)


class ClaudeCollector:
    def __init__(
        self, machine: str, config_dirs: list[Path], state_dir: Path | None = None
    ) -> None:
        self.machine = machine
        self.state_dir = state_dir or Path.home() / ".agentdash"
        self.config_dirs = [d for d in config_dirs if d.exists()]
        self._transcripts: dict[str, Path] = {}
        self._asked: dict[str, dict[str, str]] = {}  # session id -> first and last request

    async def _agents_json(self, config_dir: Path) -> list[dict[str, Any]]:
        env = dict(os.environ, CLAUDE_CONFIG_DIR=str(config_dir))
        try:
            proc = await asyncio.create_subprocess_exec(
                "claude",
                "agents",
                "--json",
                "--all",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            out, err = await asyncio.wait_for(proc.communicate(), 20)
        except (FileNotFoundError, TimeoutError) as e:
            log.warning("claude agents --json failed: %s", e)
            return []
        if proc.returncode != 0:
            log.warning("claude agents --json rc=%s: %s", proc.returncode, err.decode()[:200])
            return []
        try:
            data = json.loads(out.decode() or "[]")
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []

    def _registry(self, config_dir: Path) -> dict[int, dict[str, Any]]:
        out: dict[int, dict[str, Any]] = {}
        for p in (config_dir / "sessions").glob("*.json"):
            try:
                rec = json.loads(p.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(rec, dict) and "pid" in rec:
                out[int(rec["pid"])] = rec
        return out

    def _requests(self, sid: str, tpath: Path | None, facts: dict[str, Any]) -> None:
        """Remember what the user asked: a long tool-heavy turn pushes it out of the tail."""
        if not tpath:
            return
        seen = self._asked.setdefault(sid, {})
        if "first" not in seen:
            seen["first"] = first_request(tpath)
        if facts.get("last_user"):
            seen["last"] = facts["last_user"]
        elif "last" not in seen:  # once per session: look much further back
            seen["last"] = tail_facts(tpath, 6_000_000)["last_user"] or seen["first"]
        facts["last_user"] = seen["last"]
        facts["first_user"] = seen["first"]

    async def collect(self) -> list[Session]:
        sessions: list[Session] = []
        for config_dir in self.config_dirs:
            acct = account_of(config_dir)
            provider = acct["provider"]
            internal = str(self.state_dir / "cockpit")  # the cockpit's own headless calls
            agents, registry = await self._agents_json(config_dir), self._registry(config_dir)
            seen_ids: set[str] = set()
            for a in agents:
                sid = a.get("sessionId") or a.get("id") or ""
                if not sid:
                    continue
                seen_ids.add(sid)
                pid = a.get("pid")
                reg = registry.get(int(pid)) if pid else None
                kind = a.get("kind", "unknown")
                status = _STATUS_MAP.get(a.get("status", ""), None)
                if status is None:
                    status = _STATE_MAP.get(a.get("state", ""), SessionStatus.idle)
                if a.get("waitingFor"):
                    status = SessionStatus.waiting
                cwd = a.get("cwd", "")
                if cwd == internal:
                    continue
                tpath = self._transcripts.get(sid) or find_transcript(config_dir, sid, cwd)
                if tpath:
                    self._transcripts[sid] = tpath
                extra: dict[str, Any] = {"config_dir": str(config_dir)}
                if acct["account"]:
                    extra["account"] = acct["account"]
                if a.get("id"):
                    extra["job_id"] = a["id"]
                if reg:
                    extra["socket"] = reg.get("messagingSocketPath", "")
                    extra["version"] = reg.get("version", "")
                facts = tail_facts(tpath) if tpath else {}
                self._requests(sid, tpath, facts)
                _describe(extra, facts, sid, self.state_dir)
                sessions.append(
                    Session(
                        key=Session.make_key(self.machine, Harness.claude, sid),
                        machine=self.machine,
                        harness=Harness.claude,
                        provider=provider,
                        session_id=sid,
                        name=_short_name(a.get("name") or (reg or {}).get("name", "") or ""),
                        cwd=cwd,
                        kind=kind if kind in ("interactive", "background") else "unknown",
                        status=status,
                        waiting_for=a.get("waitingFor", "") or "",
                        pid=int(pid) if pid else None,
                        started_at=a.get("startedAt"),
                        updated_at=_activity_ms(tpath, reg, a.get("startedAt")),
                        transcript_path=str(tpath) if tpath else "",
                        last_line=facts.get("last_line", ""),
                        model=facts.get("model", ""),
                        extra=extra,
                    )
                )
            # registry-only sessions (agents --json may hide some interactive ones)
            for pid, reg in registry.items():
                sid = reg.get("sessionId", "")
                if not sid or sid in seen_ids or not _pid_alive(pid):
                    continue
                cwd = reg.get("cwd", "")
                if cwd == internal:
                    continue
                tpath = find_transcript(config_dir, sid, cwd)
                facts = tail_facts(tpath) if tpath else {}
                extra = {
                    "config_dir": str(config_dir),
                    "socket": reg.get("messagingSocketPath", ""),
                }
                if acct["account"]:
                    extra["account"] = acct["account"]
                self._requests(sid, tpath, facts)
                _describe(extra, facts, sid, self.state_dir)
                sessions.append(
                    Session(
                        key=Session.make_key(self.machine, Harness.claude, sid),
                        machine=self.machine,
                        harness=Harness.claude,
                        provider=provider,
                        session_id=sid,
                        name=_short_name(reg.get("name", "")),
                        cwd=cwd,
                        kind="interactive" if reg.get("kind") == "interactive" else "unknown",
                        status=_STATUS_MAP.get(reg.get("status", ""), SessionStatus.idle),
                        pid=pid,
                        started_at=reg.get("startedAt"),
                        updated_at=_activity_ms(tpath, reg, reg.get("startedAt")),
                        transcript_path=str(tpath) if tpath else "",
                        last_line=facts.get("last_line", ""),
                        model=facts.get("model", ""),
                        extra=extra,
                    )
                )
        return sessions


def _short_name(name: str, limit: int = 72) -> str:
    """Background sessions are named after their prompt; keep the first line, capped."""
    first = name.strip().split("\n", 1)[0]
    return first if len(first) <= limit else first[: limit - 1].rstrip() + "…"


def _activity_ms(tpath: Path | None, reg: dict[str, Any] | None, started: Any) -> int:
    """Last activity: transcript mtime, else registry updatedAt, else start time."""
    if tpath:
        try:
            return int(tpath.stat().st_mtime * 1000)
        except OSError:
            pass
    if reg and reg.get("updatedAt"):
        return int(reg["updatedAt"])
    return int(started) if started else now_ms()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
