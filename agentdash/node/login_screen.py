"""Read what Claude Code shows while logging in, so the dashboard can offer buttons,
a link and a box for the code instead of a terminal that is hard to use on a phone.

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
    """{stage, url?, options?}: stage is theme | method | browser | code | done | trust | other."""
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
        opts = []
        for line in lines[-30:]:
            m = _OPTION.match(line)
            if m:
                opts.append({"n": int(m.group(1)), "label": m.group(2), "chosen": "❯" in line})
        out["options"] = opts
    return out
