"""FastAPI application factory for the hub."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..config import get_settings
from ..db import Database
from .api import router as api_router
from .auth import COOKIE
from .bus import EventBus
from .state import HubState
from .ws_nodes import router as ws_router

STATIC = Path(__file__).resolve().parent.parent / "static"


def create_app() -> FastAPI:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = get_settings()
    db = Database(settings.hub_db)
    bus = EventBus()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await db.open()
        yield
        await db.close()

    app = FastAPI(title="agentdash hub", lifespan=lifespan)
    app.state.settings = settings
    app.state.hub = HubState(db, bus)
    app.include_router(api_router)
    app.include_router(ws_router)

    @app.post("/login")
    async def login(request: Request) -> Response:
        body = await request.json()
        if settings.web_token and body.get("token") != settings.web_token:
            return JSONResponse({"ok": False}, status_code=401)
        resp = JSONResponse({"ok": True})
        resp.set_cookie(
            COOKIE, body.get("token", ""), httponly=True, samesite="lax", max_age=60 * 60 * 24 * 365
        )
        return resp

    @app.get("/healthz")
    async def healthz():
        return {"ok": True, "nodes": sorted(app.state.hub.nodes)}

    index = STATIC / "index.html"
    if index.exists():
        app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

        @app.get("/{path:path}")
        async def spa(path: str):
            candidate = STATIC / path
            if path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(index)

    return app
