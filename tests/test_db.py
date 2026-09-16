from pathlib import Path

from agentdash.db import Database
from agentdash.models import Machine, Session


async def test_roster_replace_marks_done(tmp_path: Path):
    db = Database(tmp_path / "h.db")
    await db.open()
    await db.upsert_machine(Machine(id="m", online=True))
    a = Session(key="m:claude:a", machine="m", harness="claude", session_id="a", status="busy")
    b = Session(key="m:claude:b", machine="m", harness="claude", session_id="b", status="idle")
    changed = await db.replace_sessions("m", [a, b])
    assert {s.key for s in changed} == {a.key, b.key}
    # unchanged roster: nothing changes (updated_at differs, so re-send same objects)
    changed = await db.replace_sessions("m", [a, b])
    assert changed == []
    # b disappears -> done
    changed = await db.replace_sessions("m", [a])
    assert [(s.key, s.status) for s in changed] == [(b.key, "done")]
    active = await db.list_sessions("m", active_only=True)
    assert [s.key for s in active] == [a.key]
    await db.set_machine_online("m", False)
    after = {s.key: s.status for s in await db.list_sessions("m")}
    assert after == {a.key: "offline", b.key: "done"}
    await db.close()
