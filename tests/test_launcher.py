import json

import pytest

from agentdash.config import Settings
from agentdash.node import launcher
from agentdash.node.launcher import Endpoint, LaunchError, LaunchSpec, build


@pytest.fixture
def s(tmp_path, monkeypatch):
    monkeypatch.setattr("agentdash.config.STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(launcher.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(launcher.Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude-team").mkdir()
    monkeypatch.setenv("BORG_KEY", "sk-secret")
    return Settings(claude_config_dirs=[tmp_path / ".claude", tmp_path / ".claude-team"])


BORG = Endpoint.parse(
    {
        "id": "BORG!",
        "name": "Qwen",
        "base_url": "http://u:8000/v1",
        "anthropic_base_url": "http://u:8000",
        "key_env": "BORG_KEY",
        "wire_api": "responses",
        "context_window": 131072,
        "models": ["qwen"],
    }
)


def test_endpoint_ids_are_slugs_and_specs_are_validated():
    assert BORG.id == "borg"
    with pytest.raises(LaunchError):
        LaunchSpec.parse({"harness": "bash", "cwd": "/"})
    with pytest.raises(LaunchError):
        LaunchSpec.parse({"harness": "claude", "permissions": "yolo"})
    old = LaunchSpec.parse({"cwd": "/w", "config_dir": ".claude-team", "permission_mode": "plan"})
    assert (old.backend, old.login, old.permissions) == ("login", ".claude-team", "plan")


def test_claude_on_a_second_login_and_with_resume(s, tmp_path):
    spec = LaunchSpec.parse(
        {
            "harness": "claude",
            "cwd": "/w",
            "backend": "login",
            "login": ".claude-team",
            "model": "opus",
            "effort": "high",
            "resume": "abc",
            "prompt": "go on",
        }
    )
    argv, env = build(spec, s, [])
    assert env == {"CLAUDE_CONFIG_DIR": str(tmp_path / ".claude-team")}
    assert argv == [
        "/bin/claude",
        "--resume",
        "abc",
        "--model",
        "opus",
        "--effort",
        "high",
        "go on",
    ]
    with pytest.raises(LaunchError, match="no Claude login"):
        build(LaunchSpec.parse({"cwd": "/w", "backend": "login", "login": ".claude-nope"}), s, [])


def test_claude_on_a_custom_endpoint_gets_its_own_config_dir_and_no_key_in_argv(s, tmp_path):
    spec = LaunchSpec.parse(
        {
            "harness": "claude",
            "cwd": "/w",
            "backend": "endpoint",
            "endpoint": "borg",
            "model": "qwen",
        }
    )
    argv, env = build(spec, s, [BORG])
    assert (
        env["ANTHROPIC_BASE_URL"] == "http://u:8000" and env["ANTHROPIC_AUTH_TOKEN"] == "sk-secret"
    )
    assert (
        env["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "qwen"
        and env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == "131072"
    )
    assert (
        env["CLAUDE_CONFIG_DIR"] == str(tmp_path / ".claude-borg")
        and (tmp_path / ".claude-borg").is_dir()
    )
    assert "sk-secret" not in " ".join(argv) and "--model" not in argv


def test_codex_and_pi_on_a_custom_endpoint(s, tmp_path):
    spec = LaunchSpec.parse(
        {
            "harness": "codex",
            "cwd": "/w",
            "backend": "endpoint",
            "endpoint": "borg",
            "model": "qwen",
            "effort": "medium",
            "permissions": "bypass",
            "prompt": "hi",
        }
    )
    argv, env = build(spec, s, [BORG])
    assert 'model_provider="borg"' in argv and 'model_providers.borg.wire_api="responses"' in argv
    assert 'model_providers.borg.env_key="BORG_KEY"' in argv and env == {"BORG_KEY": "sk-secret"}
    assert argv[-2:] == [
        "--dangerously-bypass-approvals-and-sandbox",
        "hi",
    ] and "sk-secret" not in " ".join(argv)
    resumed, _ = build(
        LaunchSpec.parse({"harness": "codex", "cwd": "/w", "resume": "t1", "model": "gpt-x"}), s, []
    )
    assert resumed[:3] == ["/bin/codex", "resume", "t1"]

    argv, env = build(
        LaunchSpec.parse(
            {
                "harness": "pi",
                "cwd": "/w",
                "backend": "endpoint",
                "endpoint": "borg",
                "model": "qwen",
            }
        ),
        s,
        [BORG],
    )
    assert argv[1:3] == ["--model", "borg/qwen"]
    written = json.loads((tmp_path / ".pi/agent/models.json").read_text())["providers"]["borg"]
    assert written["apiKey"] == "$BORG_KEY" and written["models"][0]["id"] == "qwen"


def test_missing_key_or_model_is_explained(s, monkeypatch):
    monkeypatch.delenv("BORG_KEY")
    with pytest.raises(LaunchError, match="BORG_KEY is not set"):
        build(
            LaunchSpec.parse(
                {
                    "harness": "codex",
                    "cwd": "/w",
                    "backend": "endpoint",
                    "endpoint": "borg",
                    "model": "q",
                }
            ),
            s,
            [BORG],
        )
    monkeypatch.setenv("BORG_KEY", "k")
    with pytest.raises(LaunchError, match="choose a model"):
        build(
            LaunchSpec.parse(
                {"harness": "codex", "cwd": "/w", "backend": "endpoint", "endpoint": "borg"}
            ),
            s,
            [BORG],
        )


def test_key_added_to_env_file_is_picked_up_without_restart(s, monkeypatch):
    monkeypatch.delenv("BORG_KEY")
    (s.state_dir / ".env").write_text("OTHER=1\nBORG_KEY='from-file'\n")
    assert launcher.secret(s, "BORG_KEY") == "from-file" and launcher.secret(s, "NOPE") == ""


def test_handover_prompt_carries_the_state():
    from agentdash.models import Harness, Session

    old = Session(
        key="m:codex:1",
        machine="m",
        harness=Harness.codex,
        session_id="1",
        model="gpt-x",
        transcript_path="/t/r.jsonl",
        last_line="benchmarks queued",
        extra={"goal": "ship v1.4", "last_user": "status?"},
    )
    text = launcher.handover_prompt(old, "finish the report")
    assert "/t/r.jsonl" in text and "ship v1.4" in text and "finish the report" in text


def test_slash_commands_come_from_the_harness_and_the_files_around_it(tmp_path, monkeypatch):
    from agentdash.node import commands

    home, work = tmp_path / "home", tmp_path / "work"
    (home / ".claude" / "commands" / "team").mkdir(parents=True)
    (home / ".claude" / "commands" / "note.md").write_text(
        "---\ndescription: Take a note\nargument-hint: <text>\n---\nbody"
    )
    (home / ".claude" / "commands" / "team" / "standup.md").write_text(
        "# Ask everyone for a status"
    )
    (home / ".claude" / "skills" / "gog").mkdir(parents=True)
    (home / ".claude" / "skills" / "gog" / "SKILL.md").write_text(
        "---\nname: gog\ndescription: Google Workspace CLI\n---"
    )
    plug = home / ".claude" / "plugins" / "cache" / "caveman" / "commands"
    plug.mkdir(parents=True)
    (plug / "stats.md").write_text("Token stats")
    (home / ".claude" / "plugins" / "installed_plugins.json").write_text(
        json.dumps(
            {
                "plugins": {
                    "caveman@market": [{"installPath": str(plug.parent)}],
                    "uninstalled@market": [{"installPath": "/nope"}],
                }
            }
        )
    )
    (work / ".claude" / "commands").mkdir(parents=True)
    (work / ".claude" / "commands" / "deploy.md").write_text(
        "---\ndescription: Deploy this project\n---"
    )
    monkeypatch.setattr(commands.Path, "home", classmethod(lambda cls: home))

    by = {c["name"]: c for c in commands.for_session("claude", str(work))}
    assert by["compact"]["source"] == "builtin"
    assert by["note"] == {
        "name": "note",
        "description": "Take a note",
        "source": "user",
        "args": "<text>",
        "options": [],
        "free": True,  # a hint with no list means free text
    }
    assert by["team:standup"]["description"] == "Ask everyone for a status"
    assert by["deploy"]["source"] == "project" and by["gog"]["source"] == "skill"
    assert by["caveman:stats"]["source"] == "plugin"
    # builtins first, then the project's own, and every name appears once
    names = [c["name"] for c in commands.for_session("claude", str(work))]
    assert len(names) == len(set(names)) and names.index("compact") < names.index("deploy")
    assert {c["name"] for c in commands.for_session("codex", str(work))} >= {"approvals", "diff"}


def test_a_command_says_what_may_follow_it(tmp_path, monkeypatch):
    from agentdash.node import commands

    monkeypatch.setattr(commands.Path, "home", classmethod(lambda cls: tmp_path))
    models = [
        {"id": "", "label": "as configured"},
        {"id": "fable", "label": "Fable"},
        {"id": "haiku", "label": "Haiku"},
    ]
    by = {c["name"]: c for c in commands.for_session("claude", str(tmp_path), models=models)}
    assert [o["value"] for o in by["model"]["options"]] == [
        "fable",
        "haiku",
    ]  # the blank is not a choice
    assert by["model"]["args"] == "<model>" and not by["model"]["free"]
    assert [o["value"] for o in by["effort"]["options"]] == [
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]
    assert by["compact"]["free"] and by["compact"]["args"] == "[what to keep]"
    assert by["clear"]["options"] == [] and not by["clear"]["free"]  # takes nothing
    codex = {c["name"]: c for c in commands.for_session("codex", str(tmp_path), models=models)}
    assert [o["value"] for o in codex["approvals"]["options"]] == [
        "read-only",
        "auto",
        "full-access",
    ]


def test_a_tmux_server_name_becomes_a_socket_path(monkeypatch):
    from agentdash.node.adapters.tmux_keys import socket_path

    monkeypatch.setattr("os.getuid", lambda: 1000)
    assert socket_path("default") == "/tmp/tmux-1000/default"
    assert socket_path("/tmp/tmux-1000/ubar") == "/tmp/tmux-1000/ubar"
    assert socket_path("") == ""


def test_a_folder_trusted_on_the_main_login_is_trusted_on_another(tmp_path):
    main = {"projects": {"/w": {"hasTrustDialogAccepted": True}, "/x": {}}}
    (tmp_path / ".claude.json").write_text(json.dumps(main))
    team = tmp_path / ".claude-team"
    team.mkdir()
    (team / ".claude.json").write_text(json.dumps({"numStartups": 3}))
    assert launcher.carry_trust("/w/sub", team, tmp_path)  # a parent folder counts
    data = json.loads((team / ".claude.json").read_text())
    assert data["projects"]["/w/sub"]["hasTrustDialogAccepted"] and data["numStartups"] == 3
    assert not launcher.carry_trust("/w/sub", team, tmp_path)  # already there
    assert not launcher.carry_trust("/x", team, tmp_path)  # never trusted: still asks
    assert not launcher.carry_trust("/w", tmp_path / ".claude", tmp_path)  # main login


def test_a_pane_waiting_at_a_question_can_be_restarted_as_its_agent(monkeypatch):
    from agentdash.models import Harness, Session
    from agentdash.node.collectors import tmux
    from agentdash.node.runner import _pane_as_agent

    argv = ["/bin/claude", "--resume", "abc", "--name", "hub-build"]
    monkeypatch.setattr(tmux.procs, "cmdline", lambda pid: argv)
    monkeypatch.setattr(tmux.procs, "env_of", lambda pid, name: "/h/.claude-personal")
    facts = tmux.launch_facts("claude", 7)
    assert facts == {
        "resume": "abc",
        "config_dir": "/h/.claude-personal",
        "launch_name": "hub-build",
    }
    pane = Session(
        key="m:tmux:default:x:0.0",
        machine="m",
        harness=Harness.tmux,
        session_id="default:x:0.0",
        extra={"agent": "claude", **facts},
    )
    s = _pane_as_agent(pane)
    assert (s.harness, s.session_id, s.name) == ("claude", "abc", "hub-build")

    argv[:] = ["/bin/claude", "--name", "handover", "Continue that work."]
    fresh = _pane_as_agent(
        pane.model_copy(update={"extra": {"agent": "claude", **tmux.launch_facts("claude", 7)}})
    )
    assert fresh.session_id == "" and fresh.extra["launch_prompt"] == "Continue that work."
