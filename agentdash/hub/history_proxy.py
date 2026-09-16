"""Reverse proxy to agentsview's API so the web app (and phone) can search history."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, Request, Response

from .auth import require_web_token

router = APIRouter(prefix="/history", dependencies=[Depends(require_web_token)])

HOP = {"host", "content-length", "connection", "transfer-encoding", "cookie", "authorization"}


@router.api_route("/{path:path}", methods=["GET", "POST"])
async def proxy(path: str, request: Request) -> Response:
    settings = request.app.state.settings
    client: httpx.AsyncClient = request.app.state.history_client
    url = f"{settings.history_url.rstrip('/')}/{path}"
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
    headers["host"] = httpx.URL(settings.history_url).netloc.decode()
    if settings.history_token:
        headers["authorization"] = f"Bearer {settings.history_token}"
    body = await request.body()
    try:
        r = await client.request(
            request.method, url, params=request.query_params, headers=headers, content=body
        )
    except httpx.HTTPError as e:
        return Response(f"history backend unavailable: {e}", status_code=502)
    out_headers = {
        k: v
        for k, v in r.headers.items()
        if k.lower() in ("content-type", "cache-control", "etag", "last-modified")
    }
    return Response(r.content, status_code=r.status_code, headers=out_headers)
