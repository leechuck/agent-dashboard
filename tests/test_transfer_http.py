from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from agentdash.hub.blobs import router
from agentdash.node import transfer_http


@pytest.mark.asyncio
async def test_chunks_retry_lost_response_and_report_progress(tmp_path, monkeypatch):
    app = FastAPI()
    app.state.settings = SimpleNamespace(state_dir=tmp_path / "hub", node_token="test")
    app.include_router(router)
    transport = httpx.ASGITransport(app=app)
    monkeypatch.setattr(transfer_http, "CHUNK", 4)
    monkeypatch.setattr(
        transfer_http,
        "client",
        lambda token: httpx.AsyncClient(
            transport=transport, headers={"Authorization": "Bearer test"}
        ),
    )
    original = httpx.AsyncClient.request
    lost = False

    async def request(self, method, url, **kwargs):
        nonlocal lost
        result = await original(self, method, url, **kwargs)
        if method == "PUT" and not lost:
            lost = True
            raise httpx.ReadError("response lost after committing chunk")
        return result

    monkeypatch.setattr(httpx.AsyncClient, "request", request)
    source = tmp_path / "source"
    source.write_bytes(b"abcdefghijk")
    progress = []

    async def report(done, total):
        progress.append((done, total))

    url = "http://test/nodes/blob/" + "a" * 32
    await transfer_http.upload(source, url, "test", report)
    assert progress == [(0, 11), (4, 11), (8, 11), (11, 11)]
    progress.clear()
    destination = tmp_path / "destination"
    await transfer_http.download(destination, url, "test", 11, report)
    assert destination.read_bytes() == source.read_bytes()
    assert progress == [(0, 11), (4, 11), (8, 11), (11, 11)]
    async with transfer_http.client("test") as client:
        bad = await client.put(url, content=b"abcd", headers={"Content-Range": "bytes 0-3/11"})
        assert bad.status_code == 409
        assert (tmp_path / "hub/transfer" / ("a" * 32 + ".tgz")).read_bytes() == source.read_bytes()
