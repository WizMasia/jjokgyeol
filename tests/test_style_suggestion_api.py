import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread

import httpx
import pytest

from app import server
from test_decision_providers import api, raw, reply as provider_reply
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
            response = control.get("raw", json.dumps(provider_reply("chat", json.dumps(control["answer"]))).encode())
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
        base = f"http://127.0.0.1:{service.server_port}/v1"
        control["connection"] = raw(base_url=base, model="test-local-model")
        monkeypatch.setenv("JJOKGYEOL_DECISION_BASE_URLS", base)
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
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://127.0.0.1") as api:
        yield api


pytestmark = pytest.mark.anyio


async def test_suggestion_endpoint(client, local_model):
    control, calls, _, _ = local_model
    response = await client.post(ENDPOINT, json={"request": BOTH, "style": {}, "connection": control["connection"]})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json() == {"patch": {"body_pt": "16", "section_rules": [FOOT]}}
    (path, sent), = calls
    assert path == "/v1/chat/completions" and sent["model"] == "test-local-model"
    context = json.loads(sent["messages"][-1]["content"])
    assert context.keys() == {"현재 양식", "글꼴", "요청"}
    assert context["요청"] == BOTH and context["현재 양식"]["body_pt"] == "11"
    assert control["connection"]["api_key"] not in json.dumps(sent)


@pytest.mark.parametrize(("payload", "field"), [
    ({"style": {}}, "request"), ({"request": " ", "style": {}}, "request"),
    ({"request": "가" * 2001, "style": {}}, "request"),
    ({"request": BOTH, "style": {"body_pt": 0}}, "body_pt"),
    ({"request": BOTH, "style": {}, "url": "http://example.invalid"}, "request"),
])
async def test_invalid_request_never_calls_model(client, local_model, payload, field):
    response = await client.post(ENDPOINT, json={**payload, "connection": local_model[0]["connection"]})
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
    ({"raw": b" " * 262144 + json.dumps(provider_reply("chat", '{"edits":[]}')).encode()}, 502),
    ({"raw": b"broken"}, 502), ({"status": 503}, 502),
])
async def test_failed_model_is_atomic(client, local_model, config, status):
    control = local_model[0]
    control.update(config)
    response = await client.post(ENDPOINT, json={"request": BOTH, "style": {}, "connection": control["connection"]})
    assert response.status_code == status
    assert response.json().keys() == {"field", "code", "message"}


async def test_busy_deadline_and_recovery(client, local_model, monkeypatch):
    from app.ai.style import SETTINGS
    monkeypatch.setitem(SETTINGS, "timeout_seconds", 1)
    control, _, entered, release = local_model
    control["stall"] = True
    payload = {"request": BOTH, "style": {}, "connection": control["connection"]}
    first = asyncio.create_task(client.post(ENDPOINT, json=payload))
    assert await asyncio.to_thread(entered.wait, 2)
    assert (await client.post(ENDPOINT, json=payload)).status_code == 429
    assert (await first).status_code == 504
    release.set()
    assert (await client.post(ENDPOINT, json=payload)).status_code == 200


async def test_patch_uses_same_raw_geometry_as_manual_edit(client, local_model):
    # Stored rounding leaves more room than the author's two original decimal margins.
    style = {"page_mm": [".000014", 100], "margins_mm": [0, ".000002", 0, ".000002"]}
    control = local_model[0]
    for value, status in [(".0000101", 422), (".000009", 200)]:
        control["answer"] = reply(("gutter_mm", value))
        response = await client.post(ENDPOINT, json={"request": "제본 여백", "style": style,
                                                     "connection": control["connection"]})
        manual = await client.post("/api/style/export", json={**style, "gutter_mm": value})
        assert response.status_code == manual.status_code == status
        if status == 422:
            assert response.json()["field"] == manual.json()["field"] == "gutter_mm"
        else:
            assert response.json()["patch"] == {"gutter_mm": value}


async def test_changing_one_margin_keeps_other_unrounded_inputs(client, local_model):
    style = {"page_mm": [".000014", 100], "margins_mm": [0, ".000007", 0, 0]}
    control = local_model[0]
    control["answer"] = reply(("margin.left", ".000007"))
    response = await client.post(ENDPOINT, json={"request": "왼쪽 여백 변경", "style": style,
                                                 "connection": control["connection"]})
    assert response.status_code == 422 and response.json()["field"] == "margins_mm"


@pytest.mark.parametrize("connection", [None, {}, {"provider": "rule"}, {"provider": "builtin"}])
async def test_explicit_external_required(client, local_model, monkeypatch, connection):
    control, calls, _, _ = local_model
    monkeypatch.setenv("JJOKGYEOL_STYLE_URL", control["connection"]["base_url"])
    monkeypatch.setenv("JJOKGYEOL_STYLE_MODEL", "legacy")
    payload = {"request": BOTH, "style": {}}
    if connection is not None:
        payload["connection"] = connection
    result = await client.post(ENDPOINT, json=payload)
    assert not calls
    assert result.status_code == 422 and result.json()["field"] == "connection"


@pytest.mark.parametrize(("provider", "path"), [
    ("chat", "/chat/completions"), ("lmstudio", "/chat/completions"), ("responses", "/responses"),
    ("ollama", "/api/chat"), ("gemini", "/models/selected:generateContent"), ("claude", "/messages"),
])
async def test_selected_provider_receives_suggestion(client, api, provider, path):
    api.respond = lambda _: (200, {}, provider_reply(provider, json.dumps(reply(("body_pt", "13")))))
    connection = raw(provider, api.base, model="selected")
    result = await client.post(ENDPOINT, json={"request": BOTH, "style": {"body_pt": 11.25},
                                               "connection": connection})
    assert result.status_code == 200, result.text
    assert result.json() == {"patch": {"body_pt": "13"}}
    (received, _, body), = api.calls
    assert received == "/proxy" + path
    if provider != "gemini":
        assert body["model"] == "selected"
    if provider == "gemini":
        context = body["contents"][0]["parts"][0]["text"]
        prompt = body["systemInstruction"]["parts"][0]["text"]
    elif provider == "responses":
        context = body["input"]
        prompt = body["instructions"]
    else:
        context = body["messages"][-1]["content"]
        prompt = body["system"] if provider == "claude" else body["messages"][0]["content"]
    fields = json.loads(context)["현재 양식"]
    assert fields["body_pt"] == "11.25"
    assert BOTH in context
    if provider in ("lmstudio", "chat"):
        assert body["temperature"] == 0
        assert body["response_format"] == {
            "type": "json_schema", "json_schema": {"name": "style_edits", "schema": {
                "type": "object", "additionalProperties": False, "required": ["edits"],
                "properties": {"edits": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False, "required": ["field", "value"],
                    "properties": {"field": {"type": "string", "enum": list(fields)},
                                   "value": {"type": "string"}},
                }}},
            }},
        }
    else:
        assert "response_format" not in body and "temperature" not in body
        schema = json.loads(prompt.splitlines()[-1])
        assert schema["required"] == ["edits"]
        properties = schema["properties"]["edits"]["items"]["properties"]
        assert properties["field"]["enum"] == list(fields) and properties["value"]["type"] == "string"


@pytest.mark.parametrize(("status", "wire", "code"), [
    (401, {}, "authentication"),
    (404, {"error": {"code": "model_not_found"}}, "model"),
    (503, {}, "unavailable"),
])
async def test_selected_server_failure_never_retries_legacy_server(client, api, local_model, monkeypatch,
                                                                   status, wire, code):
    legacy, legacy_calls, _, _ = local_model
    monkeypatch.setenv("JJOKGYEOL_STYLE_URL", legacy["connection"]["base_url"])
    monkeypatch.setenv("JJOKGYEOL_STYLE_MODEL", legacy["connection"]["model"])
    monkeypatch.setenv("JJOKGYEOL_DECISION_BASE_URLS", api.base + "," + legacy["connection"]["base_url"])
    api.respond = lambda _: (status, {}, wire)
    connection = raw(base_url=api.base, model="selected")
    result = await client.post(ENDPOINT, json={"request": BOTH, "style": {}, "connection": connection})
    assert result.json()["code"] == code
    assert len(api.calls) == 1 and not legacy_calls


@pytest.mark.parametrize("rejection", [
    {"code": "unsupported_parameter", "param": "response_format"},
    {"code": "unsupported_value", "param": "temperature"},
    {"param": "response_format", "message": "JSON schema is not supported with this model."},
])
async def test_chat_without_structured_output_uses_plain_json_on_same_model(client, api, rejection):
    def respond(body):
        if "response_format" in body:
            return 400, {}, {"error": rejection}
        return 200, {}, provider_reply("chat", json.dumps(reply(("body_pt", "13"))))

    api.respond = respond
    result = await client.post(ENDPOINT, json={"request": "본문만 13pt로", "style": {},
                                               "connection": raw("chat", api.base, model="free-model")})
    assert result.status_code == 200 and result.json() == {"patch": {"body_pt": "13"}}
    first, second = api.calls
    assert first[0] == second[0] == "/proxy/chat/completions"
    original, plain = first[2], second[2]
    assert plain["model"] == original["model"] == "free-model"
    assert plain["messages"][1] == original["messages"][1]
    assert "response_format" not in plain and "temperature" not in plain
    schema = original["response_format"]["json_schema"]["schema"]
    assert json.loads(plain["messages"][0]["content"].splitlines()[-1]) == schema


async def test_denied_key_is_rejected_before_model_call(local_model, monkeypatch):
    monkeypatch.setenv("JJOKGYEOL_ALLOWED_HOSTS", "web.example")
    control, calls, _, _ = local_model
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),
                                 base_url="http://web.example") as remote:
        result = await remote.post(ENDPOINT, json={"request": BOTH, "style": {}, "connection": control["connection"]})
    assert result.status_code == 422 and result.json()["code"] == "address"
    assert not calls and control["connection"]["api_key"] not in result.text
