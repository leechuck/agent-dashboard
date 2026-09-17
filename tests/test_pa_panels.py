"""The node runs PA-repo scripts for the Personal tab. Here the scripts are fakes that
record their arguments, so the tests pin what reaches a script and what never does."""

import json

import pytest

from agentdash.node.pa_panels import ACTS, PANELS, PanelError, Panels

FAKE = """import json, sys
from pathlib import Path
log = Path(__file__).with_name("calls.jsonl")
with log.open("a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
n = len(log.read_text().splitlines())
{body}
"""


def fake(repo, name: str, body: str = 'print(json.dumps({"ok": True, "n": n}))') -> None:
    (repo / "scripts" / name).write_text(FAKE.format(body=body))


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "scripts").mkdir()
    fake(tmp_path, "todo.py")
    return tmp_path


def calls(repo) -> list[list[str]]:
    f = repo / "scripts" / "calls.jsonl"
    return [json.loads(ln) for ln in f.read_text().splitlines()] if f.exists() else []


async def test_panel_is_cached_until_an_act_makes_it_stale(repo):
    p = Panels(repo)
    assert (await p.panel("todo"))["n"] == 1
    assert (await p.panel("todo"))["n"] == 1  # served from memory
    assert (await p.panel("todo", fresh=True))["n"] == 2
    await p.act("todo_done", {"id": "0123abcd"})
    assert (await p.panel("todo"))["n"] == 4
    assert calls(repo)[2] == ["done", "--json", "--commit", "--", "0123abcd"]


async def test_text_is_one_argument_after_the_separator(repo):
    p = Panels(repo)
    text = "--commit; rm -rf ~ $(reboot)\n`x`"
    await p.act("todo_add", {"date": "2026-10-01", "text": text, "project": "my-paper"})
    argv = calls(repo)[0]
    assert argv[:7] == ["add", "--json", "--commit", "--date", "2026-10-01", "--list", "PA"]
    assert argv[-2:] == ["--", "--commit; rm -rf ~ $(reboot) `x`"]
    assert argv[7:9] == ["--project", "my-paper"]


async def test_calendar_add_has_no_way_to_carry_guests(repo):
    fake(repo, "agenda.py")
    await Panels(repo).act(
        "calendar_add",
        {
            "title": "--attendees=x@example.org",
            "start": "2026-09-18T15:00:00+09:00",
            "minutes": 45,
            "attendees": "boss@example.org",
            "description": "notes",
            "location": "Room 1\n--attendees=y@example.org",
        },
    )
    argv = calls(repo)[0]
    assert argv == [
        "add",
        "--json",
        "--title=--attendees=x@example.org",
        "--start=2026-09-18T15:00:00+09:00",
        "--minutes=45",
        "--calendar=work",
        "--location=Room 1 --attendees=y@example.org",
    ]
    await Panels(repo).act("calendar_remind", {"title": "Vote", "start": "2026-09-18T09:00:00Z"})
    assert calls(repo)[1][0] == "remind"


@pytest.mark.parametrize(
    ("act", "args"),
    [
        ("todo_done", {"id": "../../x"}),
        ("todo_done", {"id": "0123ABCD"}),
        ("todo_done", {}),
        ("todo_snooze", {"id": "0123abcd", "until": "tomorrow"}),
        ("todo_due", {"id": "0123abcd", "date": "2026-13-01x"}),
        ("todo_add", {"date": "2026-10-01", "text": "  "}),
        ("todo_add", {"date": "2026-10-01", "text": "x", "list": "--org"}),
        ("todo_add", {"date": "2026-10-01", "text": "x", "project": "../kg"}),
        ("todo_add", {"date": "2026-10-01", "text": "x" * 2001}),
        ("calendar_add", {"title": "x", "start": "2026-09-18 15:00"}),
        ("calendar_add", {"title": "x", "start": "2026-09-18T15:00:00"}),
        ("calendar_add", {"title": "x", "start": "2026-09-18", "all_day": False}),
        ("calendar_add", {"title": "x", "start": "2026-09-18T15:00:00Z", "minutes": "1e9"}),
        ("calendar_add", {"title": "x", "start": "2026-09-18T15:00:00Z", "calendar": "../x"}),
        ("calendar_add", {"title": "x", "start": "2026-09-18T15:00:00Z", "reminder": "1w;id"}),
        ("calendar_remind", {"title": "", "start": "2026-09-18T15:00:00Z"}),
        ("shell", {"cmd": "id"}),
    ],
)
async def test_bad_input_never_reaches_a_script(repo, act, args):
    with pytest.raises(PanelError):
        await Panels(repo).act(act, args)
    assert calls(repo) == []


async def test_a_failing_script_reports_its_own_words(repo):
    fake(repo, "todo.py", 'print(json.dumps({"ok": False, "error": "no item with id"})); sys.exit(1)')
    r = await Panels(repo).act("todo_done", {"id": "0123abcd"})
    assert r == {"ok": False, "error": "no item with id"}
    fake(repo, "todo.py", 'print("Traceback"); sys.exit(2)')
    with pytest.raises(PanelError, match="failed \\(2\\): Traceback"):
        await Panels(repo).panel("todo")


async def test_failed_results_are_not_cached_and_missing_scripts_say_so(repo):
    fake(repo, "todo.py", 'print(json.dumps({"ok": n > 1, "n": n}))')
    p = Panels(repo)
    assert (await p.panel("todo"))["ok"] is False
    assert (await p.panel("todo"))["n"] == 2
    (repo / "scripts" / "todo.py").unlink()
    with pytest.raises(PanelError, match="no scripts/todo.py"):
        await p.panel("todo", fresh=True)
    with pytest.raises(PanelError, match="unknown panel"):
        await p.panel("passwd")


async def test_a_hanging_script_is_killed(repo, monkeypatch):
    fake(repo, "todo.py", "import time; time.sleep(30)")
    monkeypatch.setitem(PANELS, "todo", (["todo.py", "list", "--json"], 20, 1))
    with pytest.raises(PanelError, match="did not finish"):
        await Panels(repo).panel("todo")


def test_every_act_names_panels_that_exist():
    for _, _, stale in ACTS.values():
        assert set(stale) <= set(PANELS)
