import json

import pytest

from agentdash.config import Settings
from agentdash.models import Harness, Session
from agentdash.node.pa import PAError, PersonalAssistant


@pytest.fixture
def pa(tmp_path, monkeypatch):
    repo = tmp_path / "pa"
    (repo / "data").mkdir(parents=True)
    (repo / "configs").mkdir()
    (repo / "CLAUDE.md").write_text("x")
    (repo / "configs" / "workspaces.yaml").write_text(
        "roots:\n  - {kind: paper, machine: m, path: ~/papers}\n"
        "workspaces:\n  - {name: p1, kind: paper, machine: m, path: ~/papers/p1}\n"
    )
    briefing = {
        "summary": "s",
        "items": [
            {
                "id": "mail:1",
                "draft": {
                    "kind": "email_reply",
                    "buffer": "*claude-mail-3*",
                    "message_id": "abc@x",
                    "to": "a@x",
                    "body": "Dear A,\nyes.\nBest,\nRob.",
                },
            },
            {"id": "mm:2", "draft": {"kind": "mattermost", "body": "ok"}},
            {"id": "task:3", "task": {"title": "edit", "workspace": "p1"}},
        ],
    }
    (repo / "data" / "dashboard_briefing.json").write_text(json.dumps(briefing))
    monkeypatch.setattr("agentdash.config.STATE_DIR", tmp_path / "state")
    s = Settings(pa_dir=repo, web_token="", node_token="")
    p = PersonalAssistant(s)
    p.calls = []
    p.alive = {"*claude-mail-3*"}

    async def fake_emacs(form, timeout=60):
        p.calls.append(form)
        if form.startswith("(fboundp"):
            return "t"
        if form.startswith("(if (get-buffer"):
            return "t" if any(b in form for b in p.alive) else "nil"
        if "claude-email-discard-buffer" in form:
            p.alive.clear()
            return "t"
        if form.startswith("(claude-email-reply"):
            p.alive.add("*claude-mail-9*")
            return '"*claude-mail-9*"'
        if "claude-email-send-buffer" in form:
            p.alive.clear()
            return "t"
        return "nil"

    p._emacs = fake_emacs
    return p


async def test_get_merges_status_and_workspaces(pa):
    pa.mark("task:3", "delegated", "to p1")
    running = Session(
        key="m:claude:9",
        machine="m",
        harness=Harness.claude,
        session_id="9",
        name="pa-briefing",
        status="busy",
        started_at=5,
    )
    r = pa.get({running.key: running})
    assert r["ok"] and r["session"]["key"] == "m:claude:9"
    assert [i["status"] for i in r["briefing"]["items"]] == ["open", "open", "delegated"]
    assert r["workspaces"][0]["name"] == "p1" and r["roots"][0]["kind"] == "paper"


async def test_unedited_draft_is_sent_from_its_gnus_buffer(pa):
    r = await pa.send_email("mail:1", None)
    assert r == {"ok": True, "status": "sent"}
    assert not [c for c in pa.calls if "claude-email-reply" in c or "discard" in c]
    assert '(claude-email-send-buffer "*claude-mail-3*")' in pa.calls[-2]
    with pytest.raises(PAError, match="already sent"):
        await pa.send_email("mail:1", None)


async def test_edited_body_replaces_the_draft_before_sending(pa):
    await pa.send_email("mail:1", "Dear A,\nno.\nBest,\nRob.")
    steps = ("discard", "claude-email-reply", "send-buffer")
    order = [k for c in pa.calls if "fboundp" not in c for k in steps if k in c]
    assert order == ["discard", "claude-email-reply", "send-buffer"]
    assert '"abc@x"' in next(c for c in pa.calls if "claude-email-reply" in c)
    assert '"*claude-mail-9*"' in next(c for c in pa.calls if "(claude-email-send-buffer" in c)


async def test_only_mail_drafts_can_be_sent_and_unknown_items_fail(pa):
    for item in ("mm:2", "task:3", "nope"):
        with pytest.raises(PAError):
            await pa.send_email(item, None)
    assert not [c for c in pa.calls if "send-buffer" in c]


async def test_a_draft_gnus_keeps_open_is_not_reported_as_sent(pa):
    inner = pa._emacs

    async def stubborn(form, timeout=60):
        if "claude-email-send-buffer" in form:
            pa.calls.append(form)
            return "nil"  # Gnus refused: the buffer stays
        return await inner(form, timeout)

    pa._emacs = stubborn
    with pytest.raises(PAError, match="kept the draft"):
        await pa.send_email("mail:1", None)
    assert pa.get({})["briefing"]["items"][0]["status"] == "open"


def test_normalise_keeps_usable_parts_of_a_sloppy_briefing():
    from agentdash.node.pa import normalise

    b = normalise(
        {
            "summary": 5,
            "items": [
                "junk",
                {
                    "subject": "A",
                    "urgency": "ASAP",
                    "draft": {"kind": "email_reply", "body": "  "},
                    "task": {"prompt": "do it"},
                },
                {"id": 7, "urgency": "now", "draft": {"body": "hi"}},
            ],
            "deadlines": [{"date": "2026-09-20", "what": "x"}, 3],
        }
    )
    a, c = b["items"]
    assert a["id"] == "item:1" and a["urgency"] == "week" and "draft" not in a
    assert a["task"] == {"prompt": "do it", "title": "A"}
    assert c["id"] == "7" and c["draft"]["kind"] == "" and b["summary"] == "5"
    assert b["deadlines"] == [{"date": "2026-09-20", "what": "x", "project": ""}]
