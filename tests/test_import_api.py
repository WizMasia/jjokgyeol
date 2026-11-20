"""Exercise the raw manuscript upload through the ASGI app and its child worker."""
import asyncio
from threading import BoundedSemaphore

import httpx
import pytest

from app import jobs, server
from app.configuration import CONFIG_DIR, read_config


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def api():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(server.app), base_url="http://local") as client:
        yield client


async def test_import_returns_only_manuscript_and_warnings(api):
    original = '<h2>관측</h2><p>앞<img src="missing.png">뒤</p>'.encode()
    response = await api.post("/api/import", params={"name": "달 & 별.HTML"}, content=original)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert data.keys() == {"markdown", "warnings"}
    assert data["markdown"].strip() == "## 관측\n\n앞뒤"
    assert any("그림" in warning for warning in data["warnings"])


@pytest.mark.parametrize("name,body", [
    ("", b"doc"), ("bad.pdf", b"text"), ("bad.docx", b"broken"), ("bad.txt", b"\xff"),
    ("a\x00.txt", b"abcd"),
])
async def test_invalid_import_reports_source_without_partial_manuscript(api, name, body):
    response = await api.post("/api/import", params={"name": name}, content=body)
    assert response.status_code == 422
    assert response.json().keys() == {"code", "field", "message"}
    assert response.json()["field"] == "source" and response.json()["message"]


async def test_import_checks_declared_and_streamed_bytes(api):
    limit = read_config(CONFIG_DIR / "imports.json")["max_bytes"]
    path = "/api/import?name=test.txt"
    declared = await api.post(path, content=b"x", headers={"content-length": str(limit + 1)})

    async def extra_byte():
        yield b"a" * limit
        yield b"!"

    streamed = await api.post(path, content=extra_byte())
    for response in (declared, streamed):
        assert response.status_code == 413
        assert response.json()["field"] == "source"
        assert f"{limit:,}" in response.json()["message"]


async def test_import_bounds_slow_upload_and_releases_slot(api, monkeypatch):
    monkeypatch.setattr(server, "SLOTS", BoundedSemaphore(1))
    monkeypatch.setattr(jobs, "TIMEOUT", 0.5)
    started = asyncio.Event()

    async def stalled():
        yield b"one"
        started.set()
        await asyncio.Event().wait()

    path = "/api/import?name=next.txt"
    slow = asyncio.create_task(api.post(path, content=stalled()))
    try:
        await asyncio.wait_for(started.wait(), 1)
        assert (await api.post(path, content=b"two")).status_code == 429
        assert (await slow).status_code == 504
    finally:
        slow.cancel()
        await asyncio.gather(slow, return_exceptions=True)
    monkeypatch.setattr(jobs, "TIMEOUT", server.LIMITS.timeout_seconds)
    recovered = await api.post(path, content=b"done")
    assert recovered.status_code == 200 and recovered.json() == {"markdown": "done", "warnings": []}
