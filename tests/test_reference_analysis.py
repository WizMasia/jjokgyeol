"""Reference requests use a separate size budget and a private, bounded worker."""
import asyncio

import httpx
import pytest


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def api():
    from app.server import app
    return httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://127.0.0.1")


@pytest.mark.anyio
async def test_operator_settings_are_public_only_by_label(api):
    async with api:
        response = await api.get("/api/reference/settings")
        assert response.status_code == 200
        assert set(response.json()) == {"model", "max_bytes", "heading_per_10000", "heading_leading_per_10000"}
        assert response.json()["heading_per_10000"][0] == 16000
        assert response.json()["heading_leading_per_10000"] == 12500
        assert response.json()["max_bytes"] > 0


@pytest.mark.anyio
async def test_reference_body_limit_and_file_type(api):
    async with api:
        limit = (await api.get("/api/reference/settings")).json()["max_bytes"]
        response = await api.post("/api/reference/analyze?name=a.pdf",
                                  headers={"content-length": str(limit + 1)}, content=b"")
        assert response.status_code == 413
        for name, content in [("a.md", b"hello"), ("a.pdf", b"hello"), ("a.png", b"")]:
            invalid = await api.post("/api/reference/analyze", params={"name": name}, content=content)
            assert (invalid.status_code, invalid.json()["field"]) == (422, "reference")


@pytest.mark.anyio
async def test_targets_expose_ambiguous_markdown_paths(api):
    async with api:
        response = await api.post("/api/reference/targets", json={"source": "# 하나\n## 둘\n## 둘"})
        assert response.status_code == 200
        assert {tuple(x["path"]): x["count"] for x in response.json()["targets"] if x["path"]} == {
            ("하나",): 1, ("하나", "둘"): 2}
        for invalid in [{}, {"source": 3}, {"source": "# 제목", "model": "client"}]:
            assert (await api.post("/api/reference/targets", json=invalid)).status_code == 422


@pytest.mark.anyio
async def test_analysis_rejects_invalid_page_and_can_retry(api):
    from pathlib import Path
    from pypdf import PdfWriter
    from io import BytesIO

    stream = BytesIO()
    # Use text from the existing calibration PDF to avoid contacting a model.
    source = Path(__file__).resolve().parents[1] / "proofs/engine-comparison/LuaLaTeX.pdf"
    writer = PdfWriter()
    writer.append(source, pages=(0, 1))
    writer.write(stream)
    async with api:
        for page in ["0", "two", "2"]:
            response = await api.post("/api/reference/analyze", params={"name":"x.PDF", "page":page},
                                      content=stream.getvalue())
            assert response.status_code == 422
        result = await api.post("/api/reference/analyze?name=x.pdf&page=1", content=stream.getvalue())
        assert result.status_code == 200, result.text
        unit, = result.json()["units"]
        assert unit["page"] == 1 and any(c["key"] == "page_mm" for c in unit["candidates"])


@pytest.mark.anyio
async def test_reference_saturation_times_out_and_releases_slot(api, monkeypatch):
    from threading import BoundedSemaphore
    from app import jobs, server

    entered = asyncio.Event()

    async def blocked(*args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(server, "REFERENCE_SLOTS", BoundedSemaphore(1))
    monkeypatch.setitem(server.REFERENCE, "timeout_seconds", 0.35)
    monkeypatch.setattr(jobs, "isolated_job", blocked)
    path = "/api/reference/analyze?name=reference.pdf"
    async with api:
        first = asyncio.create_task(api.post(path, content=b"%PDF-"))
        await asyncio.wait_for(entered.wait(), 2)
        assert (await api.post(path, content=b"%PDF-")).status_code == 429
        assert (await api.get("/api/reference/settings")).status_code == 200
        assert (await first).status_code == 504
        assert (await api.post(path, content=b"%PDF-")).status_code == 504


def test_operator_model_requires_server_url_and_never_puts_token_in_url(monkeypatch):
    from app.reference import load_settings

    monkeypatch.delenv("JJOKGYEOL_VISION_URL", raising=False)
    monkeypatch.setenv("JJOKGYEOL_VISION_MODEL", "local-test")
    with pytest.raises(ValueError, match="JJOKGYEOL_VISION_URL"):
        load_settings()
    for url in ("file:///tmp/local", "http://secret@host/v1", "http://host/v1?key=private"):
        monkeypatch.setenv("JJOKGYEOL_VISION_URL", url)
        with pytest.raises(ValueError, match="JJOKGYEOL_VISION_URL"):
            load_settings()


@pytest.mark.anyio
async def test_analysis_slot_covers_delivery_of_page_images(api, monkeypatch):
    from threading import BoundedSemaphore
    from app import jobs, server

    async def analyzed(*args):
        return b'{"units":[],"warnings":[]}'

    monkeypatch.setattr(server, "REFERENCE_SLOTS", BoundedSemaphore(1))
    monkeypatch.setattr(jobs, "isolated_job", analyzed)
    writing = asyncio.Event()
    finished = asyncio.Event()
    incoming = asyncio.Queue()
    await incoming.put({"type": "http.request", "body": b"image", "more_body": False})

    async def send(event):
        if event["type"] == "http.response.body":
            writing.set()
            await finished.wait()

    path = "/api/reference/analyze"
    scope = {"type": "http", "method": "POST", "path": path, "root_path": "",
             "headers": [(b"host", b"127.0.0.1")], "query_string": b"name=x.png"}
    first = asyncio.create_task(server.app(scope, incoming.get, send))
    try:
        await asyncio.wait_for(writing.wait(), 2)
        async with api:
            assert (await api.post(path + "?name=y.png", content=b"image")).status_code == 429
    finally:
        finished.set()
        await first


@pytest.mark.anyio
async def test_slow_upload_and_model_preserve_finished_pdf_pages(api, monkeypatch):
    from app.server import REFERENCE
    from test_reference_media import pdf

    connected = asyncio.Event()

    async def delayed_model(reader, writer):
        connected.set()
        await reader.read()  # Hold the response until the worker closes its connection.
        writer.close()
        await writer.wait_closed()

    async with await asyncio.start_server(delayed_model, "127.0.0.1", 0) as model:
        port = model.sockets[0].getsockname()[1]
        monkeypatch.setenv("JJOKGYEOL_VISION_URL", f"http://127.0.0.1:{port}/v1")
        monkeypatch.setenv("JJOKGYEOL_VISION_MODEL", "slow-local-fixture")
        monkeypatch.setitem(REFERENCE, "timeout_seconds", 4)
        sample = pdf([[("REFERENCE", 11, 36, 340)], []])

        async def upload():
            await asyncio.sleep(1)
            yield sample

        async with api:
            response = await api.post("/api/reference/analyze?name=sample.pdf", content=upload())
        assert connected.is_set()
        assert response.status_code == 200, response.text
        first, second = response.json()["units"]
        assert "REFERENCE" in first["excerpt"] and second["page"] == 2
        assert any("2쪽" in warning for warning in response.json()["warnings"])
