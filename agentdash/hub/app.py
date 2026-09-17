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
from .clientguard import ClientGuard
from .history_proxy import router as history_router
from .push import Pusher
from .state import HubState
from .terminals import router as terminal_router
from .ws_nodes import router as ws_router

STATIC = Path(__file__).resolve().parent.parent / "static"
# index.html names the hashed bundles: browsers must ask again after every deploy,
# or a phone keeps running last week's interface
_REVALIDATE = {"Cache-Control": "no-cache"}


def create_app() -> FastAPI:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = get_settings()
    db = Database(settings.hub_db)
    bus = EventBus()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        import asyncio

        import httpx

        await db.open()
        app.state.history_client = httpx.AsyncClient(timeout=30)

        async def retention() -> None:
            while True:
                try:
                    await db.prune_usage(settings.keep_usage_days * 86400 * 1000)
                    await db.prune_events(settings.keep_events_days * 86400 * 1000)
                    await db.prune_decisions(settings.keep_events_days * 86400 * 1000)
                except Exception:  # noqa: BLE001
                    logging.getLogger(__name__).exception("retention failed")
                await asyncio.sleep(6 * 3600)

        task = asyncio.create_task(retention())
        yield
        task.cancel()
        await app.state.history_client.aclose()
        await db.close()

    app = FastAPI(title="agentdash hub", lifespan=lifespan)
    app.state.settings = settings
    app.state.hub = HubState(db, bus, Pusher(settings.state_dir, settings.push_subject))
    app.add_middleware(ClientGuard, cidrs=settings.hub_allowed_cidrs)
    app.include_router(api_router)
    app.include_router(ws_router)
    app.include_router(history_router)
    app.include_router(terminal_router)

    @app.post("/login")
    async def login(request: Request) -> Response:
        body = await request.json()
        if settings.web_token and body.get("token") != settings.web_token:
            return JSONResponse({"ok": False}, status_code=401)
        resp = JSONResponse({"ok": True})
        secure = (
            request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        )
        resp.set_cookie(
            COOKIE,
            body.get("token", ""),
            httponly=True,
            secure=secure,
            samesite="none" if secure else "lax",
            max_age=60 * 60 * 24 * 365,
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
            candidate = (STATIC / path).resolve()
            if path and candidate.is_relative_to(STATIC) and candidate.is_file():
                return FileResponse(candidate, headers=_REVALIDATE)
            return FileResponse(index, headers=_REVALIDATE)

    return app
