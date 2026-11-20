import asyncio
import errno
import io
import json
import os
from pathlib import Path
import sys

import httpx
import pytest
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def anyio_backend():
    return "asyncio"


pytestmark = pytest.mark.anyio


@pytest.fixture
def api(tmp_path, monkeypatch):
    from app import jobs, server

    # Replace only the engine. Use the real validator and worker entry point.
    (tmp_path / "typeset.py").write_text('''
import io, json, os, subprocess, sys, time
from pathlib import Path
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

def typeset(doc, style, workdir):
    doc = doc.blocks[0].inlines[0].text
    markers = Path(__file__).parent
    if doc in {"crash", "partial", "stall", "first", "second"}:
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        marker = markers / (doc + ".tmp")
        marker.write_text(json.dumps([str(workdir), os.getpid(), child.pid]))
        marker.replace(markers / doc)
        (workdir / "unfinished.pdf").write_bytes(b"%PDF-partial")
        if doc == "crash": os._exit(9)
        if doc == "partial": return b"%PDF-partial"
        while not (markers / "release").exists(): time.sleep(.01)
    writer = PdfWriter()
    page = writer.add_blank_page(200, 200)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    content = DecodedStreamObject()
    content.set_data(b"BT /F1 10 Tf <" + doc.encode().hex().encode() + b"> Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()
''')
    launcher = (
        f"import sys, runpy; sys.path.insert(0, {str(ROOT)!r}); "
        f"import app; app.__path__ = [{str(tmp_path)!r}, *app.__path__]; "
        "runpy.run_module('app.jobs', run_name='__main__')"
    )
    monkeypatch.setattr(jobs, "WORKER_CMD", (sys.executable, "-IB", "-c", launcher))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://local")


def render(client, source, **extra):
    return client.post("/api/render", json={"source": source, "style": {}, **extra})


async def marker(tmp_path, name):
    async with asyncio.timeout(5):
        while not (tmp_path / name).exists():
            await asyncio.sleep(.01)
    return json.loads((tmp_path / name).read_text())


def assert_pdf(response, expected):
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "no-store"
    assert PdfReader(io.BytesIO(response.content)).pages[0].extract_text() == expected


def assert_error(response, status, field=None):
    assert response.status_code == status
    assert set(response.json()) == {"code", "message", "field"}
    assert response.json()["code"] and response.json()["message"]
    assert response.json()["field"] == field


async def test_two_clients(api, tmp_path):
    async with api:
        tasks = [asyncio.create_task(render(api, name)) for name in ("first", "second")]
        first, second = await asyncio.gather(*(marker(tmp_path, n) for n in ("first", "second")))
        assert first[0] != second[0]
        (tmp_path / "release").touch()
        for name, response in zip(("first", "second"), await asyncio.gather(*tasks)):
            assert_pdf(response, name)


async def test_capacity(api, tmp_path):
    async with api:
        tasks = [asyncio.create_task(render(api, name)) for name in ("first", "second")]
        try:
            await asyncio.gather(*(marker(tmp_path, n) for n in ("first", "second")))
            assert_error(await render(api, "third"), 429)
        finally:
            (tmp_path / "release").touch()
            await asyncio.gather(*tasks)
        assert_pdf(await render(api, "retry"), "retry")


@pytest.mark.parametrize("mode", ["crash", "partial", "timeout", "cancel", "disconnect"])
async def test_cleanup_and_retry(api, tmp_path, monkeypatch, mode):
    from app import jobs, server

    source = mode if mode in {"crash", "partial"} else "stall"
    if mode == "timeout":
        monkeypatch.setattr(jobs, "TIMEOUT", 1.5)
    async with api:
        if mode == "disconnect":
            incoming = asyncio.Queue()
            await incoming.put({"type": "http.request", "body": json.dumps(
                {"source": source, "style": {}}).encode(), "more_body": False})
            sent = []
            async def send(event):
                sent.append(event)
            scope = {"type": "http", "method": "POST", "path": "/api/render",
                     "root_path": "", "headers": [], "query_string": b""}
            task = asyncio.create_task(server.app(scope, incoming.get, send))
        else:
            task = asyncio.create_task(render(api, source))
        directory, worker, child = await marker(tmp_path, source)
        if mode == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif mode == "disconnect":
            await incoming.put({"type": "http.disconnect"})
            await asyncio.wait_for(task, 2)
            assert not sent
        else:
            assert_error(await task, 504 if mode == "timeout" else 500)
        assert not Path(directory).exists()
        for pid in (worker, child):
            with pytest.raises(OSError) as exc:
                os.kill(pid, 0)
            assert exc.value.errno == errno.ESRCH
        assert_pdf(await render(api, "retry"), "retry")


async def test_input_limit(api):
    async def chunks():
        yield b" " * (1024 * 1024)
        yield b"x"
        pytest.fail("read past the first oversized chunk")

    async with api:
        assert_error(await api.post("/api/render", content=chunks()), 413)


@pytest.mark.parametrize(("data", "field"), [
    (b"not json", None), (b'{"style":{}}', "source"),
    (b'{"source":"ok","style":[]}', "style"),
    (b'{"source":"bad-note[^x]","style":{}}', "source"),
    (b'{"source":"ok","style":{"private-key":1}}', "style"),
    (b'{"source":"ok","style":{"body_pt":0}}', "body_pt"),
    (b'{"source":"ok","style":{},"candidate":"A"}', None),
])
async def test_validation_privacy(api, data, field):
    async with api:
        response = await api.post("/api/render", content=data)
        assert_error(response, 422, field)
        assert "private-key" not in response.text and "bad-note" not in response.text


async def test_static_paths(api):
    async with api:
        response = await api.get("/sample.md")
        assert response.content == (ROOT / "proofs/engine-comparison/manuscript.md").read_bytes()
        for path in ("/app/jobs.py", "/output.pdf", "/docs", "/openapi.json"):
            assert (await api.get(path)).status_code == 404


@pytest.mark.parametrize(("source", "style", "field", "message"), [
    ("비공개 원고", {"margins_mm": [20, 75, 20, 75]}, "margins_mm",
     "여백을 줄여 본문 폭과 높이를 확보하세요."),
    ("비공개 원고", {"space_pct": [115, 90]}, "space_pct",
     "70~130% 안에서 최소와 최대를 순서대로 지정하세요."),
    ("[^비공개]", {}, "source", "각주 참조에 해당하는 정의가 없습니다."),
    ("[^비공개]: 하나\n[^비공개]: 둘", {}, "source", "각주 정의가 중복되었습니다."),
    ("비공개 원고", {"body_font": "/private/비공개"}, "body_font", "지원하는 글꼴 ID를 선택하세요."),
])
async def test_input_error_reaches_editor(source, style, field, message):
    from app.server import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
        response = await render(client, source, style=style)
    assert response.status_code == 422
    assert response.json() == {"code": "invalid_input", "field": field, "message": message}


async def test_stalled_pdf_delivery_releases_slots(api, monkeypatch):
    from app import jobs, server

    monkeypatch.setattr(jobs, "TIMEOUT", 1)
    scope = {"type": "http", "method": "POST", "path": "/api/render", "root_path": "",
             "headers": [], "query_string": b""}
    started = asyncio.Queue()

    async def stalled_client():
        events = asyncio.Queue()
        await events.put({"type": "http.request", "body": b'{"source":"ok","style":{}}'})

        async def send(event):
            if event["type"] == "http.response.start":
                assert event["status"] == 200
            else:
                await started.put(True)
                await asyncio.Event().wait()

        try:
            await server.app(scope, events.get, send)
        except TimeoutError:
            pass  # An already-started response can only close the connection.

    tasks = [asyncio.create_task(stalled_client()) for _ in range(2)]
    try:
        async with api, asyncio.timeout(3):
            await started.get()
            await started.get()
            assert_error(await render(api, "busy"), 429)
            done, _ = await asyncio.wait(tasks, timeout=1.5)
            assert len(done) == 2, "a stalled transfer retained a render slot"
            assert_pdf(await render(api, "retry"), "retry")
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_first_editor_response_uses_bootstrap(api, monkeypatch):
    import re
    from app import server
    from app import style

    data = {"defaults": {"body_pt": "11.125"},
            "fonts": [{"id": "proof", "label": '글꼴 </ScRiPt> <교정> & "줄"\u2028끝'}]}
    monkeypatch.setattr(style, "editor_bootstrap", lambda: data)
    async with api:
        response = await api.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.text == server.editor_html()
    scripts = re.findall(r'<script type="application/json" id="editor-settings">(.*?)</script>',
                         response.text, re.IGNORECASE | re.DOTALL)
    assert len(scripts) == 1
    assert "<" not in scripts[0]
    assert json.loads(scripts[0]) == data


async def test_render_decision_is_opt_in(api, monkeypatch):
    monkeypatch.delenv("JJOKGYEOL_DECISION_HOST", raising=False)
    async with api:
        for options in ({}, {"decision": "rule"}, {"decision": "model"}):
            response = await render(api, "ok", **options)
            assert_pdf(response, "ok")
            assert response.headers["X-Jjokgyeol-Decision"] == "rule"
        assert_error(await render(api, "ok", decision="https://example.org"), 422, "decision")
