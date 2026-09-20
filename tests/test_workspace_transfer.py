import io
import json
import tarfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agentdash.models import Session
from agentdash.node import past
from agentdash.node import workspace_transfer as wt


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    from pathlib import Path

    home = tmp_path / "test-home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_environment_roundtrip_preserves_dirty_files_skills_dependencies_and_login(
    tmp_path, harness
):
    source, dest = tmp_path / "source", tmp_path / "destination"
    work = source / "project"
    work.mkdir(parents=True)
    (work / ".git").mkdir()
    (work / ".git/HEAD").write_text("ref: refs/heads/work\n")
    (work / "dirty.py").write_text("uncommitted = True\n")
    (work / ".env").write_text("PROJECT_VALUE=fixture\n")
    (work / ".venv/bin").mkdir(parents=True)
    tool = work / ".venv/bin/tool"
    tool.write_text("#!/bin/sh\nexit 0\n")
    tool.chmod(0o755)
    (work / "relative").symlink_to("dirty.py")
    config = source / ("." + harness)
    skill = source / "skill-source"
    skill.mkdir()
    (skill / "SKILL.md").write_text("local skill instructions")
    (config / "skills").mkdir(parents=True)
    (config / "skills/test").symlink_to(skill)
    config_name = "settings.json" if harness == "claude" else "config.toml"
    (config / config_name).write_text("{}" if harness == "claude" else 'model = "fixture"')
    (config / "auth.json").write_text("source login must not travel")
    transcript = config / "sessions/session-id.jsonl"
    transcript.parent.mkdir(exist_ok=True)
    transcript.write_text(
        json.dumps({"type": "session_meta", "payload": {"id": "session-id"}}) + "\n"
    )
    if harness == "claude":
        transcript = config / "session-id.jsonl"
        transcript.write_text('{"type":"user","message":{"content":"remember me"}}\n')
    s = Session(
        key=f"src:{harness}:session-id",
        machine="src",
        harness=harness,
        session_id="session-id",
        cwd=str(work),
        transcript_path=str(transcript),
        extra={"config_dir": str(config), "codex_home": str(config)},
    )
    bundle, meta = past.pack(s, tmp_path / "bundles", environment=True)
    assert meta["environment"]
    target = dest / "project"
    runtime = dest / ("." + harness + "-move-test")
    runtime.mkdir(parents=True)
    (runtime / "auth.json").write_text("destination login")
    assert wt.restore(bundle, target, dest, runtime, check_only=True)
    assert not target.exists()
    assert wt.restore(bundle, target, dest, runtime)
    for rel in ["dirty.py", ".env", ".git/HEAD", ".venv/bin/tool"]:
        assert (target / rel).read_bytes() == (work / rel).read_bytes()
    assert (target / ".venv/bin/tool").stat().st_mode & 0o111
    assert (target / "relative").is_symlink()
    assert (target / "relative").read_text() == (work / "dirty.py").read_text()
    assert (runtime / "skills/test/SKILL.md").read_text() == "local skill instructions"
    assert (runtime / "auth.json").read_text() == "destination login"
    restored = past.unpack(bundle, harness, "session-id", str(target), runtime)
    assert restored.read_bytes() == transcript.read_bytes()
    # Retrying the same copy is harmless.
    assert wt.restore(bundle, target, dest, runtime)


def make_bundle(tmp_path, extras=()):
    work = tmp_path / "source/project"
    work.mkdir(parents=True)
    (work / "tracked").write_text("source")
    (work / "new").write_text("new")
    bundle = tmp_path / "bundle.tgz"
    with tarfile.open(bundle, "w:gz") as tar:
        wt.add(tar, work, tmp_path / "source", "codex", tmp_path / "source/.codex")
        for name, kind, value in extras:
            info = tarfile.TarInfo(name)
            if kind in ("symlink", "hardlink"):
                info.type = tarfile.SYMTYPE if kind == "symlink" else tarfile.LNKTYPE
                info.linkname = value
                tar.addfile(info)
            else:
                data = value.encode()
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return bundle


def test_conflict_never_overwrites_or_partially_copies(tmp_path):
    bundle = make_bundle(tmp_path)
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "tracked").write_text("valuable destination work")
    with pytest.raises(past.TransferError, match="destination conflict"):
        wt.restore(bundle, dest, tmp_path / "home", tmp_path / "runtime")
    assert (dest / "tracked").read_text() == "valuable destination work"
    assert not (dest / "new").exists()


@pytest.mark.parametrize(
    "extras",
    [
        [("environment/workspace/../../escape", "file", "bad")],
        [
            ("environment/workspace/redirect", "symlink", "/tmp"),
            ("environment/workspace/redirect/escape", "file", "bad"),
        ],
        [("environment/workspace/tracked", "file", "duplicate")],
    ],
)
def test_malicious_archives_do_not_write_destination(tmp_path, extras):
    bundle = make_bundle(tmp_path, extras)
    dest = tmp_path / "dest"
    with pytest.raises(past.TransferError):
        wt.restore(bundle, dest, tmp_path / "home", tmp_path / "runtime")
    assert not dest.exists()


def test_destination_symlink_is_not_followed(tmp_path):
    bundle = make_bundle(tmp_path)
    protected = tmp_path / "protected"
    protected.mkdir()
    dest = tmp_path / "dest"
    dest.symlink_to(protected)
    with pytest.raises(past.TransferError, match="symlink"):
        wt.restore(bundle, dest, tmp_path / "home", tmp_path / "runtime")
    assert not list(protected.iterdir())


def test_relocation_message_tells_agent_to_find_missing_folders():
    text = wt.relocation_message("laptop", "ws", "/old", "/new", True, "Continue tests")
    for part in ["laptop", "ws", "/old", "/new", "additional folders", "Gnus", "Continue tests"]:
        assert part in text


@pytest.mark.parametrize("harness", ["claude", "codex"])
@pytest.mark.parametrize("ready", [True, False])
async def test_destination_checks_precede_stopping_source(tmp_path, monkeypatch, harness, ready):
    from agentdash.hub import api

    calls = []

    async def node_call(request, machine, action, payload, timeout):
        calls.append((machine, action, dict(payload)))
        if action == "session.export":
            return dict(
                ok=True,
                blob="a" * 32,
                harness=harness,
                session_id="id",
                cwd="/project",
                stopped=payload["stop"],
            )
        if payload.get("prepare_only"):
            return dict(ok=ready, prepared=ready, error="" if ready else "conflict")
        return {"ok": True}

    hub = SimpleNamespace(nodes={"ws": object()}, db=object())
    req = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(hub=hub, settings=SimpleNamespace(state_dir=tmp_path))
        )
    )
    monkeypatch.setattr(api, "_node_call", node_call)
    monkeypatch.setattr(api.ck, "endpoints_setting", AsyncMock(return_value=[]))
    result = await api.move_session(f"laptop:{harness}:id", api.MoveBody(machine="ws"), req)
    assert calls[0][2]["stop"] is False
    assert calls[1][2]["prepare_only"] is True
    if ready:
        assert result["ok"]
        assert calls[2][2]["stop"] is True
        assert not calls[3][2].get("prepare_only")
        assert calls[3][2]["source_machine"] == "laptop"
    else:
        assert not result["ok"] and not result["stopped"]
        assert len(calls) == 2


@pytest.mark.parametrize("harness", ["claude", "codex"])
async def test_node_import_restores_environment_and_delivers_relocation(
    tmp_path, monkeypatch, harness
):
    import asyncio
    from pathlib import Path

    from agentdash.config import Settings
    from agentdash.node import runner

    source = tmp_path / "source"
    source.mkdir()
    work = source / "work"
    work.mkdir()
    (work / "untracked").write_text("my work")
    cfg = source / ("." + harness)
    cfg.mkdir()
    transcript = cfg / "session-id.jsonl"
    transcript.write_text('{"type":"user","message":{"content":"remember context"}}\n')
    sess = Session(
        key=f"src:{harness}:session-id",
        machine="src",
        harness=harness,
        session_id="session-id",
        cwd=str(work),
        transcript_path=str(transcript),
        extra={"config_dir": str(cfg), "codex_home": str(cfg)},
    )
    bundle, _ = past.pack(sess, tmp_path / "out", environment=True)
    target_home = tmp_path / "dest"
    target_home.mkdir()
    login = target_home / ("." + harness)
    login.mkdir()
    credential = ".credentials.json" if harness == "claude" else "auth.json"
    (login / credential).write_text("destination credentials")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: target_home))
    monkeypatch.setattr(runner, "discover_claude_dirs", lambda: [target_home / ".claude"])
    monkeypatch.setattr(runner, "_validate_claude_login", AsyncMock())
    monkeypatch.setattr("shutil.which", lambda cmd: "/bin/" + cmd)
    target = target_home / "project"
    state = target_home / "state"
    state.mkdir()
    n = runner.Node.__new__(runner.Node)
    monkeypatch.setattr("agentdash.config.STATE_DIR", state)
    n.s = Settings(claude_config_dirs=[target_home / ".claude"])
    n.machine = "ws"
    n.claude = SimpleNamespace(config_dirs=[target_home / ".claude"])
    n.codex = SimpleNamespace(home=login)
    n.pi = SimpleNamespace(dir=target_home / ".pi")
    n._endpoints = lambda p: []
    n._hub_http = lambda: "http://fixture"
    n._reply = AsyncMock()
    n._refresh = asyncio.Event()

    class Response:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def aiter_bytes(self):
            yield bundle.read_bytes()

    class Client(Response):
        def __init__(self, **kwargs):
            pass

        def stream(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(runner.httpx, "AsyncClient", Client)

    # Real archive/filesystem operations; execute the offload inline so this test
    # does not depend on worker-thread wakeup sockets in restricted environments.
    async def inline(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(runner.asyncio, "to_thread", inline)
    start = AsyncMock(return_value={"ok": True, "tmux": {"socket": "default", "target": "test:0"}})
    monkeypatch.setattr(runner, "launch", start)
    args = dict(
        harness=harness,
        session_id="session-id",
        cwd=str(target),
        blob="a" * 32,
        environment=True,
        source_machine="laptop",
        source_cwd=str(work),
        note="Keep working",
    )
    await n.session_import({**args, "prepare_only": True, "create_dir": False})
    denied = n._reply.call_args.args[2]
    assert not denied["ok"] and "no such destination directory" in denied["error"]
    assert not target.exists()
    start.assert_not_awaited()
    target.mkdir()
    await n.session_import({**args, "prepare_only": True, "create_dir": False})
    assert n._reply.call_args.args[2] == {"ok": True, "prepared": True}
    target.rmdir()
    await n.session_import({**args, "prepare_only": True})
    assert n._reply.call_args.args[2] == {"ok": True, "prepared": True}
    assert not target.exists()
    start.assert_not_awaited()
    await n.session_import(args)
    result = n._reply.call_args.args[2]
    assert result["ok"], result
    assert (target / "untracked").read_text() == "my work"
    spec = start.call_args.args[0]
    assert spec.resume == "session-id" and spec.cwd == str(target)
    assert "moved from laptop to ws" in spec.prompt and "additional folders" in spec.prompt
    assert "Keep working" in spec.prompt
    runtime = start.call_args.kwargs["runtime_home"]
    assert runtime != login and (runtime / credential).resolve() == login / credential
    if harness == "claude":
        assert runtime in n.claude.config_dirs
    assert Path(result["transcript_path"]).read_bytes() == transcript.read_bytes()


def test_codex_roster_finds_isolated_runtime_without_replacing_host_config(tmp_path):
    import sqlite3

    from agentdash.node.collectors.codex import CodexCollector

    base = tmp_path / ".codex"
    runtime = tmp_path / ".codex-move-test"
    runtime.mkdir()
    with sqlite3.connect(runtime / "state_5.sqlite") as db:
        columns = [name.strip() for name in CodexCollector._COLS.split(",")]
        db.execute("CREATE TABLE threads (" + ", ".join(f"{c} TEXT" for c in columns) + ")")
        db.execute(
            "INSERT INTO threads (id, cwd, updated_at_ms) VALUES ('thread', '/moved', '123')"
        )
    collector = CodexCollector("ws", base)
    rows = collector._query("id = ?", ("thread",))
    assert len(rows) == 1 and rows[0]["codex_home"] == str(runtime)
    assert past.codex_session("ws", rows[0], base).extra["codex_home"] == str(runtime)


def test_codex_resume_explicitly_uses_destination_directory(monkeypatch):
    from agentdash.config import Settings
    from agentdash.node.launcher import LaunchSpec, build

    monkeypatch.setattr("shutil.which", lambda name: "/bin/" + name)
    args, _ = build(LaunchSpec(harness="codex", resume="thread", cwd="/moved"), Settings(), [])
    assert args[args.index("--cd") + 1] == "/moved"


def test_transcript_conflict_is_detected_in_preflight(tmp_path):

    bundle = make_bundle(tmp_path)
    # An environment archive without the requested conversation cannot pass preflight.
    with pytest.raises(past.TransferError, match="transcript"):
        past.unpack(bundle, "claude", "missing", "/project", tmp_path / "runtime", check_only=True)
    assert not (tmp_path / "runtime").exists()


@pytest.mark.parametrize("harness", ["claude", "codex"])
@pytest.mark.parametrize("stop", [False, True])
async def test_export_streams_bundle_and_reports_actual_stop(tmp_path, monkeypatch, harness, stop):
    import asyncio

    from agentdash.config import Settings
    from agentdash.node import runner

    cwd = tmp_path / "project"
    cwd.mkdir()
    (cwd / "dirty").write_text("uncommitted")
    transcript = tmp_path / "session-id.jsonl"
    transcript.write_text('{"type":"user","message":{"content":"context"}}\n')
    sess = Session(
        key=f"laptop:{harness}:session-id",
        machine="laptop",
        harness=harness,
        session_id="session-id",
        cwd=str(cwd),
        transcript_path=str(transcript),
        status="idle",
        pid=1234567,
    )
    monkeypatch.setattr("agentdash.config.STATE_DIR", tmp_path / "state")
    node = runner.Node.__new__(runner.Node)
    node.s = Settings()
    node.locate = AsyncMock(return_value=sess)
    node._stop = AsyncMock()
    node._refresh = asyncio.Event()
    node._reply = AsyncMock()
    node._hub_http = lambda: "http://fixture"
    received = bytearray()

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def request(self, method, url, content, headers):
            assert method == "PUT" and headers["Content-Range"].startswith("bytes ")
            received.extend(content)
            return SimpleNamespace(status_code=200)

    async def inline(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(runner.httpx, "AsyncClient", Client)
    monkeypatch.setattr(runner.asyncio, "to_thread", inline)
    await node.session_export({"session_key": sess.key, "stop": stop, "environment": True})
    result = node._reply.call_args.args[2]
    assert result["ok"] and result["stopped"] is stop
    assert node._stop.await_count == int(stop)
    with tarfile.open(fileobj=io.BytesIO(received), mode="r:gz") as tar:
        assert tar.extractfile("environment/workspace/dirty").read() == b"uncommitted"
    assert not list((node.s.state_dir / "transfer").glob("*.tgz"))


def test_hardlink_cannot_read_through_an_external_symlink(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private").write_text("must not be copied")
    bundle = make_bundle(
        tmp_path,
        [
            ("environment/workspace/link", "symlink", str(outside)),
            ("environment/workspace/stolen", "hardlink", "environment/workspace/link/private"),
        ],
    )
    with pytest.raises(past.TransferError, match="hard link"):
        wt.restore(bundle, tmp_path / "dest", tmp_path / "home", tmp_path / "runtime")
    assert not (tmp_path / "dest").exists()


def test_claude_isolated_login_reuses_onboarding_without_copying_project_state(tmp_path):
    home = tmp_path / "home"
    runtime = home / ".claude-move-test"
    runtime.mkdir(parents=True)
    (home / ".claude.json").write_text(json.dumps({
        "hasCompletedOnboarding": True, "lastOnboardingVersion": "2.1.211",
        "oauthAccount": {"fixture": "destination account"},
        "projects": {"/unrelated": {"hasTrustDialogAccepted": True}},
    }))
    wt.bootstrap_claude_login(home / ".claude", runtime, home)
    result = json.loads((runtime / ".claude.json").read_text())
    assert result["hasCompletedOnboarding"]
    assert result["oauthAccount"] == {"fixture": "destination account"}
    assert "projects" not in result
    assert (runtime / ".claude.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_logged_out_destination_is_rejected_before_transfer(monkeypatch, tmp_path):
    from agentdash.node import runner

    process = SimpleNamespace(communicate=AsyncMock(return_value=(b'{"loggedIn":false}', b'')))
    monkeypatch.setattr(runner.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process))
    with pytest.raises(past.TransferError, match='logged out on the destination'):
        await runner._validate_claude_login(tmp_path, {})
