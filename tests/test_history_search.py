import json
from types import SimpleNamespace

import pytest

from agentdash.models import Session
from agentdash.node import past


def session(machine, sid, updated=0, **kwargs):
    return Session(
        key=f"{machine}:claude:{sid}",
        machine=machine,
        harness="claude",
        session_id=sid,
        updated_at=updated,
        **kwargs,
    )


def test_search_old_sessions_and_dashboard_titles_before_pagination(tmp_path):
    rows = [session("ws", str(i), i) for i in range(250)]
    old = session("lc-dell", "old", name="Old project description")
    rows.append(old)
    assert past.search_sessions(rows, "project description", set(), {}) == [old]
    assert past.search_sessions(rows, "renamed title", set(), {old.key: "Renamed title"}) == [old]
    assert not past.search_sessions(rows, "renamed title", {old.key}, {old.key: "Renamed title"})


@pytest.mark.parametrize(
    "record",
    [
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "An older café response"}]},
        },
        {
            "type": "response_item",
            "payload": {"content": [{"type": "output_text", "text": "An older café response"}]},
        },
    ],
)
def test_content_search_reads_decoded_text_and_returns_excerpt(tmp_path, record):
    path = tmp_path / "log.jsonl"
    path.write_text("bad line\n" + json.dumps(record) + "\n")
    s = session("lc-dell", "old", transcript_path=str(path))
    assert not past.search_sessions([s], "café response", set(), {})
    found = past.search_sessions([s], "café response", set(), {}, True)
    assert found == [s]
    assert "café response" in found[0].extra["search_excerpt"]
    assert not past.search_sessions([s], "absent word", set(), {}, True)


@pytest.mark.asyncio
async def test_global_pagination_preserves_older_laptop_results(monkeypatch):
    from agentdash.hub import api

    all_rows = {
        "ws": [session("ws", "new", 3)],
        "lc-dell": [session("lc-dell", "older", 2), session("lc-dell", "oldest", 1)],
    }
    st = SimpleNamespace(nodes=dict.fromkeys(all_rows), past={}, titler=SimpleNamespace(titles={}))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(hub=st)))

    async def call(request, machine, action, payload, timeout):
        assert payload["limit"] == 3
        return {"ok": True, "sessions": [s.model_dump() for s in all_rows[machine]]}

    monkeypatch.setattr(api, "_node_call", call)
    result = await api.past_sessions(request, limit=1, offset=1, content=False)
    assert [s["key"] for s in result["sessions"]] == ["lc-dell:claude:older"]
    assert result["has_more"]
    assert st.past["lc-dell:claude:older"].machine == "lc-dell"


def test_codex_archive_can_read_beyond_roster_limit(tmp_path):
    import sqlite3

    from agentdash.node.collectors.codex import CodexCollector

    home = tmp_path / ".codex"
    home.mkdir()
    with sqlite3.connect(home / "state_5.sqlite") as db:
        columns = [name.strip() for name in CodexCollector._COLS.split(",")]
        db.execute("CREATE TABLE threads (" + ", ".join(f"{c} TEXT" for c in columns) + ")")
        db.executemany(
            "INSERT INTO threads (id, updated_at_ms) VALUES (?, ?)",
            [(str(i), str(i).zfill(4)) for i in range(250)],
        )
    collector = CodexCollector("lc-dell", home)
    assert len(collector._query("1=1", ())) == 200
    assert len(collector._query("1=1", (), None)) == 250
