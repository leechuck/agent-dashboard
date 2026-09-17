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
