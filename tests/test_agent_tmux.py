import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "agentdash/install/files/agent-tmux"
FLAG = "--dangerously-bypass-approvals-and-sandbox"


@pytest.fixture
def invoke(tmp_path):
    # Capture argv at the tmux boundary and at the already-inside-tmux boundary.
    stub = "#!/usr/bin/python3\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n"
    for name in ("tmux", "codex", "claude"):
        path = tmp_path / name
        path.write_text(stub)
        path.chmod(0o755)

    def run(args, inside=False):
        env = {**os.environ, "PATH": f"{tmp_path}:/usr/bin:/bin", "TMUX": "test" if inside else ""}
        result = subprocess.run(
            ["bash", str(SCRIPT), *args],
            env=env,
            cwd=tmp_path,
            text=True,
            capture_output=True,
            check=True,
        )
        argv = json.loads(result.stdout)
        if not inside:
            assert argv[0] == "new-session"
            argv = argv[argv.index("--") + 1 :]
        return argv, result.stderr

    return run


@pytest.mark.parametrize("inside", [False, True])
@pytest.mark.parametrize(
    "args",
    [
        ["resume", FLAG],
        [FLAG, "resume"],
        ["-dangerously-bypass-approvals-and-sandbox", "resume"],
        ["resume", "--last", "-dangerously-bypass-approvals-and-sandbox"],
    ],
)
def test_codex_resume_with_explicit_bypass(invoke, args, inside):
    argv, stderr = invoke(["codex", *args], inside)
    expected = [FLAG if a == FLAG[1:] else a for a in args]
    assert argv == ([] if inside else ["codex"]) + expected
    assert ("two hyphens" in stderr) == (FLAG[1:] in args)


@pytest.mark.parametrize("inside", [False, True])
def test_no_implicit_bypass_or_argument_splitting(invoke, inside):
    args = ["resume", "session name", "Continue with $(literal) and `backticks`"]
    argv, stderr = invoke(["codex", *args], inside)
    assert argv == ([] if inside else ["codex"]) + args
    assert FLAG not in argv and not stderr


def test_literal_after_separator_is_untouched(invoke):
    args = ["codex", "resume", "--", FLAG[1:]]
    argv, stderr = invoke(args)
    assert argv == args and not stderr


def test_other_agents_are_untouched(invoke):
    args = ["claude", "--resume", FLAG[1:]]
    argv, stderr = invoke(args)
    assert argv == args and not stderr
