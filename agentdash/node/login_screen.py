"""Read what Claude Code asks in its terminal (a login, a folder trust, a paused
session, a model switch), so the dashboard can offer buttons, a link and a box for the
code instead of a terminal that is hard to use on a phone.

Only what is on the screen is read: the sign-in address Claude prints for the owner to
open. No token ever appears there; the code the owner pastes goes straight to the pane.
"""

from __future__ import annotations

import re
from typing import Any

_URL_START = re.compile(
    r"https://(?:claude\.com|claude\.ai|console\.anthropic\.com|platform\.claude\.com)/\S+"
)
_OPTION = re.compile(r"^\s*(?:❯|>)?\s*(\d)\.\s+(.+?)\s*$")
_RULE = re.compile(r"^\s*[─━═]{6,}")
_NOISE = ("●", "⎿", "✻", "✽", "·", "*", "⏵", "❯ ", "> ")
_CHOICE_HINTS = ("enter to select", "enter to confirm", "session paused")


def _options(lines: list[str]) -> list[dict[str, Any]]:
    return [
        {
            "n": int(m.group(1)),
            "label": m.group(2),
            "chosen": "❯" in line or line.lstrip().startswith(">"),
        }
        for line in lines
        if (m := _OPTION.match(line))
    ]


def _choice(lines: list[str]) -> dict[str, Any] | None:
    """A dialog the TUI put in the way: its title, its question and its numbered options.

    Claude Code draws these for a paused session, a model switch, a trust question and
    the like. Numbered lists in a reply do not count: a dialog marks the chosen option."""
    tail = lines[-40:]
    starts = [i for i, line in enumerate(tail) if _OPTION.match(line)]
    if len(starts) < 2:
        return None
    options = _options(tail[starts[0] :])
    low = "\n".join(tail).lower()
    if not any(o["chosen"] for o in options) and not any(h in low for h in _CHOICE_HINTS):
        return None
    # The dialog's text sits between a rule (or the last piece of output) and the options,
    # with blank lines between its paragraphs.
    block: list[str] = []
    for line in reversed(tail[max(0, starts[0] - 14) : starts[0]]):
        text = line.strip().lstrip("│┃|").strip()
        if _RULE.match(line) or text.startswith(_NOISE):
            break
        if text:
            block.append(text)
    block.reverse()
    title = block[0].lstrip("☐☑☒ ").strip() if block else "The agent asks"
    return {
        "stage": "choice",
        "title": title[:120],
        "question": " ".join(block[1:])[:1200],
        "options": options,
    }


def _url(lines: list[str]) -> str:
    """The sign-in address. The TUI breaks long lines itself, so a URL can continue on
    the following lines; they are joined until a line that is not part of it."""
    for i, line in enumerate(lines):
        m = _URL_START.search(line)
        if not m or "oauth" not in m.group(0):
            continue
        url = m.group(0)
        for nxt in lines[i + 1 :]:
            piece = nxt.strip()
            if not piece or " " in piece or not re.match(r"^[\w%&=.\-~+/?:#]+$", piece):
                break
            url += piece
        return url
    return ""


def read(screen: str) -> dict[str, Any]:
    """{stage, url?, options?, title?, question?}: stage is theme | method | browser | code |
    done | trust | choice (any other dialog with numbered options) | other."""
    lines = screen.splitlines()
    text = "\n".join(lines[-60:])
    low = text.lower()
    out: dict[str, Any] = {"stage": "other"}
    if "login successful" in low or "logged in as" in low:
        out["stage"] = "done"
        out["continue"] = "press enter to continue" in low
        return out
    if "select login method" not in low and "syntax theme" in low and "dark mode" in low:
        out["stage"] = "theme"  # first start on a new login: pick a look, then log in
        return out
    if "trust this folder" in low or "do you trust" in low:
        out["stage"] = "trust"
        out["options"] = [
            {"n": int(m.group(1)), "label": m.group(2), "chosen": "❯" in line or ">" in line}
            for line in lines[-30:]
            if (m := _OPTION.match(line))
        ]
        return out
    url = _url(lines[-80:])
    if url:
        out["url"] = url
        out["stage"] = "code" if "paste code" in low else "browser"
        return out
    if "select login method" in low or "log in" in low and "1." in text:
        out["stage"] = "method"
        out["options"] = _options(lines[-30:])
        return out
    return _choice(lines) or out
