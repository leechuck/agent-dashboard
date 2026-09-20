from unittest.mock import AsyncMock

import pytest

from agentdash.config import Settings
from agentdash.hub.api import StartBody, SwitchBody
from agentdash.node import launcher
from agentdash.node.adapters.tmux_keys import TmuxSendError
from agentdash.node.launcher import LaunchError, LaunchSpec, build, launch, set_codex_mode


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/bin/" + name)
    monkeypatch.setattr(launcher, "start_background", AsyncMock(return_value={"ok": True}))
    return Settings(state_dir=tmp_path), tmp_path / "new" / "project with spaces"


async def test_missing_directory_requires_opt_in(setup):
    settings, folder = setup
    spec = LaunchSpec(cwd=str(folder), mode="background", prompt="plan the project")
    with pytest.raises(LaunchError, match="Create directory"):
        await launch(spec, settings, [])
    assert not folder.exists()
    launcher.start_background.assert_not_awaited()
    spec.create_dir = True
    spec.work_mode = "plan"
    assert (await launch(spec, settings, []))["ok"]
    assert folder.is_dir()
    assert launcher.start_background.call_args.args[0] == str(folder)
    assert launcher.start_background.call_args.kwargs["permission_mode"] == "plan"
    # Existing folders are safe to reuse, including their contents.
    (folder / "keep").write_text("keep")
    await launch(spec, settings, [])
    assert (folder / "keep").read_text() == "keep"


async def test_directory_creation_error_is_actionable(setup):
    settings, folder = setup
    folder.parent.mkdir()
    folder.write_text("file")
    with pytest.raises(LaunchError, match="cannot create directory"):
        await launch(LaunchSpec(cwd=str(folder), create_dir=True), settings, [])
    launcher.start_background.assert_not_awaited()


def test_start_api_preserves_mode_and_directory_choice():
    body = StartBody(cwd="/new", create_dir=True, work_mode="plan")
    spec = LaunchSpec.parse(body.model_dump())
    assert spec.create_dir is True and spec.work_mode == "plan"
    assert SwitchBody(work_mode="implement").model_dump()["work_mode"] == "implement"
    with pytest.raises(LaunchError, match="boolean"):
        LaunchSpec.parse({"create_dir": "false"})
    with pytest.raises(LaunchError, match="unknown work mode"):
        LaunchSpec.parse({"work_mode": "wrong"})
    with pytest.raises(LaunchError, match="not supported"):
        LaunchSpec.parse({"harness": "pi", "work_mode": "plan"})


@pytest.mark.parametrize("work_mode,expected", [("plan", "plan"), ("implement", "default")])
async def test_claude_mode_is_explicit_for_interactive_and_background(setup, work_mode, expected):
    settings, folder = setup
    folder.mkdir(parents=True)
    spec = LaunchSpec(cwd=str(folder), work_mode=work_mode, prompt="task", mode="background")
    argv, _ = build(spec, settings, [])
    assert argv[argv.index("--permission-mode") + 1] == expected
    await launch(spec, settings, [])
    assert launcher.start_background.call_args.kwargs["permission_mode"] == expected


def test_planning_overrides_claude_bypass(setup):
    settings, _ = setup
    argv, _ = build(LaunchSpec(work_mode="plan", permissions="bypass"), settings, [])
    assert "--dangerously-skip-permissions" not in argv
    assert argv[-2:] == ["--permission-mode", "plan"]


def test_codex_task_is_not_passed_before_mode_selection(setup):
    settings, _ = setup
    argv, _ = build(LaunchSpec(harness="codex", work_mode="plan", prompt="do work"), settings, [])
    assert "do work" not in argv


@pytest.mark.parametrize("mode,agent", [("plan", "plan"), ("implement", "build")])
def test_opencode_modes_and_resume(setup, mode, agent):
    settings, _ = setup
    argv, _ = build(LaunchSpec(harness="opencode", work_mode=mode, resume="sid"), settings, [])
    assert argv[argv.index("--agent") + 1] == agent
    assert argv[argv.index("--session") + 1] == "sid"


DEFAULT = (
    "Earlier mention of Plan mode\n› Ask Codex to do anything\n  model · /work · ← for agents\n\n"
)
PLAN = "› Ask Codex to do anything\n  model · /work · ← for agents   Plan mode (shift+tab to cycle)\n\n"


@pytest.mark.parametrize(
    "mode,before,after", [("plan", DEFAULT, PLAN), ("implement", PLAN, DEFAULT)]
)
async def test_codex_native_mode_is_verified(monkeypatch, mode, before, after):
    monkeypatch.setattr(launcher, "capture", AsyncMock(side_effect=[before, after]))
    monkeypatch.setattr(launcher, "type_prompt", AsyncMock())
    monkeypatch.setattr(launcher, "send_keys", AsyncMock())
    await set_codex_mode("default", "test:0.0", mode)
    if mode == "plan":
        launcher.type_prompt.assert_awaited_once_with("default", "test:0.0", "/plan")
        launcher.send_keys.assert_not_awaited()
    else:
        launcher.send_keys.assert_awaited_once_with("default", "test:0.0", ["BTab"])
        launcher.type_prompt.assert_not_awaited()


async def test_codex_never_types_a_mode_into_trust_dialog(monkeypatch):
    monkeypatch.setattr(
        launcher, "capture", AsyncMock(return_value="Do you trust?\n› 1. Yes\n  2. No")
    )
    monkeypatch.setattr(launcher, "type_prompt", AsyncMock())
    monkeypatch.setattr(launcher, "send_keys", AsyncMock())
    with pytest.raises(TmuxSendError, match="trust or login"):
        await set_codex_mode("default", "test:0.0", "plan")
    launcher.type_prompt.assert_not_awaited()
    launcher.send_keys.assert_not_awaited()
