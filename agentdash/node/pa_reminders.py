"""Which todo reminders to push, and when. Pure, so the rules can be tested.

The list lives in the PA repo; the node asks its todo script what is due. A long list of
old overdue items must not turn into a daily wall of notifications, so the rules are:

- nothing between 21:00 and 07:00 local time;
- the first check of the day sends ONE digest: what is due today, what slipped yesterday,
  and how many older items are overdue (a count, not a list);
- after that, only an item that newly became due today (added, re-dated, snooze ended)
  is announced, one push per item, never twice on the same day.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

QUIET_BEFORE, QUIET_FROM = 7, 21
URL = "/#/personal"


def _titles(items: list[dict[str, Any]], limit: int = 4) -> str:
    names = [str(i.get("title") or "")[:80] for i in items[:limit]]
    more = len(items) - limit
    return "; ".join(names) + (f"; and {more} more" if more > 0 else "")


def plan(
    items: list[dict[str, Any]], seen: dict[str, str], now: datetime
) -> tuple[list[dict[str, str]], dict[str, str]]:
    """(pushes to send, the new `seen` map). `seen` maps item id -> day it was announced,
    plus "_digest" -> day of the last digest. Entries from other days are dropped."""
    today = now.date().isoformat()
    seen = {k: v for k, v in seen.items() if v == today}
    if not QUIET_BEFORE <= now.hour < QUIET_FROM:
        return [], seen
    due = [i for i in items if i.get("bucket") == "today" and i.get("id")]
    slipped = [i for i in items if i.get("bucket") == "overdue" and i.get("days") == -1]
    older = sum(1 for i in items if i.get("bucket") == "overdue") - len(slipped)
    pushes: list[dict[str, str]] = []
    if seen.get("_digest") != today:
        seen["_digest"] = today
        for i in due:
            seen[str(i["id"])] = today
        parts = []
        if due:
            parts.append(_titles(due))
        if slipped:
            parts.append("slipped yesterday: " + _titles(slipped, 2))
        if older > 0:
            parts.append(f"{older} older overdue")
        if due or slipped:
            title = f"{len(due)} due today" if due else "Nothing due today"
            if len(due) == 1:
                title = "Due today"
            pushes.append(
                {"title": title, "body": " · ".join(parts), "url": URL, "tag": f"pa-digest-{today}"}
            )
        return pushes, seen
    for i in due:
        if seen.get(str(i["id"])) == today:
            continue
        seen[str(i["id"])] = today
        pushes.append(
            {
                "title": "Due today",
                "body": str(i.get("title") or "")[:140],
                "url": URL,
                "tag": f"pa-todo-{i['id']}",
            }
        )
    return pushes, seen
