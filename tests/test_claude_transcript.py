from pathlib import Path

from agentdash.node.adapters.claude_transcript import (
    TranscriptTail,
    last_line_preview,
    read_last,
)

FIX = Path(__file__).parent / "fixtures" / "claude_transcript.jsonl"


def test_parse_kinds():
    msgs = read_last(FIX, 100)
    kinds = [(m.role, m.kind) for m in msgs]
    assert kinds == [
        ("user", "text"),
        ("assistant", "thinking"),
        ("assistant", "tool_use"),
        ("tool", "tool_result"),
        ("assistant", "text"),
        ("user", "text"),
    ]
    tool = msgs[2]
    assert tool.tool_name == "Bash"
    assert tool.tool_input == {"command": "ls -la", "description": "List files"}
    assert msgs[3].tool_use_id == "toolu_1"
    assert msgs[3].text.startswith("total 0")
    assert msgs[5].is_meta is True
    assert msgs[0].ts == 1789542000000


def test_read_last_limits():
    assert len(read_last(FIX, 2)) == 2


def test_preview_skips_meta():
    assert last_line_preview(FIX) == "One file: foo.txt"


def test_tail_incremental(tmp_path: Path):
    p = tmp_path / "t.jsonl"
    lines = FIX.read_text().splitlines()
    p.write_text("\n".join(lines[:4]) + "\n")
    tail = TranscriptTail(p)
    first = tail.read_new()
    assert [m.kind for m in first] == ["text", "thinking"]
    # append a partial line then complete it
    with p.open("a") as f:
        f.write(lines[4][:40])
    assert tail.read_new() == []
    with p.open("a") as f:
        f.write(lines[4][40:] + "\n" + lines[6] + "\n")
    more = tail.read_new()
    assert [m.kind for m in more] == ["tool_use", "text"]
