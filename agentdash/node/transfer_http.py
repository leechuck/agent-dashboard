"""Bounded, retryable requests for large environment bundles."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from ..transfer_limits import MAX_BYTES, require_space
from .past import TransferError

CHUNK = 4 * 1024**2


async def request(client, method, url, **kwargs):
    for attempt in range(4):
        try:
            response = await client.request(method, url, **kwargs)
            if response.status_code < 500 or response.status_code == 507 or attempt == 3:
                return response
        except httpx.TransportError:
            if attempt == 3:
                raise
        await asyncio.sleep(2**attempt)
    raise TransferError("transfer request failed")


def client(token: str) -> httpx.AsyncClient:
    # Reconnecting each bounded request avoids getting stuck on a stale VPN path.
    return httpx.AsyncClient(
        timeout=httpx.Timeout(90, connect=20),
        limits=httpx.Limits(max_keepalive_connections=0),
        headers={"Authorization": f"Bearer {token}"},
    )


async def upload(path: Path, url: str, token: str, progress=None) -> None:
    total = path.stat().st_size
    offset = 0
    if progress:
        await progress(0, total)
    async with client(token) as connection:
        with path.open("rb") as source:
            while chunk := await asyncio.to_thread(source.read, CHUNK):
                response = await request(
                    connection,
                    "PUT",
                    url,
                    content=chunk,
                    headers={"Content-Range": f"bytes {offset}-{offset + len(chunk) - 1}/{total}"},
                )
                if response.status_code != 200:
                    detail = response.text[:300]
                    raise TransferError(
                        f"bundle upload failed: HTTP {response.status_code}: {detail}"
                    )
                offset += len(chunk)
                if progress:
                    await progress(offset, total)


async def download(path: Path, url: str, token: str, total: int, progress=None) -> None:
    if not 0 < total <= MAX_BYTES:
        raise TransferError("invalid transfer size")
    require_space(path, total)
    if progress:
        await progress(0, total)
    async with client(token) as connection:
        with path.open("wb") as destination:
            for start in range(0, total, CHUNK):
                end = min(total, start + CHUNK) - 1
                response = await request(
                    connection,
                    "GET",
                    url,
                    headers={"Range": f"bytes={start}-{end}"},
                )
                if (
                    response.status_code != 206
                    or len(response.content) != end - start + 1
                    or response.headers.get("content-range") != f"bytes {start}-{end}/{total}"
                ):
                    raise TransferError("invalid partial bundle response")
                destination.write(response.content)
                if progress:
                    await progress(end + 1, total)
