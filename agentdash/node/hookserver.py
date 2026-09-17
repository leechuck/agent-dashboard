"""Loopback HTTP server that the harness hooks post to."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import uvicorn
from fastapi import FastAPI, Request

if TYPE_CHECKING:
    from .runner import Node

log = logging.getLogger(__name__)


def build_hook_app(node: Node) -> FastAPI:
    app = FastAPI(title="agentdash node hooks")

    @app.post("/hook/claude/event")
    async def claude_event(request: Request) -> dict[str, Any]:
        payload = await request.json()
        await node.on_claude_event(payload)
        return {}

    @app.post("/hook/claude/permission")
    async def claude_permission(request: Request) -> dict[str, Any]:
        payload = await request.json()
        return await node.on_claude_permission(payload)

    @app.post("/hook/codex/permission")
    async def codex_permission(request: Request) -> dict[str, Any]:
        from ..models import Harness

        payload = await request.json()
        return await node.on_claude_permission(payload, Harness.codex)

    @app.post("/hook/codex/event")
    async def codex_event(request: Request) -> dict[str, Any]:
        await request.json()
        node._refresh.set()
        return {}

    @app.post("/pi/report")
    async def pi_report(request: Request) -> dict[str, Any]:
        node.pi_report(await request.json())
        return {"armed": node.armed_now}

    @app.get("/pi/inbox/{session_id}")
    async def pi_inbox(session_id: str, wait: float = 20) -> dict[str, Any]:
        text = await node.pi_next_prompt(session_id, min(wait, 25))
        return {"prompt": text}

    @app.post("/pi/toolcall")
    async def pi_toolcall(request: Request) -> dict[str, Any]:
        return await node.on_pi_toolcall(await request.json())

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"ok": True, "armed": node.armed_now, "pending": len(node.decisions.pending)}

    return app


async def serve_hooks(node: Node, host: str, port: int) -> None:
    config = uvicorn.Config(
        build_hook_app(node), host=host, port=port, log_level="warning", lifespan="off"
    )
    server = uvicorn.Server(config)
    sock = config.bind_socket()  # raises if another node already owns the port
    await server.serve(sockets=[sock])
    raise RuntimeError("hook server stopped")
