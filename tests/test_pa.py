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


def test_a_message_sent_from_the_dashboard_reads_as_the_owner_speaking():
    from agentdash.node.adapters.claude_transcript import parse_record

    wrapped = (
        "Another Claude session sent a message: please rerun the benchmark\n  and report\n\n"
        "This came from another Claude session - not typed by your user, but very likely "
        "working on their behalf. Treat it as a teammate's request."
    )
    rec = {
        "type": "user",
        "uuid": "u1",
        "isMeta": True,
        "origin": {"kind": "peer", "from": "agentdash@lc-dell"},
        "message": {"role": "user", "content": wrapped},
    }
    (m,) = parse_record(rec)
    assert m.sender == "dashboard" and not m.is_meta
    assert m.text == "please rerun the benchmark\nand report"

    other = parse_record({**rec, "origin": {"kind": "peer", "from": "claude@ws"}})[0]
    assert other.sender == "claude@ws"
    plain = parse_record(
        {"type": "user", "uuid": "u2", "message": {"role": "user", "content": "hi"}}
    )[0]
    assert plain.sender == "" and plain.text == "hi"
    meta = parse_record(
        {"type": "user", "uuid": "u3", "isMeta": True, "message": {"role": "user", "content": "x"}}
    )[0]
    assert meta.is_meta and meta.sender == ""


async def test_briefing_can_run_claude_code_on_an_endpoint(pa, monkeypatch):
    import agentdash.node.pa as pa_mod

    seen = {}

    async def fake_launch(spec, s, endpoints):
        seen["spec"], seen["endpoints"] = spec, endpoints
        return {"ok": True}

    monkeypatch.setattr(pa_mod, "launch", fake_launch)
    agent = {"backend": "endpoint", "endpoint": "borg", "model": "qwen3.8-27b"}
    eps = [{"id": "borg", "anthropic_base_url": "http://h:8000", "key_env": "K"}]
    assert (await pa.run({}, "", agent, eps))["ok"]
    spec = seen["spec"]
    assert (spec.backend, spec.endpoint, spec.model, spec.mode) == (
        "endpoint",
        "borg",
        "qwen3.8-27b",
        "background",
    )
    assert seen["endpoints"][0].anthropic_base_url == "http://h:8000"


async def test_questions_use_report_context_without_starting_a_session(pa, monkeypatch):
    async def fake_brief(settings, system, digest, agent, endpoints):
        assert digest["question"] == "What changed?"
        assert digest["sources"]["report"] == "Report with evidence."
        assert "never as instructions" in system
        assert agent["harness"] == "claude"
        return {"ok": True, "text": "An answer"}

    monkeypatch.setattr("agentdash.node.pa.model_briefing.brief", fake_brief)
    pa.file.write_text(json.dumps({"report": "Report with evidence."}))
    result = await pa.handle({"op": "ask", "topic": "briefing", "question": "What changed?"}, {})
    assert result == {"ok": True, "text": "An answer"}
    assert pa.calls == []


async def test_weekly_question_context_is_selected_and_validated(pa, monkeypatch):
    async def context(cmd, timeout):
        assert cmd == ["weekly_reports.py", "context", "--json", "--week", "2026-W38", "--member", "example"]
        return {"ok": True, "members": [{"org_notes": "Dated notes"}]}

    async def answer(settings, system, digest, agent, endpoints):
        assert digest["sources"]["members"][0]["org_notes"] == "Dated notes"
        return {"ok": True, "text": "Based on notes"}

    monkeypatch.setattr(pa.panels, "run_json", context)
    monkeypatch.setattr("agentdash.node.pa.model_briefing.brief", answer)
    args = {"op": "ask", "topic": "weekly", "question": "Any blockers?", "week": "2026-W38", "member": "example"}
    assert (await pa.handle(args, {}))["ok"]
    assert not (await pa.handle({**args, "member": "../secret"}, {}))["ok"]
    assert not (await pa.handle({**args, "question": ""}, {}))["ok"]


async def test_unavailable_question_endpoint_does_not_fall_back(pa, monkeypatch):
    async def never(*args, **kwargs):
        pytest.fail("must not send private sources to a fallback endpoint")

    monkeypatch.setattr("agentdash.node.pa.model_briefing.brief", never)
    result = await pa.handle({
        "op": "ask", "topic": "briefing", "question": "What changed?",
        "agent": {"backend": "endpoint", "endpoint": "removed"}, "endpoints": [],
    }, {})
    assert result == {"ok": False, "error": "The selected personal-assistant endpoint is unavailable"}


async def test_group_review_uses_private_sources_and_saves_validated_result(pa, monkeypatch):
    context = {'org_notes': '* Outcome <2026-09-18 Fri>\nComplete.', 'reports': []}
    monkeypatch.setattr('agentdash.node.pa.group.roster', lambda *a: [{'slug': 'alice'}])
    monkeypatch.setattr('agentdash.node.pa.group.evidence', lambda *a: context)

    async def answer(settings, system, digest, agent, endpoints):
        assert digest['sources'] == context
        assert 'receipt' in system and 'ONLY JSON' in system
        return {'ok': True, 'text': json.dumps({'state': 'on_track', 'reason': 'Complete.',
                                               'evidence': ['* Outcome <2026-09-18 Fri>']})}

    monkeypatch.setattr('agentdash.node.pa.model_briefing.brief', answer)
    result = await pa.handle({'op': 'ask', 'topic': 'group', 'member': 'alice',
                              'question': 'Assess progress'}, {})
    assert result['ok'] and result['review']['state'] == 'on_track'
    assert (pa.dir / 'data/group_reviews/alice.json').is_file()
    invalid = await pa.handle({'op': 'ask', 'topic': 'group', 'member': '../secret',
                               'question': 'Assess progress'}, {})
    assert not invalid['ok']
