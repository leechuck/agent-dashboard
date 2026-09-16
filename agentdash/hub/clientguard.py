"""Only loopback and tailnet clients may talk to the hub when it binds a real interface."""

from __future__ import annotations

import ipaddress

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse

DEFAULT_CIDRS = ("127.0.0.0/8", "::1/128", "100.64.0.0/10", "fd7a:115c:a1e0::/48")


class ClientGuard(BaseHTTPMiddleware):
    def __init__(self, app, cidrs: list[str] | tuple[str, ...] = DEFAULT_CIDRS) -> None:
        super().__init__(app)
        self.nets = [ipaddress.ip_network(c) for c in cidrs]

    def allowed(self, host: str | None) -> bool:
        if not host:
            return False
        try:
            ip = ipaddress.ip_address(host.split("%")[0])
        except ValueError:
            return False
        return any(ip in n for n in self.nets)

    async def dispatch(self, request: Request, call_next):
        client = request.client.host if request.client else None
        if not self.allowed(client):
            return PlainTextResponse("forbidden", status_code=403)
        return await call_next(request)


def client_allowed(host: str | None, cidrs=DEFAULT_CIDRS) -> bool:
    return ClientGuard(None, cidrs).allowed(host)  # type: ignore[arg-type]
