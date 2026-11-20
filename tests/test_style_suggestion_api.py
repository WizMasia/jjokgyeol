import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread

import httpx
import pytest

from app import server
from test_style_ai import BOTH, FOOT, reply

ENDPOINT = "/api/style-suggestion"


@pytest.fixture
def local_model(monkeypatch):
    calls = []
    entered, release = Event(), Event()
    control = {"answer": reply(("body_pt", "16"), ("footer.center", "{현재쪽}"))}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            entered.set()
            if control.get("stall"):
                release.wait(5)
            response = control.get("raw", json.dumps({"choices": [
                {"message": {"content": json.dumps(control["answer"])}}]}).encode())
            self.send_response(control.get("status", 200))
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            try:
                self.wfile.write(response)
            except OSError:
                pass

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as service:
        thread = Thread(target=service.serve_forever, kwargs={"poll_interval": .02}, daemon=True)
        thread.start()
        monkeypatch.setenv("JJOKGYEOL_STYLE_URL", f"http://127.0.0.1:{service.server_port}/v1")
        monkeypatch.setenv("JJOKGYEOL_STYLE_MODEL", "test-local-model")
        try:
            yield control, calls, entered, release
        finally:
            release.set()
            service.shutdown()
            thread.join()


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://local") as api:
        yield api


pytestmark = pytest.mark.anyio


async def test_suggestion_endpoint(client, local_model):
    _, calls, _, _ = local_model
    response = await client.post(ENDPOINT, json={"request": BOTH, "style": {}})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json() == {"patch": {"body_pt": "16", "section_rules": [FOOT]}}
    (path, sent), = calls
    assert path == "/v1/chat/completions" and sent["model"] == "test-local-model"
    assert sent["max_tokens"] > 0 and sent["response_format"]["type"] == "json_schema"
    assert BOTH in sent["messages"][-1]["content"]


@pytest.mark.parametrize(("payload", "field"), [
    ({"style": {}}, "request"), ({"request": " ", "style": {}}, "request"),
    ({"request": "가" * 2001, "style": {}}, "request"),
    ({"request": BOTH, "style": {"body_pt": 0}}, "body_pt"),
    ({"request": BOTH, "style": {}, "url": "http://example.invalid"}, "request"),
])
async def test_invalid_request_never_calls_model(client, local_model, payload, field):
    response = await client.post(ENDPOINT, json=payload)
    assert response.status_code == 422 and response.json()["field"] == field
    assert not local_model[1]


async def test_stream_limit(client, local_model):
    async def chunks():
        yield b" " * 4096
        yield b" " * 65536
    response = await client.post(ENDPOINT, content=chunks())
    assert response.status_code == 413
    assert not local_model[1]


@pytest.mark.parametrize(("config", "status"), [
    ({"answer": reply(("body_pt", "14"), ("list.1.expr", "{없는토큰}"))}, 422),
    ({"raw": b"x" * 32769}, 502), ({"raw": b"broken"}, 502), ({"status": 503}, 502),
])
async def test_failed_model_is_atomic(client, local_model, config, status):
    local_model[0].update(config)
    response = await client.post(ENDPOINT, json={"request": BOTH, "style": {}})
    assert response.status_code == status
    assert response.json().keys() == {"field", "code", "message"}


async def test_busy_deadline_and_recovery(client, local_model, monkeypatch):
    from app.ai.style import SETTINGS
    monkeypatch.setitem(SETTINGS, "timeout_seconds", 1)
    control, _, entered, release = local_model
    control["stall"] = True
    payload = {"request": BOTH, "style": {}}
    first = asyncio.create_task(client.post(ENDPOINT, json=payload))
    assert await asyncio.to_thread(entered.wait, 2)
    assert (await client.post(ENDPOINT, json=payload)).status_code == 429
    assert (await first).status_code == 504
    release.set()
    assert (await client.post(ENDPOINT, json=payload)).status_code == 200


async def test_patch_uses_same_raw_geometry_as_manual_edit(client, local_model):
    # Stored rounding leaves more room than the author's two original decimal margins.
    style = {"page_mm": [".000014", 100], "margins_mm": [0, ".000002", 0, ".000002"]}
    for value, status in [(".0000101", 422), (".000009", 200)]:
        local_model[0]["answer"] = reply(("gutter_mm", value))
        response = await client.post(ENDPOINT, json={"request": "제본 여백", "style": style})
        manual = await client.post("/api/style/export", json={**style, "gutter_mm": value})
        assert response.status_code == manual.status_code == status
        if status == 422:
            assert response.json()["field"] == manual.json()["field"] == "gutter_mm"
        else:
            assert response.json()["patch"] == {"gutter_mm": value}


async def test_changing_one_margin_keeps_other_unrounded_inputs(client, local_model):
    style = {"page_mm": [".000014", 100], "margins_mm": [0, ".000007", 0, 0]}
    local_model[0]["answer"] = reply(("margin.left", ".000007"))
    response = await client.post(ENDPOINT, json={"request": "왼쪽 여백 변경", "style": style})
    assert response.status_code == 422 and response.json()["field"] == "margins_mm"
