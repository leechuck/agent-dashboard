"""Fleet cockpit: what needs Robert, and what he could do better.

Two layers:

1. `analyse()` is deterministic and free. It turns the roster, pending decisions,
   usage windows and machine state into ranked findings, each with one action the
   web app can offer (open a session, open Decisions, start a new session, clean up).
2. `Briefer` asks a language model for a short briefing on top of those findings:
   what each agent is doing and advice that needs judgement (hand off to a fresh
   session, fan work out, move to another harness). The model call runs on a node
   that holds the API key; the hub never sees the key.

Nothing here acts on a session. The cockpit only recommends.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from ..models import Decision, Machine, Session, UsageWindow, now_ms

log = logging.getLogger(__name__)

HOUR = 3600 * 1000
STALE_MS = 48 * HOUR
ACTIVE = ("busy", "idle", "waiting")
SEVERITY_ORDER = {"act": 0, "warn": 1, "info": 2}
HARNESS_PROVIDER = {"claude": "anthropic", "codex": "openai"}
PROVIDER_NAME = {"anthropic": "Claude", "openai": "Codex", "openrouter": "OpenRouter"}


@dataclass
class Action:
    type: str  # open_session | open_decisions | open_limits | new_session | cleanup | send_prompt
    label: str
    href: str = ""
    machine: str = ""
    keys: list[str] = field(default_factory=list)
    prompt: str = ""  # for send_prompt: typed into keys[0]


@dataclass
class Finding:
    id: str
    severity: str  # act | warn | info
    kind: str
    title: str
    detail: str = ""
    session_key: str = ""
    machine: str = ""
    action: Action | None = None


def is_stale(s: Session, now: int) -> bool:
    return s.status != "busy" and now - s.updated_at > STALE_MS


def _name(s: Session) -> str:
    return s.name or s.session_id[:8]


def _session_href(key: str) -> str:
    from urllib.parse import quote

    return f"#/session/{quote(key, safe='')}"


def _new_href(machine: str, cwd: str = "") -> str:
    from urllib.parse import quote

    return f"#/new?machine={quote(machine, safe='')}" + (
        f"&cwd={quote(cwd, safe='')}" if cwd else ""
    )


def _span(ms: float) -> str:
    m = max(0, int(ms / 60000))
    if m < 90:
        return f"{m} min"
    h = m / 60
    return f"{h:.0f} h" if h < 48 else f"{h / 24:.0f} d"


def burn_rate(points: list[tuple[int, float]], now: int, lookback_ms: int = 2 * HOUR) -> float:
    """Percent per hour over the recent history of one window; 0 when unknown.

    A drop in used_pct means the window reset, so only the run since the last drop counts.
    """
    pts = [(t, v) for t, v in points if t >= now - lookback_ms]
    for i in range(len(pts) - 1, 0, -1):
        if pts[i][1] < pts[i - 1][1] - 0.5:
            pts = pts[i:]
            break
    if len(pts) < 2 or pts[-1][0] - pts[0][0] < 10 * 60000:
        return 0.0
    return max(0.0, (pts[-1][1] - pts[0][1]) / ((pts[-1][0] - pts[0][0]) / HOUR))


def _is_money(w: UsageWindow) -> bool:
    return w.provider == "openrouter"


def _accounts(usage: list[UsageWindow], provider: str) -> list[str]:
    return sorted({w.account for w in usage if w.provider == provider and not _is_money(w)})


def _who(w: UsageWindow, usage: list[UsageWindow]) -> str:
    """ "Claude", or "Claude (team · KAUST)" when the provider has more than one login."""
    name = PROVIDER_NAME.get(w.provider, w.provider)
    return f"{name} ({w.account})" if w.account and len(_accounts(usage, w.provider)) > 1 else name


def _command(w: UsageWindow) -> str:
    d = str(w.detail.get("config_dir") or "")
    return "claude" + d.removeprefix(".claude") if d.startswith(".claude") else ""


def provider_headroom(usage: list[UsageWindow]) -> list[dict[str, Any]]:
    """Worst subscription window per login, as percent used."""
    worst: dict[tuple[str, str], UsageWindow] = {}
    for w in usage:
        if _is_money(w) or w.window == "extra_usage":
            continue
        k = (w.provider, w.account)
        if k not in worst or w.used_pct > worst[k].used_pct:
            worst[k] = w
    return [
        {
            "provider": w.provider,
            "account": w.account,
            "label": _who(w, usage),
            "used_pct": w.used_pct,
            "window": w.label or w.window,
            "command": _command(w),
        }
        for w in worst.values()
    ]


def pick_account(usage: list[UsageWindow], provider: str, now: int) -> dict[str, Any] | None:
    """Which login of one provider should take new work, so no quota expires unused.

    Weekly quota that resets soon is lost first, so rank by unused weekly percent per
    hour left; a login whose short window is nearly full cannot take work right now.
    """
    rows = []
    for acct in _accounts(usage, provider):
        wins = [w for w in usage if w.provider == provider and w.account == acct]
        week = next((w for w in wins if w.window.startswith(("seven_day", "secondary"))), None)
        short = next((w for w in wins if w.window.startswith(("five_hour", "primary"))), None)
        if week is None:
            week, short = short, None
        if week is None:
            continue
        hours = max(1.0, ((week.resets_at or now + 168 * HOUR) - now) / HOUR)
        rows.append(
            {
                "account": acct,
                "week_pct": week.used_pct,
                "week_resets_in": _span(hours * HOUR),
                "short_pct": short.used_pct if short else None,
                "blocked": bool(short and short.used_pct >= 90) or week.used_pct >= 98,
                "score": (100 - week.used_pct) / hours,
                "command": _command(week),
            }
        )
    if len(rows) < 2:
        return None
    open_ = [r for r in rows if not r["blocked"]] or rows
    best = max(open_, key=lambda r: r["score"])
    return {"best": best, "rows": rows}


def analyse(
    sessions: list[Session],
    machines: list[Machine],
    decisions: list[Decision],
    usage: list[UsageWindow],
    history: dict[str, list[tuple[int, float]]] | None = None,
    now: int | None = None,
) -> list[Finding]:
    now = now or now_ms()
    history = history or {}
    out: list[Finding] = []
    by_key = {s.key: s for s in sessions}
    online = {m.id for m in machines if m.online}
    live = [s for s in sessions if s.status in ACTIVE and not is_stale(s, now)]

    # 1. approvals waiting on the phone
    for d in decisions:
        if d.status != "pending":
            continue
        s = by_key.get(d.session_key)
        who = d.session_name or (_name(s) if s else d.session_key.rsplit(":", 1)[-1][:8])
        out.append(
            Finding(
                id=f"decision:{d.id}",
                severity="act",
                kind="decision",
                title=f"{who} asks to run {d.tool_name or 'a tool'}",
                detail=f"On {d.machine}. The agent is blocked until you answer"
                + (
                    f"; falls back to the terminal in {_span(d.expires_at - now)}."
                    if d.expires_at
                    else "."
                ),
                session_key=d.session_key,
                machine=d.machine,
                action=Action("open_decisions", "Answer", "#/decisions"),
            )
        )
    decided = {d.session_key for d in decisions if d.status == "pending"}

    # 2. sessions blocked on Robert
    for s in live:
        if s.status != "waiting" or s.key in decided:
            continue
        out.append(
            Finding(
                id=f"waiting:{s.key}",
                severity="act",
                kind="waiting",
                title=f"{_name(s)} is waiting for {s.waiting_for or 'you'}",
                detail=f"{s.harness} on {s.machine}, blocked for {_span(now - s.updated_at)}.",
                session_key=s.key,
                machine=s.machine,
                action=Action("open_session", "Open", _session_href(s.key)),
            )
        )

    # 3. machines that dropped off while they had work
    for m in machines:
        if m.online:
            continue
        had = [s for s in live if s.machine == m.id]
        if had or now - m.last_seen < 24 * HOUR:
            out.append(
                Finding(
                    id=f"offline:{m.id}",
                    severity="warn" if had else "info",
                    kind="offline",
                    title=f"{m.id} is offline",
                    detail=f"Last seen {_span(now - m.last_seen)} ago."
                    + (f" {len(had)} session(s) there cannot be reached." if had else ""),
                    machine=m.id,
                )
            )

    # 4. subscription windows: level, burn rate, and where else to work
    headroom = provider_headroom(usage)
    for w in usage:
        if _is_money(w):
            continue
        rate = burn_rate(history.get(f"{w.provider}:{w.account}:{w.window}", []), now)
        to_reset = (w.resets_at - now) if w.resets_at else None
        eta = ((100 - w.used_pct) / rate * HOUR) if rate > 0 else None
        runs_out = eta is not None and to_reset is not None and eta < to_reset and w.used_pct >= 50
        if w.used_pct < 80 and not runs_out:
            continue
        pname = _who(w, usage)
        users = [
            s
            for s in live
            if HARNESS_PROVIDER.get(s.harness) == w.provider
            and s.provider in ("", w.provider)
            and s.extra.get("account", w.account) == w.account
        ]
        busy = [s for s in users if s.status == "busy"]
        alts = sorted(
            (h for h in headroom if (h["provider"], h["account"]) != (w.provider, w.account)),
            key=lambda h: (h["provider"] != w.provider, h["used_pct"]),
        )
        alts = [h for h in alts if h["used_pct"] < 70]
        bits = [f"{w.used_pct:.0f}% used"]
        if to_reset is not None:
            bits.append(f"resets in {_span(to_reset)}")
        if runs_out:
            bits.append(
                f"at the current pace ({rate:.0f}%/h) it is full in {_span(eta)}, before the reset"
            )
        advice = []
        if busy:
            advice.append(
                f"{len(busy)} {pname} session(s) are working now: "
                + ", ".join(_name(s) for s in busy[:4])
                + "."
            )
        if alts:
            h = alts[0]
            how = f" with `{h['command']}`" if h["command"] and h["provider"] == w.provider else ""
            advice.append(
                f"Start new work on {h['label']} ({h['used_pct']:.0f}% used){how} or through OpenRouter."
            )
        elif to_reset is not None:
            advice.append(
                "No other subscription has room; pause non-urgent work until the reset or use OpenRouter credits."
            )
        out.append(
            Finding(
                id=f"usage:{w.provider}:{w.account}:{w.window}",
                severity="act" if w.used_pct >= 95 or (runs_out and eta < HOUR) else "warn",
                kind="limit",
                title=f"{pname} {w.label or w.window} limit: "
                + ", ".join(bits[:1])
                + (", running out early" if runs_out and w.used_pct < 80 else ""),
                detail=". ".join(b[0].upper() + b[1:] for b in bits[1:])
                + (". " if bits[1:] else "")
                + " ".join(advice),
                action=Action("open_limits", "Limits", "#/limits"),
            )
        )

    # 5. money: OpenRouter credits and key limits
    for w in usage:
        if not _is_money(w):
            continue
        remaining = w.detail.get("remaining")
        if w.window == "credits" and isinstance(remaining, int | float) and remaining < 10:
            out.append(
                Finding(
                    id="usage:openrouter:credits",
                    severity="act" if remaining < 2 else "warn",
                    kind="limit",
                    title=f"OpenRouter credits low: ${remaining:.2f} left",
                    detail="Sessions routed through OpenRouter stop when this reaches zero. Top up, or move them to a subscription harness.",
                    action=Action("open_limits", "Limits", "#/limits"),
                )
            )
        elif w.window != "credits" and w.used_pct >= 80:
            out.append(
                Finding(
                    id=f"usage:openrouter:{w.window}:{w.account}",
                    severity="act" if w.used_pct >= 95 else "warn",
                    kind="limit",
                    title=f"OpenRouter key {w.account} at {w.used_pct:.0f}% of its limit",
                    detail="Raise the key limit on openrouter.ai or switch the sessions using it to another key.",
                    action=Action("open_limits", "Limits", "#/limits"),
                )
            )

    # 6. context windows filling up
    for s in live:
        pct = s.extra.get("context_pct")
        if not isinstance(pct, int | float) or pct < 70:
            continue
        window = int(s.extra.get("context_window") or 0)
        size = f" of {window // 1000}k" if window else ""
        if pct >= 85:
            detail = (
                f"Context {pct:.0f}% full{size}. Compact now (/compact) or ask for a handoff summary and "
                "continue in a fresh session; quality drops and auto-compaction can lose detail near the limit."
            )
        else:
            detail = f"Context {pct:.0f}% full{size}. Plan a /compact at the next natural break, or start the next sub-task in a new session."
        can_type = (
            bool(s.extra.get("tmux")) and s.harness in ("claude", "codex") and s.status == "idle"
        )
        out.append(
            Finding(
                id=f"context:{s.key}",
                severity="warn" if pct >= 85 else "info",
                kind="context",
                title=f"{_name(s)}: context {pct:.0f}% full",
                detail=detail,
                session_key=s.key,
                machine=s.machine,
                action=Action(
                    "send_prompt",
                    "Compact now",
                    _session_href(s.key),
                    keys=[s.key],
                    prompt="/compact",
                )
                if can_type
                else Action("open_session", "Open", _session_href(s.key)),
            )
        )

    # 7. busy but silent: possibly stuck
    for s in live:
        if s.status == "busy" and now - s.updated_at > 30 * 60000:
            out.append(
                Finding(
                    id=f"silent:{s.key}",
                    severity="warn",
                    kind="stuck",
                    title=f"{_name(s)} has been busy without output for {_span(now - s.updated_at)}",
                    detail="A long tool call, a hung command, or a dialog the dashboard cannot see. Check the terminal.",
                    session_key=s.key,
                    machine=s.machine,
                    action=Action("open_session", "Open", _session_href(s.key)),
                )
            )

    # 8. several agents in one working directory
    by_dir: dict[tuple[str, str], list[Session]] = {}
    for s in live:
        if s.cwd and s.harness != "tmux" and s.status == "busy" and not s.extra.get("parent"):
            by_dir.setdefault((s.machine, s.cwd), []).append(s)
    for (machine, cwd), group in by_dir.items():
        if len(group) > 1:
            out.append(
                Finding(
                    id=f"shared-dir:{machine}:{cwd}",
                    severity="info",
                    kind="conflict",
                    title=f"{len(group)} agents are editing {cwd.rstrip('/').rsplit('/', 1)[-1]} at once",
                    detail=", ".join(_name(s) for s in group)
                    + f" on {machine}. Use git worktrees if they touch the same files.",
                    machine=machine,
                )
            )

    # 9. sessions ready for the next instruction
    ready = [
        s
        for s in live
        if s.status == "idle"
        and s.harness not in ("tmux",)
        and not s.extra.get("parent")
        and now - s.updated_at < 12 * HOUR
    ]
    if ready:
        ready.sort(key=lambda s: -s.updated_at)
        out.append(
            Finding(
                id="ready",
                severity="info",
                kind="ready",
                title=f"{len(ready)} session(s) finished their turn and wait for the next instruction",
                detail=", ".join(
                    f"{_name(s)} ({s.machine}, {_span(now - s.updated_at)} ago)" for s in ready[:6]
                )
                + ("…" if len(ready) > 6 else ""),
                session_key=ready[0].key,
                action=Action("open_session", "Open latest", _session_href(ready[0].key)),
            )
        )

    # 10. stale sessions to clear out
    for m in machines:
        stale = [
            s for s in sessions if s.machine == m.id and s.status in ACTIVE and is_stale(s, now)
        ]
        if len(stale) >= 3 and m.id in online:
            out.append(
                Finding(
                    id=f"stale:{m.id}",
                    severity="info",
                    kind="cleanup",
                    title=f"{len(stale)} stale sessions on {m.id}",
                    detail="Untouched for more than two days. Removing them keeps the fleet readable.",
                    machine=m.id,
                    action=Action(
                        "cleanup", "Remove stale", machine=m.id, keys=[s.key for s in stale]
                    ),
                )
            )

    # 11. spare capacity
    if live and not any(s.status == "busy" for s in live) and headroom:
        h = min(headroom, key=lambda x: x["used_pct"])
        if h["used_pct"] < 50:
            target = next((m.id for m in machines if m.online), "")
            out.append(
                Finding(
                    id="capacity",
                    severity="info",
                    kind="capacity",
                    title="Nothing is running and there is room on the limits",
                    detail=f"{h['label']} is at {h['used_pct']:.0f}%. A good moment to queue background work.",
                    action=Action("new_session", "Start a session", _new_href(target))
                    if target
                    else None,
                )
            )

    # 12. several logins of one provider: which one should take new work
    for provider in sorted({w.provider for w in usage if not _is_money(w)}):
        pick = pick_account(usage, provider, now)
        if not pick:
            continue
        best, pname = pick["best"], PROVIDER_NAME.get(provider, provider)
        on_other = [
            s
            for s in live
            if HARNESS_PROVIDER.get(s.harness) == provider
            and s.extra.get("account") not in (None, best["account"])
            and not s.extra.get("parent")
        ]
        rows = "; ".join(
            f"{r['account']}: week {r['week_pct']:.0f}%, resets in {r['week_resets_in']}"
            + (f", session {r['short_pct']:.0f}%" if r["short_pct"] is not None else "")
            + (" (full for now)" if r["blocked"] else "")
            for r in pick["rows"]
        )
        gap = max(r["week_pct"] for r in pick["rows"]) - min(r["week_pct"] for r in pick["rows"])
        out.append(
            Finding(
                id=f"account:{provider}",
                severity="warn" if gap >= 40 and on_other else "info",
                kind="account",
                title=f"Use {best['account']} for new {pname} work"
                + (f" (`{best['command']}`)" if best["command"] else ""),
                detail=f"It has the most weekly quota that would otherwise expire unused. {rows}."
                + (
                    f" {len(on_other)} session(s) run on the other login: "
                    + ", ".join(_name(s) for s in on_other[:4])
                    + "."
                    if on_other
                    else ""
                ),
                action=Action("open_limits", "Limits", "#/limits"),
            )
        )

    out.sort(key=lambda f: SEVERITY_ORDER.get(f.severity, 3))
    return out


def stats(
    sessions: list[Session], machines: list[Machine], now: int | None = None
) -> dict[str, Any]:
    now = now or now_ms()
    live = [s for s in sessions if s.status in ACTIVE and not is_stale(s, now)]
    return {
        "busy": sum(s.status == "busy" for s in live),
        "subagents": sum(1 for s in live if s.extra.get("parent")),
        "waiting": sum(s.status == "waiting" for s in live),
        "idle": sum(s.status == "idle" for s in live),
        "stale": sum(1 for s in sessions if s.status in ACTIVE and is_stale(s, now)),
        "machines_online": sum(m.online for m in machines),
        "machines": len(machines),
    }


def headline(findings: list[Finding], st: dict[str, Any]) -> str:
    act = sum(f.severity == "act" for f in findings)
    warn = sum(f.severity == "warn" for f in findings)
    if act:
        lead = f"{act} thing{'s' if act != 1 else ''} need{'' if act != 1 else 's'} you now"
    elif warn:
        lead = f"Nothing blocked, {warn} thing{'s' if warn != 1 else ''} to watch"
    else:
        lead = "All clear"
    return f"{lead}. {st['busy']} working, {st['idle']} idle, {st['waiting']} waiting on {st['machines_online']}/{st['machines']} machines."


def digest(
    sessions: list[Session],
    machines: list[Machine],
    usage: list[UsageWindow],
    findings: list[Finding],
    now: int | None = None,
) -> dict[str, Any]:
    """Compact, secret-free description of the fleet for the language model."""
    now = now or now_ms()
    live = [s for s in sessions if s.status in ACTIVE and not is_stale(s, now)]
    live.sort(key=lambda s: (s.status != "waiting", s.status != "busy", -s.updated_at))
    return {
        "machines": [{"id": m.id, "online": m.online} for m in machines],
        "sessions": [
            {
                "key": s.key,
                "name": _name(s),
                "machine": s.machine,
                "harness": s.harness,
                "provider": s.provider,
                "model": s.model,
                "kind": s.kind,
                "dir": s.cwd,
                "status": s.status,
                "waiting_for": s.waiting_for,
                "minutes_since_activity": int((now - s.updated_at) / 60000),
                "hours_running": round((now - s.started_at) / HOUR, 1) if s.started_at else None,
                "context_pct": s.extra.get("context_pct"),
                "effort": s.extra.get("effort"),
                "account": s.extra.get("account"),
                "subagent_of": s.extra.get("parent"),
                "can_receive_prompt": (s.harness == "claude" and bool(s.extra.get("socket")))
                or (s.harness == "pi" and bool(s.extra.get("inbox")))
                or bool(s.extra.get("tmux")),
                "can_receive_slash_commands": bool(s.extra.get("tmux")),
                "last_user_request": s.extra.get("last_user", ""),
                "last_output": s.last_line,
            }
            for s in live[:40]
        ],
        "limits": [
            {
                "provider": w.provider,
                "account": w.account,
                "window": w.label or w.window,
                "used_pct": round(w.used_pct, 1),
                "resets_in_minutes": int((w.resets_at - now) / 60000) if w.resets_at else None,
            }
            for w in usage
            if not _is_money(w)
        ],
        "prepaid_credit": [
            {
                "provider": w.provider,
                "what": w.label or w.window,
                "remaining_usd": w.detail.get("remaining"),
            }
            for w in usage
            if _is_money(w) and isinstance(w.detail.get("remaining"), int | float)
        ],
        "rule_findings": [
            {"severity": f.severity, "title": f.title, "detail": f.detail} for f in findings
        ],
    }


SYSTEM_PROMPT = """You are the cockpit of a fleet of coding agents (Claude Code, Codex, pi, opencode, \
Hermes) that one researcher runs across several machines. You get a JSON snapshot: sessions with status, \
context fill, last request and last output; subscription limits; and findings that deterministic rules \
already produced. Write a briefing for the owner, who reads it on a phone.

Rules:
- Do not repeat the rule findings; build on them. Add judgement the rules cannot make.
- Be concrete: name sessions. No generic advice, no praise, no filler.
- Useful advice includes: which session to answer first and why; moving work to another harness when a \
limit is close; compacting or handing off to a fresh session when context is high or the topic changed; \
splitting a long serial task across parallel subagents or background sessions; stopping sessions that \
duplicate each other or have gone idle for long; work that could start now because capacity is free.
- Only suggest what the data supports. If the fleet is fine, say so in one sentence and give few or no suggestions.
- "limits" are subscription windows that reset; "prepaid_credit" is money and only matters when the \
remaining dollars are low compared with what the fleet burns. Never call credit "x% used".
- A busy session without last_output is normal, not a problem.
- Sessions with "subagent_of" were started by another session and are managed by it: never suggest \
answering or prompting them; mention them only if they are stuck or wasteful.
- When a provider has several accounts, say which account new work should use so that no weekly \
quota expires unused, and name sessions worth moving.
- Only put a "prompt" on sessions with can_receive_prompt true, or the owner cannot send it. A prompt \
that is a slash command (/compact, /clear) only works where can_receive_slash_commands is true; elsewhere \
say in "why" that the owner has to type it in the terminal, and leave "prompt" empty.
- A suggested prompt must be something the owner could send to that session verbatim.

Reply with JSON only, matching:
{"summary": "2-4 sentences on the state of the fleet",
 "sessions": [{"key": "<session key>", "doing": "max 12 words on what it is working on"}],
 "suggestions": [{"title": "imperative, max 10 words", "why": "1-2 sentences", "kind": \
"answer|switch_harness|compact|handoff|fan_out|new_session|stop|other", "session_key": "<key or empty>", \
"prompt": "optional text to send to that session"}]}
At most 6 suggestions, most valuable first."""


def fingerprint(d: dict[str, Any]) -> str:
    """Changes when the fleet changed in a way worth a new briefing (not on every tick)."""
    # busy and idle alternate all day; only blocking states and coarse levels count
    core = {
        "s": sorted(
            (
                s["key"],
                s["status"] == "waiting",
                s["waiting_for"],
                int((s["context_pct"] or 0) // 10),
            )
            for s in d["sessions"]
        ),
        "l": sorted(
            (w["provider"], w["account"], w["window"], int(w["used_pct"] // 5)) for w in d["limits"]
        ),
        "f": sorted(f["title"] for f in d["rule_findings"] if f["severity"] == "act"),
    }
    return hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()[:16]


def parse_briefing(text: str) -> dict[str, Any]:
    """Pull the JSON object out of a model reply and keep only the fields we render."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in reply")
    raw = json.loads(text[start : end + 1])
    if not isinstance(raw, dict):
        raise ValueError("reply is not an object")
    kinds = {
        "answer",
        "switch_harness",
        "compact",
        "handoff",
        "fan_out",
        "new_session",
        "stop",
        "other",
    }
    sug = []
    for x in raw.get("suggestions") or []:
        if not isinstance(x, dict) or not x.get("title"):
            continue
        sug.append(
            {
                "title": str(x["title"])[:120],
                "why": str(x.get("why") or "")[:400],
                "kind": x.get("kind") if x.get("kind") in kinds else "other",
                "session_key": str(x.get("session_key") or ""),
                "prompt": str(x.get("prompt") or "")[:1200],
            }
        )
    return {
        "summary": str(raw.get("summary") or "")[:1200],
        "sessions": {
            str(x["key"]): str(x.get("doing") or "")[:140]
            for x in raw.get("sessions") or []
            if isinstance(x, dict) and x.get("key")
        },
        "suggestions": sug[:6],
    }


class Briefer:
    """Caches one briefing and refreshes it through a node when the fleet has changed."""

    def __init__(self, min_interval: float = 600.0) -> None:
        self.min_interval = min_interval
        self.preferred = ""  # machine id from settings
        self.briefing: dict[str, Any] | None = None
        self.error = ""
        self.generating = False
        self._lock = asyncio.Lock()
        self._node = ""  # the node that last produced a briefing

    def view(self, current_fp: str) -> dict[str, Any]:
        b = self.briefing
        return {
            "briefing": b,
            "outdated": bool(b) and b.get("fingerprint") != current_fp,
            "generating": self.generating,
            "error": self.error,
        }

    async def refresh(self, state: Any, d: dict[str, Any], force: bool = False) -> None:
        fp = fingerprint(d)
        b = self.briefing
        if not force and b:
            if (
                b.get("fingerprint") == fp
                or now_ms() - b["generated_at"] < self.min_interval * 1000
            ):
                return
        if self._lock.locked():
            return
        async with self._lock:
            self.generating = True
            state.bus.publish("cockpit.updated", {"generating": True})
            try:
                order = sorted(state.nodes, key=lambda m: (m != self.preferred, m != self._node, m))
                if not order:
                    raise RuntimeError("no node online to run the briefing")
                last = "no online node can run the model (needs a logged-in claude, or an API key)"
                for machine in order:
                    res = await state.request(
                        state.nodes[machine],
                        "cockpit.brief",
                        {"system": SYSTEM_PROMPT, "digest": d},
                        timeout=120,
                    )
                    if res.get("ok"):
                        parsed = parse_briefing(res.get("text", ""))
                        self.briefing = {
                            **parsed,
                            "generated_at": now_ms(),
                            "fingerprint": fp,
                            "model": res.get("model", ""),
                            "via": machine,
                            "cost_usd": res.get("cost_usd"),
                        }
                        self._node, self.error = machine, ""
                        break
                    if res.get("error") != "no key":
                        last = f"{machine}: {res.get('error', 'failed')}"
                else:
                    self.error = last
            except (TimeoutError, RuntimeError, ValueError, json.JSONDecodeError) as e:
                self.error = str(e) or e.__class__.__name__
                log.warning("cockpit briefing failed: %s", self.error)
            finally:
                self.generating = False
                state.bus.publish("cockpit.updated", {"generating": False})


def to_json(findings: list[Finding]) -> list[dict[str, Any]]:
    return [asdict(f) for f in findings]
