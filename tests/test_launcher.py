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
