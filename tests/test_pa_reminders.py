import json
from datetime import datetime

from agentdash.config import Settings
from agentdash.db import Database
from agentdash.hub.bus import EventBus
from agentdash.hub.state import HubState
from agentdash.node.pa import PersonalAssistant
from agentdash.node.pa_reminders import plan

MORNING = datetime(2026, 9, 17, 8, 5)


def item(id_, bucket, days=0, title=""):
    return {"id": id_, "bucket": bucket, "days": days, "title": title or f"todo {id_}"}


def test_first_check_of_the_day_is_one_digest():
    items = [item("a", "today"), item("b", "today"), item("c", "overdue", -1)]
    items += [item(f"o{n}", "overdue", -30) for n in range(70)]
    pushes, seen = plan(items, {"x": "2026-09-16", "_digest": "2026-09-16"}, MORNING)
    assert len(pushes) == 1
    assert pushes[0]["title"] == "2 due today"
    assert pushes[0]["body"] == "todo a; todo b · slipped yesterday: todo c · 70 older overdue"
    assert seen == {"_digest": "2026-09-17", "a": "2026-09-17", "b": "2026-09-17"}
    assert plan(items, seen, MORNING.replace(hour=9)) == ([], seen)


def test_old_overdue_items_alone_stay_silent():
    pushes, seen = plan([item("o", "overdue", -40)], {}, MORNING)
    assert pushes == [] and seen == {"_digest": "2026-09-17"}


def test_an_item_that_becomes_due_later_is_announced_once():
    seen = {"_digest": "2026-09-17", "a": "2026-09-17"}
    items = [item("a", "today"), item("n", "today", title="Call the bank")]
    pushes, seen = plan(items, seen, MORNING.replace(hour=14))
    assert [(p["title"], p["body"], p["tag"]) for p in pushes] == [
        ("Due today", "Call the bank", "pa-todo-n")
    ]
    assert plan(items, seen, MORNING.replace(hour=15))[0] == []


def test_nothing_at_night_and_the_digest_waits_for_the_morning():
    items = [item("a", "today")]
    assert plan(items, {}, MORNING.replace(hour=5)) == ([], {})
    assert plan(items, {}, MORNING.replace(hour=22)) == ([], {})
    assert len(plan(items, {}, MORNING.replace(hour=7))[0]) == 1


async def test_node_remembers_what_it_announced(tmp_path, monkeypatch):
    repo = tmp_path / "pa"
    (repo / "scripts").mkdir(parents=True)
    (repo / "CLAUDE.md").write_text("x")
    due = {"ok": True, "items": [item("0123abcd", "today", title="Vote")]}
    (repo / "scripts" / "todo.py").write_text(f"print({json.dumps(json.dumps(due))})")
    monkeypatch.setattr("agentdash.config.STATE_DIR", tmp_path / "state")
    pa = PersonalAssistant(Settings(pa_dir=repo, web_token="", node_token=""))
    first = await pa.due_reminders(MORNING)
    assert [p["body"] for p in first] == ["Vote"]
    assert await pa.due_reminders(MORNING.replace(hour=10)) == []
    pa.mark("mail:1", "done")  # item states and reminder memory share a file
    assert await pa.due_reminders(MORNING.replace(hour=11)) == []


class FakePusher:
    enabled = True

    def __init__(self):
        self.sent = []

    async def send(self, subs, payload):
        self.sent.append(payload)
        return []


async def test_hub_pushes_a_reminder_and_keeps_no_trace_of_it(tmp_path):
    db = Database(tmp_path / "h.db")
    await db.open()
    bus, pusher = EventBus(), FakePusher()
    q = bus.subscribe()
    hub = HubState(db, bus, pusher)
    await hub.node_event("m", {"kind": "pa.reminder", "title": "Due today", "body": "Vote"})
    assert pusher.sent == [
        {"title": "Due today", "body": "Vote", "url": "/#/personal", "tag": "pa"}
    ]
    assert await db.list_events(10) == [] and q.empty()
    await hub.node_event("m", {"kind": "something.else"})
    assert len(await db.list_events(10)) == 1 and not q.empty()
    await db.close()
