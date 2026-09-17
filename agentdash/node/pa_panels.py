"""Panels of the Personal tab: each one is a script in the PA repo that prints JSON.

The node knows which scripts exist and how a request from the browser becomes an argument
list; what the scripts compute, and every personal datum, stays in the PA repo (ADR 0007,
contract in docs/pa-panels.md). Scripts run without a shell, with validated arguments, and
read results are kept in memory for a short while so that a phone reloading the tab does
not start a calendar or mail query every time.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class PanelError(Exception):
    pass


# name -> (script and arguments, seconds a result stays fresh, timeout)
PANELS: dict[str, tuple[list[str], int, int]] = {
    "todo": (["todo.py", "list", "--json"], 20, 30),
    "agenda": (["agenda.py", "show", "--json", "--days", "7"], 300, 75),
}

_ID = re.compile(r"^[0-9a-f]{8}$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_LIST = re.compile(r"^[A-Za-z][A-Za-z0-9 _-]{0,30}$")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}$")
_WHEN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:\d{2})$")
_DURATION = re.compile(r"^\d{1,4}[mhd]$")
_WORD = re.compile(r"^[a-z]{1,20}$")


def _need(a: dict[str, Any], key: str, pattern: re.Pattern[str], optional: bool = False) -> str:
    v = str(a.get(key) or "").strip()
    if not v and optional:
        return ""
    if not pattern.match(v):
        raise PanelError(f"bad or missing '{key}'")
    return v


def _text(a: dict[str, Any], key: str, limit: int, optional: bool = False) -> str:
    v = " ".join(str(a.get(key) or "").split())
    if not v and not optional:
        raise PanelError(f"'{key}' is empty")
    if len(v) > limit:
        raise PanelError(f"'{key}' is longer than {limit} characters")
    return v


def _todo_add(a: dict[str, Any]) -> list[str]:
    cmd = ["todo.py", "add", "--json", "--commit", "--date", _need(a, "date", _DATE)]
    cmd += ["--list", _need(a, "list", _LIST, optional=True) or "PA"]
    if project := _need(a, "project", _SLUG, optional=True):
        cmd += ["--project", project]
    return [*cmd, "--", _text(a, "text", 2000)]


def _todo_done(a: dict[str, Any]) -> list[str]:
    cmd = ["todo.py", "done", "--json", "--commit"]
    if note := _text(a, "note", 300, optional=True):
        cmd += ["--note", note]
    return [*cmd, "--", _need(a, "id", _ID)]


def _todo_simple(verb: str, second: str = "") -> Callable[[dict[str, Any]], list[str]]:
    def build(a: dict[str, Any]) -> list[str]:
        cmd = ["todo.py", verb, "--json", "--commit", "--", _need(a, "id", _ID)]
        return [*cmd, _need(a, second, _DATE)] if second else cmd

    return build


def _calendar_add(a: dict[str, Any]) -> list[str]:
    """An event on the owner's own calendar. There is no field for guests or a description,
    here or in the script: an event with guests sends invitations."""
    all_day = bool(a.get("all_day"))
    when = _DATE if all_day else _WHEN
    cmd = ["agenda.py", "add", "--json", f"--title={_text(a, 'title', 200)}"]
    cmd.append(f"--start={_need(a, 'start', when)}")
    if end := _need(a, "end", when, optional=True):
        cmd.append(f"--end={end}")
    if all_day:
        cmd.append("--all-day")
    elif a.get("minutes") is not None:
        minutes = int(a["minutes"]) if str(a["minutes"]).isdigit() else 0
        if not 5 <= minutes <= 1440:
            raise PanelError("'minutes' is between 5 and 1440")
        cmd.append(f"--minutes={minutes}")
    cmd.append(f"--calendar={_need(a, 'calendar', _WORD, optional=True) or 'work'}")
    if reminder := _need(a, "reminder", _DURATION, optional=True):
        cmd.append(f"--reminder={reminder}")
    if location := _text(a, "location", 200, optional=True):
        cmd.append(f"--location={location}")
    return cmd


def _calendar_remind(a: dict[str, Any]) -> list[str]:
    return [
        "agenda.py",
        "remind",
        "--json",
        f"--title={_text(a, 'title', 200)}",
        f"--start={_need(a, 'start', _WHEN)}",
        f"--calendar={_need(a, 'calendar', _WORD, optional=True) or 'work'}",
    ]


# act -> (argument builder, timeout, panels whose cached result is now stale)
ACTS: dict[str, tuple[Callable[[dict[str, Any]], list[str]], int, tuple[str, ...]]] = {
    "todo_add": (_todo_add, 60, ("todo",)),
    "todo_done": (_todo_done, 60, ("todo",)),
    "todo_reopen": (_todo_simple("reopen"), 60, ("todo",)),
    "todo_snooze": (_todo_simple("snooze", "until"), 60, ("todo",)),
    "todo_unsnooze": (_todo_simple("unsnooze"), 60, ("todo",)),
    "todo_due": (_todo_simple("due", "date"), 60, ("todo",)),
    "todo_sync": (lambda a: ["todo_sync.py", "sync"], 300, ("todo",)),
    "calendar_add": (_calendar_add, 90, ("agenda",)),
    "calendar_remind": (_calendar_remind, 90, ("agenda",)),
}


class Panels:
    def __init__(self, pa_dir: Path) -> None:
        self.scripts = pa_dir / "scripts"
        self.cwd = pa_dir
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def _run(self, cmd: list[str], timeout: int) -> tuple[int, str, str]:
        script = self.scripts / cmd[0]
        if not script.is_file():
            raise PanelError(f"the PA repo has no scripts/{cmd[0]}")
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(script),
            *cmd[1:],
            cwd=str(self.cwd),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except TimeoutError as e:
            proc.kill()
            raise PanelError(f"scripts/{cmd[0]} did not finish in {timeout} s") from e
        return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")

    async def run_json(self, cmd: list[str], timeout: int) -> dict[str, Any]:
        rc, out, err = await self._run(cmd, timeout)
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            data = None
        if not isinstance(data, dict):
            tail = (err.strip() or out.strip())[-300:]
            raise PanelError(f"scripts/{cmd[0]} failed ({rc}): {tail}" if tail else f"exit {rc}")
        data.setdefault("ok", rc == 0)
        return data

    async def panel(self, name: str, fresh: bool = False) -> dict[str, Any]:
        if name not in PANELS:
            raise PanelError(f"unknown panel {name}")
        cmd, ttl, timeout = PANELS[name]
        async with self._locks.setdefault(name, asyncio.Lock()):
            hit = self._cache.get(name)
            if hit and not fresh and time.monotonic() - hit[0] < ttl:
                return hit[1]
            data = await self.run_json(cmd, timeout)
            if data.get("ok"):
                self._cache[name] = (time.monotonic(), data)
            return data

    async def act(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name not in ACTS:
            raise PanelError(f"unknown action {name}")
        build, timeout, stale = ACTS[name]
        cmd = build(args if isinstance(args, dict) else {})
        if "--json" in cmd:
            result = await self.run_json(cmd, timeout)
        else:
            rc, out, err = await self._run(cmd, timeout)
            result = {"ok": rc == 0, "output": out.strip()[-600:]}
            if rc != 0:
                result["error"] = (err.strip() or out.strip())[-300:] or f"exit {rc}"
        for p in stale:
            self._cache.pop(p, None)
        return result
