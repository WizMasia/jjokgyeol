"""Wire-level tests for session-scoped decision connections."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread
from time import monotonic, sleep

import pytest


ROOT = Path(__file__).resolve().parents[1]
KEY = "unit-test-credential"
OPTIONS = {"layout-a": [{"end_glyph": 12}], "layout-b": [{"end_glyph": 10}]}
ANSWER = '{"id":"layout-b"}'
DEFAULTS = {
    "responses": "https://api.openai.com/v1",
    "chat": "https://api.openai.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
    "claude": "https://api.anthropic.com/v1",
    "ollama": "http://127.0.0.1:11434",
    "lmstudio": "http://127.0.0.1:1234/v1",
}


def reply(provider, answer=ANSWER):
    if provider == "responses":
        return {"status": "completed", "output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "status": "completed", "role": "assistant",
             "content": [{"type": "output_text", "text": answer}]}]}
    if provider == "gemini":
        return {"candidates": [{"finishReason": "STOP", "content": {"role": "model", "parts": [
            {"text": "thinking", "thought": True}, {"text": answer, "thoughtSignature": "opaque"}]}}]}
    if provider == "claude":
        return {"type": "message", "role": "assistant", "stop_reason": "end_turn",
                "content": [{"type": "thinking", "thinking": "thinking"}, {"type": "text", "text": answer}]}
    message = {"role": "assistant", "content": answer}
    if provider == "ollama":
        return {"done": True, "done_reason": "stop", "message": message}
    return {"choices": [{"finish_reason": "stop", "message": message}]}


def raw(provider="chat", base_url=DEFAULTS["chat"], **extra):
    return {"provider": provider, "base_url": base_url, "model": "sample", "api_key": KEY, **extra}


@pytest.fixture
def api(monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.respond(self.rfile.read(int(self.headers.get("Content-Length", 0))))

        def do_POST(self):
            self.server.last_request_bytes = self.rfile.read(int(self.headers["Content-Length"]))
            self.respond(json.loads(self.server.last_request_bytes))

        def respond(self, body):
            self.server.calls.append((self.path, self.headers, body))
            status, headers, wire = self.server.respond(body)
            if not isinstance(wire, bytes):
                wire = json.dumps(wire).encode()
            try:
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.end_headers()
                for i in range(0, len(wire), self.server.chunk):
                    self.wfile.write(wire[i:i + self.server.chunk])
                    sleep(self.server.delay)
            except OSError:
                pass

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        server.base = f"http://127.0.0.1:{server.server_port}/proxy"
        server.calls = []
        server.delay = 0
        server.chunk = 1024 * 1024
        monkeypatch.setenv("JJOKGYEOL_DECISION_BASE_URLS", "https://other.test/v1, " + server.base)
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join()


def test_registry_and_packaging():
    registry = json.loads((ROOT / "app/config/decision-providers.json").read_text())
    assert {row["id"]: row["base_url"] for row in registry["providers"]} == DEFAULTS
    assert [row["id"] for row in registry["providers"]] == list(DEFAULTS)
    assert all(row["label"] for row in registry["providers"])
    responses, = [row for row in registry["providers"] if row["id"] == "responses"]
    assert "Codex" in responses["label"]
    # The source tree is absent in the Docker test image.
    if (ROOT / ".dockerignore").exists():
        assert "!app/config/decision-providers.json" in (ROOT / ".dockerignore").read_text().splitlines()


@pytest.mark.parametrize("key,value", [("max_tokens", True), ("max_response_bytes", -2)])
def test_invalid_style_budget_rejected_at_load(monkeypatch, key, value):
    import runpy
    from app import configuration
    from app.ai import providers

    read = configuration.read_config

    def bad_config(path):
        config = read(path)
        if path.name == "decision-providers.json":
            config["style_suggestion"][key] = value
        return config

    monkeypatch.setattr(configuration, "read_config", bad_config)
    with pytest.raises(ValueError, match=key):
        runpy.run_path(providers.__file__)


@pytest.mark.parametrize("provider,suffix,auth", [
    ("responses", "/responses", "Authorization"), ("chat", "/chat/completions", "Authorization"),
    ("gemini", "/models/sample:generateContent", "x-goog-api-key"),
    ("claude", "/messages", "x-api-key"), ("ollama", "/api/chat", "Authorization"),
    ("lmstudio", "/chat/completions", "Authorization"),
])
def test_request_and_completed_answer(api, provider, suffix, auth):
    from app.ai.providers import parse_connection

    api.respond = lambda body: (200, {}, reply(provider))
    conn = parse_connection(raw(provider, api.base))
    assert KEY not in repr(conn)
    assert conn.predict(OPTIONS) == "layout-b"
    (path, headers, body), = api.calls
    assert path == "/proxy" + suffix
    assert headers[auth] == ("Bearer " + KEY if auth == "Authorization" else KEY)
    assert headers.get_content_type() == "application/json"
    assert KEY not in json.dumps(body)
    if provider == "responses":
        assert body["store"] is False and body["instructions"]
        assert "max_output_tokens" not in body
        candidate_text = body["input"]
    elif provider == "gemini":
        candidate_text = body["contents"][0]["parts"][0]["text"]
        assert body["systemInstruction"]["parts"][0]["text"]
        assert "maxOutputTokens" not in body.get("generationConfig", {})
    else:
        candidate_text = body["messages"][-1]["content"]
        assert body["messages"][-1]["role"] == "user"
        if provider == "claude":
            assert body["max_tokens"] > 0 and headers["anthropic-version"] and body["system"]
        else:
            assert body["messages"][0]["role"] == "system" and body["stream"] is False
    if provider != "gemini":
        assert body["model"] == "sample"
    assert json.loads(candidate_text) == OPTIONS


def test_check_calls_once_with_example_only_and_optional_key(api):
    from app.ai.providers import check_connection

    def choose(body):
        options = json.loads(body["messages"][-1]["content"])
        assert len(options) >= 2 and all(isinstance(lines, list) for lines in options.values())
        return 200, {}, reply("chat", json.dumps({"id": next(iter(options))}))

    api.respond = choose
    connection = raw(base_url=api.base)
    del connection["api_key"]
    assert check_connection(connection) is None
    (_, headers, _), = api.calls
    assert "Authorization" not in headers
    api.respond = lambda body: (200, {}, reply("chat", '{"id":"invented"}'))
    assert_code("invalid_response", lambda: check_connection(connection))


@pytest.mark.parametrize("provider", DEFAULTS)
def test_generate_json_sends_style_prompt_and_decodes_edits(api, provider, monkeypatch):
    from app.ai import providers

    monkeypatch.setitem(providers.LIMITS, "decision_seconds", 0)
    prompt = "Return JSON style edits."
    payload = {"요청": "본문을 키워 주세요", "현재 양식": {"body_pt": "10.0625"}}
    edits = {"edits": [{"field": "body_pt", "value": "12.125"}]}
    api.respond = lambda _: (200, {}, reply(provider, json.dumps(edits)))
    conn = providers.parse_connection(raw(provider, api.base))
    assert conn.generate_json(prompt, payload, timeout=1) == edits
    (_, _, body), = api.calls
    if provider == "responses":
        instruction, data = body["instructions"], body["input"]
        assert body["max_output_tokens"] == 512
    elif provider == "gemini":
        instruction = body["systemInstruction"]["parts"][0]["text"]
        data = body["contents"][0]["parts"][0]["text"]
        assert body["generationConfig"]["maxOutputTokens"] == 512
    else:
        instruction = body["system"] if provider == "claude" else body["messages"][0]["content"]
        data = body["messages"][-1]["content"]
        if provider == "ollama":
            assert body["options"]["num_predict"] == 512
        elif provider == "chat":
            assert body["max_completion_tokens"] == 512
        else:
            assert body["max_tokens"] == 512
    assert instruction == prompt and json.loads(data) == payload
    assert payload["요청"] in data and payload["요청"].encode("utf-8") in api.last_request_bytes


def test_generate_json_rejects_ambiguous_text_and_expired_deadlines(api):
    from app.ai.providers import parse_connection

    conn = parse_connection(raw(base_url=api.base))
    api.respond = lambda _: (200, {}, reply("chat", '{"edits":[],"edits":[]}'))
    assert_code("invalid_response", lambda: conn.generate_json("prompt", {}, timeout=1))
    for seconds in (0, float("inf"), float("nan")):
        assert_code("timeout", lambda: conn.generate_json("prompt", {}, timeout=seconds))
    assert len(api.calls) == 1


def test_generate_json_bounds_even_valid_style_replies_before_decoding(api):
    from app.ai.providers import parse_connection

    conn = parse_connection(raw(base_url=api.base))
    style_reply = json.dumps(reply("chat", '{"edits":[]}')).encode()
    api.respond = lambda _: (200, {}, style_reply.ljust(32768))
    assert conn.generate_json("prompt", {}, timeout=1) == {"edits": []}
    api.respond = lambda _: (200, {}, style_reply.ljust(32769))
    assert_code("invalid_response", lambda: conn.generate_json("prompt", {}, timeout=1))
    api.respond = lambda _: (200, {}, json.dumps(reply("chat")).encode().ljust(32769))
    assert conn.predict(OPTIONS) == "layout-b"


def assert_code(code, call):
    from app.ai.providers import ProviderError

    with pytest.raises(ProviderError) as error:
        call()
    assert isinstance(error.value, ValueError)
    assert error.value.code == str(error.value) == code


def test_web_addresses_are_provider_specific_and_operator_entries_exact(monkeypatch):
    from app.ai.providers import parse_connection

    custom = "http://intranet.test:8181/proxy/v1"
    monkeypatch.setenv("JJOKGYEOL_DECISION_BASE_URLS", "https://elsewhere.test, " + custom)
    for provider, url in DEFAULTS.items():
        if provider in ("ollama", "lmstudio"):
            assert_code("address", lambda: parse_connection(raw(provider, url)))
            parse_connection(raw(provider, url), desktop=True)
        else:
            parse_connection(raw(provider, url))
    parse_connection(raw(base_url=custom, api_key=""))
    for url in (custom + "/extra", custom.replace("8181", "8182"), custom.replace("http:", "https:"),
                DEFAULTS["ollama"], DEFAULTS["gemini"], "https://api.openai.com.other/v1"):
        assert_code("address", lambda: parse_connection(raw(base_url=url, api_key="")))


@pytest.mark.parametrize("host", ["host.docker.internal", "intranet.test", "192.0.2.1", "[2001:db8::1]"])
def test_allowlisted_remote_http_is_keyless_only(monkeypatch, host):
    from app.ai.providers import parse_connection

    http = f"http://{host}:8181/v1"
    https = f"https://{host}:8181/v1"
    monkeypatch.setenv("JJOKGYEOL_DECISION_BASE_URLS", f"{http},{https}")
    parse_connection(raw(base_url=http, api_key=""))
    parse_connection(raw(base_url=https))
    assert_code("address", lambda: parse_connection(raw(base_url=http)))


@pytest.mark.parametrize("host", ["host.docker.internal", "intranet.test"])
def test_keyless_web_http_request_to_allowlisted_host(api, monkeypatch, host):
    import socket
    from app.ai.providers import parse_connection

    resolve = socket.getaddrinfo

    def resolve_test_host(name, *args):
        assert name == host
        return resolve("127.0.0.1", *args)

    monkeypatch.setattr(socket, "getaddrinfo", resolve_test_host)
    monkeypatch.setattr(socket.socket, "getpeername", lambda sock: ("192.0.2.1", api.server_port))
    base = api.base.replace("127.0.0.1", host)
    monkeypatch.setenv("JJOKGYEOL_DECISION_BASE_URLS", base)
    api.respond = lambda body: (200, {}, reply("chat"))
    assert parse_connection(raw(base_url=base, api_key="")).predict(OPTIONS) == "layout-b"
    (_, headers, _), = api.calls
    assert headers["Host"] == f"{host}:{api.server_port}"
    assert not {"authorization", "x-api-key", "x-goog-api-key"}.intersection(map(str.lower, headers))


@pytest.mark.parametrize("host", ["127.0.0.1", "127.42.0.1", "[::1]", "localhost"])
def test_keyed_web_loopback_still_requires_allowlist(monkeypatch, host):
    from app.ai.providers import parse_connection

    base = f"http://{host}:8181/v1"
    monkeypatch.delenv("JJOKGYEOL_DECISION_BASE_URLS", raising=False)
    assert_code("address", lambda: parse_connection(raw(base_url=base)))
    monkeypatch.setenv("JJOKGYEOL_DECISION_BASE_URLS", base)
    parse_connection(raw(base_url=base))


@pytest.mark.parametrize("desktop,key", [(False, KEY), (True, KEY), (True, "")])
def test_http_loopback_peer_checked_before_sending(api, monkeypatch, desktop, key):
    import socket
    from app.ai.providers import parse_connection

    base = api.base.replace("127.0.0.1", "localhost")
    monkeypatch.setenv("JJOKGYEOL_DECISION_BASE_URLS", base)
    conn = parse_connection(raw(base_url=base, api_key=key), desktop=desktop)
    api.respond = lambda body: (200, {}, reply("chat"))
    assert conn.predict(OPTIONS) == "layout-b"
    api.calls.clear()
    monkeypatch.setattr(socket.socket, "getpeername", lambda sock: ("192.0.2.1", api.server_port))
    assert_code("address", lambda: conn.predict(OPTIONS))
    assert not api.calls


@pytest.mark.parametrize("url", ["http://127.10.20.30:8181/v1", "http://[::1]:8181",
                                 "http://localhost:8181", "https://custom.example.org/proxy/v1"])
def test_desktop_addresses(url):
    from app.ai.providers import parse_connection

    parse_connection(raw(base_url=url), desktop=True)


@pytest.mark.parametrize("url", ["http://localhost.other", "http://192.168.1.3/v1", "ftp://127.0.0.1",
                                 "https://name:secret@api.test/v1", "https://api.test?key=secret",
                                 "https://api.test/#fragment", "https://api.test:99999",
                                 "https://api.test/../other", "https://api.test/%2fother",
                                 "https://api.test\\@elsewhere.test", "https://api.test/\rpath"])
def test_rejects_invalid_native_address(url):
    from app.ai.providers import parse_connection

    assert_code("address", lambda: parse_connection(raw(base_url=url), desktop=True))


@pytest.mark.parametrize("bad", [None, [], {}, {"provider": "invalid"}, {"provider": []},
                                  raw(model="  "), raw(api_key="key\r\ninvalid"), raw(other="field")])
def test_malformed_connection(bad):
    from app.ai.providers import parse_connection

    assert_code("invalid_connection", lambda: parse_connection(bad))


@pytest.mark.parametrize("model", ["../other", "//other.test/path", "other%2Fkey", "a?b", "a:b", "models/..",
                                   "Gemini Flash", "", "has\nnewline"])
def test_invalid_gemini_model_id(model, api):
    from app.ai.providers import parse_connection

    assert_code("model", lambda: parse_connection(raw("gemini", api.base, model=model)))
    assert not api.calls


@pytest.mark.parametrize("answer", ["", "layout-b", '{"id":"other"}', '{"id":12}', '["layout-b"]',
                                    '{"id":"layout-a","id":"layout-b"}', '{"id":"layout-b","extra":0}',
                                    ANSWER + " commentary", "```json\n" + ANSWER + "\n```"])
def test_exact_candidate_object(api, answer):
    from app.ai.providers import parse_connection

    api.respond = lambda body: (200, {}, reply("chat", answer))
    assert_code("invalid_response", lambda: parse_connection(raw(base_url=api.base)).predict(OPTIONS))


@pytest.mark.parametrize("provider", DEFAULTS)
def test_rejects_incomplete_and_refused_even_with_valid_id(api, provider):
    from app.ai.providers import parse_connection

    bad = reply(provider)
    if provider == "responses":
        bad["status"] = "incomplete"
    elif provider in ("chat", "lmstudio"):
        bad["choices"][0]["finish_reason"] = "length"
    elif provider == "gemini":
        bad["candidates"][0]["finishReason"] = "MAX_TOKENS"
    elif provider == "claude":
        bad["stop_reason"] = "max_tokens"
    else:
        bad["done_reason"] = "length"
    conn = parse_connection(raw(provider, api.base))
    api.respond = lambda body: (200, {}, bad)
    assert_code("invalid_response", lambda: conn.predict(OPTIONS))
    bad = reply(provider)
    if provider == "responses":
        bad["output"][-1]["content"].append({"type": "refusal", "refusal": "no"})
    elif provider in ("chat", "lmstudio"):
        bad["choices"][0]["message"]["refusal"] = "no"
    elif provider == "gemini":
        bad["promptFeedback"] = {"blockReason": "SAFETY"}
    elif provider == "claude":
        bad["stop_reason"] = "refusal"
    else:
        bad["message"]["tool_calls"] = [{"function": {}}]
    assert_code("invalid_response", lambda: conn.predict(OPTIONS))


@pytest.mark.parametrize("status,code", [(307, "address"), (401, "authentication"), (403, "authentication"),
                                        (404, "address"), (408, "timeout"), (429, "rate_limit"),
                                        (503, "unavailable"), (504, "timeout")])
def test_http_errors_are_sanitized_and_redirects_never_followed(api, status, code):
    from app.ai.providers import parse_connection

    api.respond = lambda body: (status, {"Location": api.base + "/elsewhere"}, {"private": KEY})
    assert_code(code, lambda: parse_connection(raw(base_url=api.base)).predict(OPTIONS))
    assert len(api.calls) == 1


@pytest.mark.parametrize("provider,body", [
    ("responses", {"error": {"code": "model_not_found", "message": KEY}}),
    ("chat", {"error": {"type": "invalid_request_error", "message": f"The model `{KEY}` does not exist."}}),
    ("gemini", {"error": {"code": 404, "status": "NOT_FOUND", "message":
                          f"models/{KEY} is not found for API version v1beta, or is not supported for generateContent."}}),
    ("gemini", {"error": {"code": 404, "status": "NOT_FOUND", "message":
                          f"models/{KEY} is not supported for generateContent."}}),
    ("chat", {"error": {"message": "This model is not supported on this endpoint."}}),
    ("gemini", {"error": {"status": "NOT_FOUND", "message": KEY, "details": [
        {"@type": "type.googleapis.com/google.rpc.ResourceInfo", "resourceName": "models/sample"}]}}),
    ("chat", {"error": {"message": f"The model sample is not supported on this endpoint. {KEY}"}}),
    ("claude", {"type": "error", "error": {"type": "not_found_error", "message": f"model: {KEY}"}}),
    ("ollama", {"error": f"model '{KEY}' not found, try pulling it first"}),
])
def test_model_specific_404(api, provider, body):
    from app.ai.providers import parse_connection

    api.respond = lambda request: (404, {}, body)
    assert_code("model", lambda: parse_connection(raw(provider, api.base)).predict(OPTIONS))


@pytest.mark.parametrize("provider,body", [
    ("responses", b"<html>404 Not Found</html>"),
    ("chat", {"error": {"message": f"Unknown path /v1/models/{KEY} not found"}}),
    ("gemini", {"error": {"code": 404, "status": "NOT_FOUND", "message": f"Resource {KEY} not found"}}),
    ("claude", {"type": "error", "error": {"type": "not_found_error", "message": f"Unknown endpoint {KEY}"}}),
    ("ollama", {"error": f"route /api/chat for model {KEY} not found"}),
    ("gemini", {"error": {"status": "NOT_FOUND", "message": KEY, "details": [
        {"@type": "type.googleapis.com/google.rpc.ResourceInfo", "resourceName": "files/sample"}]}}),
    ("chat", {"error": {"message": "Model endpoint not found"}}),
    ("chat", b'{"error":{"code":"model_not_found"},"error":{}}'),
])
def test_route_or_unrecognized_404_reports_address(api, provider, body):
    from app.ai.providers import parse_connection

    api.respond = lambda request: (404, {}, body)
    assert_code("address", lambda: parse_connection(raw(provider, api.base)).predict(OPTIONS))


@pytest.mark.parametrize("headers", [{}, {"Content-Length": "262145"}])
def test_404_body_larger_than_limit_falls_back_to_address(api, headers):
    from app.ai.providers import parse_connection

    body = b'{"error":{"code":"model_not_found"}}'.ljust(262145)
    api.respond = lambda request: (404, headers, body)
    assert_code("address", lambda: parse_connection(raw(base_url=api.base)).predict(OPTIONS))


@pytest.mark.parametrize("response", [b'{"choices":[],"choices":[]}', b'\xff', b'[]', b' ' * 262145,
                                      b'{"choices":[{"finish_reason":"stop","message":{"content":null}}]}'])
def test_malformed_or_oversize_wire_response(api, response):
    from app.ai.providers import parse_connection

    api.respond = lambda body: (200, {}, response)
    assert_code("invalid_response", lambda: parse_connection(raw(base_url=api.base)).predict(OPTIONS))


def test_total_timeout_even_with_continuous_data(api):
    from app.ai.providers import parse_connection

    api.chunk = 1
    api.delay = 0.025
    api.respond = lambda body: (200, {}, reply("chat"))
    conn = parse_connection(raw(base_url=api.base))
    start = monotonic()
    assert_code("timeout", lambda: conn.predict(OPTIONS, timeout=0.12))
    assert monotonic() - start < 0.8
    assert_code("timeout", lambda: conn.predict(OPTIONS, timeout=0))
    assert len(api.calls) == 1


def test_codex_uses_completed_final_text_after_commentary(api):
    from app.ai.providers import parse_connection

    result = reply("responses")
    final = result["output"][-1]
    final.update(phase="final_answer")
    result["output"].insert(1, {**final, "phase": "commentary",
                                "content": [{"type": "output_text", "text": "Checking widths."}]})
    api.respond = lambda body: (200, {}, result)
    conn = parse_connection(raw("responses", api.base))
    assert conn.predict(OPTIONS) == "layout-b"
    final["status"] = "incomplete"
    assert_code("invalid_response", lambda: conn.predict(OPTIONS))
    result["output"].pop()
    assert_code("invalid_response", lambda: conn.predict(OPTIONS))


@pytest.mark.parametrize("fault", ["duplicate_answer", "tool_call", "refusal", "duplicate_json",
                                   "empty", "bad_json_number", "wrong_role"])
def test_rejects_ambiguous_or_malformed_completed_responses(api, fault):
    from app.ai.providers import parse_connection

    result = reply("responses")
    final = result["output"][-1]
    if fault == "duplicate_answer":
        result["output"].append(final)
    elif fault == "tool_call":
        result["output"].append({"type": "function_call", "status": "completed"})
    elif fault == "refusal":
        final["content"].insert(0, {"type": "refusal", "refusal": "private"})
    elif fault == "duplicate_json":
        result = json.dumps(result).replace('"status": "completed"', '"status":"failed","status":"completed"').encode()
    elif fault == "bad_json_number":
        result = json.dumps(result).replace('"summary": []', '"summary": [NaN]').encode()
    elif fault == "empty":
        final["content"] = []
    else:
        final["role"] = "user"
    api.respond = lambda body: (200, {}, result)
    assert_code("invalid_response", lambda: parse_connection(raw("responses", api.base)).predict(OPTIONS))


@pytest.mark.parametrize("error,code", [
    ({"message": KEY, "details": [{"reason": "API_KEY_INVALID"}]}, "authentication"),
    ({"message": KEY, "code": "model_not_found"}, "model"),
    ({"message": KEY, "param": "model"}, "model"),
    ({"message": KEY}, "invalid_connection"),
])
def test_structured_bad_request_errors(api, error, code):
    from app.ai.providers import parse_connection

    api.respond = lambda body: (400, {}, {"error": error})
    assert_code(code, lambda: parse_connection(raw(base_url=api.base)).predict(OPTIONS))


def test_ollama_accepts_published_api_base(api):
    from app.ai.providers import parse_connection

    api.respond = lambda body: (200, {}, reply("ollama"))
    parse_connection(raw("ollama", api.base + "/api/"), desktop=True).predict(OPTIONS)
    assert api.calls[0][0] == "/proxy/api/chat"


def test_custom_internationalized_host_and_path(api):
    from app.ai.providers import parse_connection

    parse_connection(raw(base_url="https://예시.한국/서비스/v1"), desktop=True)
    conn = parse_connection(raw(base_url=api.base + "/서비스"), desktop=True)
    api.respond = lambda body: (200, {}, reply("chat"))
    assert conn.predict(OPTIONS) == "layout-b"
    assert api.calls[0][0] == "/proxy/%EC%84%9C%EB%B9%84%EC%8A%A4/chat/completions"


def test_expired_dns_cannot_send_a_late_request(api, monkeypatch):
    from http.client import HTTPConnection
    import socket
    from threading import Event

    from app.ai.providers import parse_connection

    resolve = socket.getaddrinfo
    close = HTTPConnection.close
    release, closed = Event(), Event()

    def delayed(*args):
        release.wait(2)
        return resolve(*args)

    def record_close(conn):
        close(conn)
        closed.set()

    monkeypatch.setattr(socket, "getaddrinfo", delayed)
    monkeypatch.setattr(HTTPConnection, "close", record_close)
    conn = parse_connection(raw(base_url=api.base))
    try:
        assert_code("timeout", lambda: conn.predict(OPTIONS, timeout=0.1))
    finally:
        release.set()
    assert closed.wait(1)
    assert not api.calls
