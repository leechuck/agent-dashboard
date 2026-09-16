"""Web auth: one shared token via cookie, header or ?token= (set at login)."""

from __future__ import annotations

from fastapi import HTTPException, Request

COOKIE = "agentdash_token"


def _token_from(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return request.cookies.get(COOKIE, "") or request.query_params.get("token", "")


async def require_web_token(request: Request) -> None:
    expected: str = request.app.state.settings.web_token
    if not expected:
        return
    if _token_from(request) != expected:
        raise HTTPException(401, "not authorized")
