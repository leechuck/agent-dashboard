"""Relay for moving a session between machines.

The source node uploads a bundle (the transcript, packed), the target node downloads
it, the hub deletes it once the move is over. Bundles are the owner's transcripts:
they live for one transfer only, under the hub's state directory, never in the database.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

router = APIRouter(prefix="/nodes/blob")

_ID = re.compile(r"^[a-f0-9]{16,64}$")
MAX_BYTES = 2 * 1024**3


def _dir(request: Request) -> Path:
    d = request.app.state.settings.state_dir / "transfer"
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    return d


def _check(request: Request, blob_id: str) -> Path:
    token: str = request.app.state.settings.node_token
    auth = request.headers.get("authorization", "")
    if token and auth != f"Bearer {token}" and request.query_params.get("token") != token:
        raise HTTPException(401, "node token required")
    if not _ID.match(blob_id):
        raise HTTPException(400, "bad bundle id")
    return _dir(request) / f"{blob_id}.tgz"


def remove(state_dir: Path, blob_id: str) -> None:
    if _ID.match(blob_id):
        (state_dir / "transfer" / f"{blob_id}.tgz").unlink(missing_ok=True)


@router.put("/{blob_id}")
async def upload(blob_id: str, request: Request):
    path = _check(request, blob_id)
    size = 0
    with path.open("wb") as f:
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_BYTES:
                f.close()
                path.unlink(missing_ok=True)
                raise HTTPException(413, "bundle too large")
            f.write(chunk)
    return {"ok": True, "size": size}


@router.get("/{blob_id}")
async def download(blob_id: str, request: Request):
    path = _check(request, blob_id)
    if not path.is_file():
        raise HTTPException(404, "no such bundle")
    return FileResponse(path, media_type="application/gzip")


@router.delete("/{blob_id}")
async def delete(blob_id: str, request: Request):
    _check(request, blob_id).unlink(missing_ok=True)
    return {"ok": True}
