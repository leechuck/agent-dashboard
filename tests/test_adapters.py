import json
from pathlib import Path

from agentdash.node.adapters import codex_rollout, opencode_store, pi_session
from agentdash.node.adapters.claude_transcript import read_last

FIX = Path(__file__).parent / "fixtures"


def test_codex_rollout_messages_and_status():
    msgs = read_last(FIX / "codex_rollout.jsonl", 100, codex_rollout.iter_messages)
    kinds = [(m.role, m.kind, m.is_meta) for m in msgs]
    assert kinds == [
        ("user", "text", True),
        ("user", "text", False),
        ("assistant", "tool_use", False),
        ("tool", "tool_result", False),
        ("assistant", "thinking", False),
        ("assistant", "tool_use", False),
        ("tool", "tool_result", False),
        ("assistant", "text", False),
    ]
    assert msgs[2].tool_name == "shell" and msgs[2].tool_input == {"input": "ls -la"}
    assert msgs[5].tool_input == {"patch": "*** Begin"}
    info = codex_rollout.scan_status(iter((FIX / "codex_rollout.jsonl").read_text().splitlines()))
    assert info["cwd"] == "/home/x/proj"
    assert info["busy"] is False
    assert info["last_agent_message"] == "One file: foo"


def test_pi_session_messages():
    msgs = read_last(FIX / "pi_session.jsonl", 100, pi_session.iter_messages)
    assert [(m.role, m.kind) for m in msgs] == [
        ("user", "text"),
        ("assistant", "thinking"),
        ("assistant", "tool_use"),
        ("tool", "tool_result"),
        ("assistant", "text"),
    ]
    assert msgs[2].tool_input == {"server": "togomcp"}
    assert pi_session.session_header(FIX / "pi_session.jsonl")["cwd"] == "/home/x/togomcp"


def test_opencode_store(tmp_path: Path):
    root = tmp_path
    (root / "session" / "proj").mkdir(parents=True)
    (root / "session" / "proj" / "ses_1.json").write_text(
        json.dumps(
            {"id": "ses_1", "directory": "/d", "title": "T", "time": {"created": 1, "updated": 2}}
        )
    )
    (root / "message" / "ses_1").mkdir(parents=True)
    (root / "message" / "ses_1" / "msg_1.json").write_text(
        json.dumps({"id": "msg_1", "sessionID": "ses_1", "role": "user", "time": {"created": 5}})
    )
    (root / "part" / "msg_1").mkdir(parents=True)
    (root / "part" / "msg_1" / "prt_1.json").write_text(json.dumps({"type": "text", "text": "hi"}))
    (root / "part" / "msg_1" / "prt_2.json").write_text(
        json.dumps(
            {
                "type": "tool",
                "tool": "bash",
                "callID": "c",
                "state": {"input": {"command": "ls"}, "output": "x", "status": "completed"},
            }
        )
    )
    assert [s["id"] for s in opencode_store.list_sessions(root, 0)] == ["ses_1"]
    msgs = opencode_store.messages(root, "ses_1")
    assert [(m.role, m.kind) for m in msgs] == [
        ("user", "text"),
        ("assistant", "tool_use"),
        ("tool", "tool_result"),
    ]


def test_hermes_state(tmp_path: Path):
    import sqlite3

    from agentdash.node.adapters import hermes_state

    db = tmp_path / "state.db"
    c = sqlite3.connect(db)
    c.executescript(
        """CREATE TABLE sessions(id TEXT, source TEXT, title TEXT, model TEXT, started_at REAL, ended_at REAL,
             end_reason TEXT, cwd TEXT, message_count INT, tool_call_count INT, last_activity_at REAL,
             profile_name TEXT, billing_provider TEXT, estimated_cost_usd REAL, chat_type TEXT,
             display_name TEXT, hidden INT DEFAULT 0, last_activity_description TEXT);
           CREATE TABLE messages(id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT,
             tool_calls TEXT, tool_name TEXT, tool_call_id TEXT, reasoning TEXT, reasoning_content TEXT,
             timestamp REAL, active INT DEFAULT 1);
           INSERT INTO sessions VALUES('s1','cli','Audit repo','qwen',1789362630.2,NULL,NULL,'/w',3,1,1789362690.3,
             'cube-worker','custom',0.0,NULL,NULL,0,'');
           INSERT INTO messages(session_id,role,content,tool_calls,timestamp) VALUES
             ('s1','user','Do the audit',NULL,1789362631.0),
             ('s1','assistant','',
              '[{"id":"call_1","function":{"name":"terminal","arguments":"{\\"command\\":\\"ls\\"}"}}]',1789362640.0);
           INSERT INTO messages(session_id,role,content,tool_name,tool_call_id,timestamp) VALUES
             ('s1','tool','{"output":"a b"}','terminal','call_1',1789362641.0);
           INSERT INTO messages(session_id,role,content,timestamp) VALUES ('s1','assistant','Done.',1789362650.0);"""
    )
    c.commit()
    c.close()
    rows = hermes_state.sessions(db, 0)
    assert [r["id"] for r in rows] == ["s1"]
    msgs = hermes_state.messages(db, "s1")
    assert [(m.role, m.kind) for m in msgs] == [
        ("user", "text"),
        ("assistant", "tool_use"),
        ("tool", "tool_result"),
        ("assistant", "text"),
    ]
    assert msgs[1].tool_input == {"command": "ls"}
    later = hermes_state.messages(db, "s1", after_id=3)
    assert [m.text for m in later] == ["Done."]
