import tarfile
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from agentdash import transfer_limits
from agentdash.hub.blobs import router
from agentdash.node import workspace_transfer as wt


def test_environment_larger_than_two_gib_round_trips(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "large.dat"
    size = 2 * 1024**3 + 1024**2
    with source.open("wb") as f:
        f.seek(size - 4)
        f.write(b"tail")
    bundle = tmp_path / "bundle.tgz"
    destination = tmp_path / "destination"
    try:
        with tarfile.open(bundle, "w:gz", compresslevel=1) as archive:
            wt.add(archive, project, tmp_path / "home", "codex", tmp_path / "config")
        assert wt.restore(bundle, destination, tmp_path / "home", tmp_path / "runtime")
        copied = destination / "large.dat"
        assert copied.stat().st_size == size
        with copied.open("rb") as f:
            assert f.read(4) == b"\0" * 4
            f.seek(-4, 2)
            assert f.read() == b"tail"
    finally:
        source.unlink(missing_ok=True)
        (destination / "large.dat").unlink(missing_ok=True)


def test_disk_check_combines_writes_on_same_filesystem(tmp_path, monkeypatch):
    monkeypatch.setattr(
        transfer_limits.shutil, "disk_usage", lambda path: SimpleNamespace(free=800 * 1024**2)
    )
    with pytest.raises(OSError, match="not enough free disk space"):
        transfer_limits.require_spaces(
            [(tmp_path / "a", 200 * 1024**2), (tmp_path / "b", 200 * 1024**2)]
        )


@pytest.mark.asyncio
async def test_hub_chunks_cross_two_gib_boundary(tmp_path):
    app = FastAPI()
    app.state.settings = SimpleNamespace(state_dir=tmp_path, node_token="test")
    app.include_router(router)
    directory = tmp_path / "transfer"
    directory.mkdir()
    path = directory / ("a" * 32 + ".tgz")
    offset = 2 * 1024**3
    with path.open("wb") as f:
        f.truncate(offset)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer test"},
        ) as client:
            url = "/nodes/blob/" + "a" * 32
            r = await client.put(
                url,
                content=b"tail",
                headers={"Content-Range": f"bytes {offset}-{offset + 3}/{offset + 4}"},
            )
            assert r.status_code == 200
            r = await client.get(url, headers={"Range": f"bytes={offset}-{offset + 3}"})
            assert r.status_code == 206 and r.content == b"tail"
    finally:
        path.unlink(missing_ok=True)
