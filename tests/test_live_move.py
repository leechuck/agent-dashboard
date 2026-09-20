"""Opt-in native CLI test through a deployed hub and two online nodes.

Creates disposable agent projects, spends model tokens, and stops only test sessions.
Set AGENTDASH_LIVE_MOVE_URL, AGENTDASH_WEB_TOKEN, AGENTDASH_LIVE_MOVE_SOURCE,
and AGENTDASH_LIVE_MOVE_TARGET. Normal test runs never contact the fleet.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from urllib.parse import quote

import httpx
import pytest

URL = os.environ.get("AGENTDASH_LIVE_MOVE_URL", "")
pytestmark = pytest.mark.skipif(
    not URL, reason="requires explicit live two-host test configuration"
)


def poll(check, description, timeout=300):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        result = check()
        if result:
            return result
        time.sleep(2)
    pytest.fail(
        f"timed out: {description}; inspect the test session terminal for login/trust/permissions"
    )


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_native_move_remembers_context_and_uses_transferred_files(harness):
    source = os.environ["AGENTDASH_LIVE_MOVE_SOURCE"]
    target = os.environ["AGENTDASH_LIVE_MOVE_TARGET"]
    assert source != target
    tag = uuid.uuid4().hex
    secret = uuid.uuid4().hex
    cwd = f"/tmp/agentdash-move-smoke-{harness}-{tag}"
    destination = cwd + "-destination"
    keys = []
    trust_panels = {}
    permissions = os.environ.get("AGENTDASH_LIVE_MOVE_PERMISSIONS", "default")
    with httpx.Client(
        base_url=URL.rstrip("/"),
        timeout=3600,
        headers={"Authorization": f"Bearer {os.environ['AGENTDASH_WEB_TOKEN']}"},
    ) as client:

        def get(path):
            # Only approve workspace trust for the disposable folders this test created.
            # Claude versions differ in whether Yes or No is selected initially.
            if path == "/api/sessions":
                for pane, folder in list(trust_panels.items()):
                    response = client.get("/api/panes/" + quote(pane, safe="") + "/screen")
                    if response.status_code != 200:
                        continue
                    view = response.json()
                    screen = view.get("screen", "")
                    if view.get("login", {}).get("stage") != "trust" or folder not in screen:
                        continue
                    no = re.search(r"(?m)^\s*[❯›>]\s*(?:\d+\.\s*)?No", screen)
                    yes = re.search(r"(?m)^\s*(?:[❯›>]\s*)?(?:\d+\.\s*)?Yes", screen)
                    press = []
                    if no and yes:
                        press = ["Down" if yes.start() > no.start() else "Up"]
                    post(
                        "/api/panes/" + quote(pane, safe="") + "/keys", {"keys": press + ["Enter"]}
                    )
                    trust_panels.pop(pane)
                    print(f"Accepted trust for test folder {folder}", flush=True)
            r = client.get(path)
            r.raise_for_status()
            return r.json()

        def post(path, data):
            r = client.post(path, json=data)
            r.raise_for_status()
            body = r.json()
            assert body.get("ok"), body
            return body

        def completed(key, marker):
            session = get("/api/sessions/" + quote(key, safe=""))
            messages = get("/api/sessions/" + quote(key, safe="") + "/messages")
            return session["status"] == "idle" and any(
                m.get("role") == "assistant" and marker in m.get("text", "") for m in messages
            )

        try:
            started = post(
                f"/api/machines/{source}/sessions",
                {
                    "harness": harness,
                    "cwd": cwd,
                    "create_dir": True,
                    "mode": "tmux",
                    "permissions": permissions,
                    "name": f"move-smoke-{tag[:8]}",
                    "prompt": (
                        f"This is a disposable dashboard migration test. Work only in {cwd}. "
                        f"Remember the conversation-only code {secret}; do not write it to any file. "
                        "Create tracked.txt with baseline, run git init, git add tracked.txt, "
                        "and a commit using the configured Git identity. "
                        "Then change tracked.txt to dirty, create untracked.txt containing untracked, "
                        "and .env containing MOVE_TEST=fixture. Create dependency/bin/check as an "
                        "executable shell script printing dependency-ok. Create "
                        ".agents/skills/move-fixture/SKILL.md with simple fixture instructions. "
                        f"Leave those changes uncommitted. Finish with MOVE_READY:{tag}. Then wait."
                    ),
                },
            )
            pane = started["tmux"]
            trust_panels[f"{source}:tmux:{pane['socket']}:{pane['target']}"] = cwd
            session = poll(
                lambda: next(
                    (
                        s
                        for s in get("/api/sessions")
                        if s["machine"] == source and s["harness"] == harness and s["cwd"] == cwd
                    ),
                    None,
                ),
                "source discovery",
            )
            key = session["key"]
            keys.append(key)
            poll(lambda: completed(key, f"MOVE_READY:{tag}"), "source fixture setup")
            moved = post(
                "/api/sessions/" + quote(key, safe="") + "/move",
                {
                    "machine": target,
                    "cwd": destination,
                    "environment": True,
                    "stop_old": True,
                    "permissions": permissions,
                    "note": (
                        "Verify with tools that the current directory is the new destination, "
                        "tracked.txt contains dirty, untracked.txt contains untracked, "
                        ".env has MOVE_TEST=fixture, git status shows uncommitted changes, "
                        "dependency/bin/check is executable and prints dependency-ok, and "
                        ".agents/skills/move-fixture/SKILL.md exists. If anything fails, report "
                        "MOVE_FAILED and the evidence. Otherwise finish with "
                        f"MOVE_OK:{tag}: followed by the conversation-only code from before the move. "
                        "Do not change any other folders."
                    ),
                },
            )
            assert moved["stopped"]
            key = moved["session_key"]
            keys.append(key)
            trust_panels[moved["terminal_key"]] = destination
            poll(
                lambda: any(s["key"] == key for s in get("/api/sessions")), "destination discovery"
            )
            poll(
                lambda: completed(key, f"MOVE_OK:{tag}:{secret}"),
                "native resume, environment verification and conversation recall",
            )
            assert get("/api/sessions/" + quote(key, safe=""))["cwd"] == destination
        finally:
            for key in keys:
                # Do not remove projects/transcripts: they are evidence for failures.
                client.post(
                    "/api/sessions/" + quote(key, safe="") + "/action", json={"action": "terminate"}
                )
