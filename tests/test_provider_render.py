from concurrent.futures import ThreadPoolExecutor
from email import policy
from email.parser import BytesParser
from functools import partial
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
import pytest

from app import jobs, server
from test_decision import SOURCE, STYLE, candidates, model_server, write_rows
from test_decision_providers import api as provider_api, reply as provider_reply


def test_desktop_can_import_decision_without_posix_resource():
    result = subprocess.run(
        [sys.executable, "-B", "-c",
         'import sys; sys.modules["resource"] = None; from app.ai import decision'],
        capture_output=True, cwd=Path(__file__).resolve().parents[1],
    )
    assert result.returncode == 0, result.stderr


@pytest.fixture
def workdirs(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "TemporaryDirectory", partial(TemporaryDirectory, dir=tmp_path, delete=False))
    return tmp_path


def assert_private(directory):
    for path in directory.rglob("*"):
        if path.is_file():
            assert b"session-marker" not in path.read_bytes(), path.name


@pytest.mark.parametrize(("route", "body"), [
    ("/api/render", b'{"connection":{"api_key":"session-marker"}'),
    ("/api/render", b'{"source":"hi","connection":{"api_key":"session-marker"},"connection":{"provider":"rule"}}'),
    ("/api/render", b'{"source":"hi","unknown":{"api_key":"session-marker"}}'),
    ("/api/render", b'{"source":null,"connection":{"provider":"chat","api_key":"session-marker",'
                    b'"base_url":"https://api.openai.com/v1","model":"sample"}}'),
    ("/api/decision/check", b'{"api_key":"session-marker"'),
    ("/api/decision/check", b'{"api_key":"session-marker","api_key":""}'),
])
def test_credentials_never_reach_worker_files_or_launch_arguments(workdirs, monkeypatch, route, body):
    launch = subprocess.Popen

    def inspected(command, **kwargs):
        assert_private(Path(kwargs["cwd"]))
        assert "session-marker" not in repr((command, kwargs["env"]))
        return launch(command, **kwargs)

    monkeypatch.setattr(jobs.subprocess, "Popen", inspected)
    with TestClient(server.app, base_url="http://127.0.0.1") as client:
        reply = client.post(route, content=body)
    assert reply.status_code == 422
    assert b"session-marker" not in reply.content
    assert_private(workdirs)


def test_invalid_check_has_fixed_error_and_body_limit(monkeypatch):
    with TestClient(server.app, base_url="http://127.0.0.1") as client:
        for body in [b'{"api_key":"session-marker"', b'{"provider":0,"api_key":"session-marker"}',
                     b'{"api_key":"session-marker","api_key":""}']:
            reply = client.post("/api/decision/check", content=body)
            assert reply.status_code == 422
            assert reply.json()["code"] == "invalid_connection"
            assert reply.json()["field"] == "connection"
            assert b"session-marker" not in reply.content
            assert reply.headers["Cache-Control"] == "no-store"
        monkeypatch.setattr(server, "MAX_BODY", 10)
        assert client.post("/api/decision/check", content=b' ' * 11).status_code == 413


@pytest.mark.parametrize(("late", "expected"), [(None, "model"), ("result", "rule"), ("error", "rule")])
def test_portable_replay_honors_total_deadline_without_posix_timer(tmp_path, monkeypatch, late, expected):
    from app.ai import decision
    from app.style import Style

    now = 10.0
    monkeypatch.setattr(decision, "monotonic", lambda: now)
    monkeypatch.delattr(decision.signal, "setitimer", raising=False)

    def engine(doc, style, folder, **kwargs):
        nonlocal now
        choice = kwargs.get("paragraph_choices", {}).get(1, "rule")
        write_rows(folder, [{"block": 1, "rule_id": "rule", "selected_id": choice,
                             "candidates": candidates()[:2]}])
        pdf = b"%PDF-" + choice.encode() + b"\n%%EOF"
        (folder / "book.pdf").write_bytes(pdf)
        if late and folder.name == "replay":
            now += 15
            if late == "error":
                raise ValueError("engine failed during replay")
        return pdf

    class Connection:
        def predict(self, options, *, timeout):
            assert 0 < timeout <= 10
            assert "private" not in json.dumps(options)
            return "other"

    pdf, status, reason = decision.render_provider_choice(
        None, Style(), tmp_path, 20.0, Connection(), typesetter=engine)
    assert status == expected
    assert reason == ("timeout" if late else None)
    assert pdf == (tmp_path / ("measure" if late else "replay") / "book.pdf").read_bytes()


def test_check_and_mixed_proof_use_the_requested_model_and_keep_credentials_private(workdirs, monkeypatch):
    requests = []

    def answer(path, data):
        requests.append(data["model"])
        options = json.loads(data["messages"][-1]["content"])
        picked = next(iter(options))
        if data["model"] == "mixed" and requests.count("mixed") == 2:
            picked = "session-marker"
        return {"choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": json.dumps({"id": picked})}}]}

    with model_server(answer) as port, TestClient(server.app, base_url="http://127.0.0.1") as client:
        url = f"http://127.0.0.1:{port}/v1"
        monkeypatch.setenv("JJOKGYEOL_DECISION_BASE_URLS", url)
        raw = {"provider": "chat", "base_url": url, "api_key": "session-marker", "model": "check"}
        probe = client.post("/api/decision/check", json=raw)
        assert probe.status_code == 200 and probe.json() == {"ok": True}
        raw["model"] = "mixed"
        reply = client.post("/api/render", headers={"Accept": "multipart/form-data"}, json={
            "source": "풀잎 사이 Dew 은빛 이슬\n\n" * 2, "style": STYLE, "connection": raw})
        assert reply.status_code == 200
        assert reply.headers["X-Jjokgyeol-Decision"] == "mixed"
        assert reply.headers["X-Jjokgyeol-Decision-Reason"] == "invalid_response"
        packet = BytesParser(policy=policy.default).parsebytes(
            f"Content-Type: {reply.headers['Content-Type']}\r\n\r\n".encode() + reply.content)
        rule, model = (part.get_payload(decode=True) for part in packet.iter_parts())
        measure, = workdirs.glob("*/measure/book.pdf")
        assert rule == measure.read_bytes()
        assert model == (measure.parents[1] / "replay/book.pdf").read_bytes()
        assert requests == ["check", "mixed", "mixed"]

    assert_private(workdirs)


def test_rule_connection_overrides_operator_and_rejects_unknown_fields(monkeypatch):
    monkeypatch.setenv("JJOKGYEOL_DECISION_HOST", "127.0.0.1")
    with TestClient(server.app, base_url="http://127.0.0.1") as client:
        payload = {"source": SOURCE, "style": STYLE, "decision": "model"}
        good = client.post("/api/render", json={**payload, "connection": {"provider": "rule"}})
        assert good.status_code == 200 and good.content.startswith(b"%PDF-")
        assert good.headers["X-Jjokgyeol-Decision"] == "rule"
        assert "X-Jjokgyeol-Decision-Reason" not in good.headers
        for raw in [None, {"provider": "rule", "api_key": "session-marker"}]:
            invalid = client.post("/api/render", json={**payload, "connection": raw})
            assert invalid.status_code == 422 and invalid.json()["code"] == "invalid_connection"
            assert b"session-marker" not in invalid.content


def test_explicit_rule_skips_model_connection(monkeypatch):
    calls = []

    def answer(path, payload):
        calls.append(path)
        return {}

    with model_server(answer) as port, TestClient(server.app, base_url="http://127.0.0.1") as client:
        base = f"http://127.0.0.1:{port}/v1"
        monkeypatch.setenv("JJOKGYEOL_DECISION_BASE_URLS", base)
        response = client.post("/api/render", headers={"Accept": "multipart/form-data"}, json={
            "source": SOURCE, "style": STYLE, "decision": "rule",
            "connection": {"provider": "chat", "base_url": base, "model": "fixture", "api_key": ""},
        })
    assert calls == []
    assert response.status_code == 200
    assert response.headers["X-Jjokgyeol-Decision"] == "rule"
    assert "X-Jjokgyeol-Decision-Reason" not in response.headers
    assert response.content.startswith(b"%PDF-")


def test_operator_model_failure_reports_rule_reason(monkeypatch):
    monkeypatch.setenv("JJOKGYEOL_DECISION_HOST", "disabled")
    with TestClient(server.app, base_url="http://127.0.0.1") as client:
        reply = client.post("/api/render", json={"source": SOURCE, "style": STYLE, "decision": "model"})
    assert reply.status_code == 200 and reply.content.startswith(b"%PDF-")
    assert reply.headers["X-Jjokgyeol-Decision"] == "rule"
    assert reply.headers.get("X-Jjokgyeol-Decision-Reason") == "unavailable"


def test_simultaneous_sessions_keep_keys_and_model_failures_separate(provider_api):
    def answer(data):
        if data["model"] == "denied":
            return 401, {}, b"private upstream text"
        options = json.loads(data["messages"][-1]["content"])
        return 200, {}, provider_reply("chat", json.dumps({"id": next(iter(options))}))

    provider_api.respond = answer

    def request(model):
        connection = {"provider": "chat", "base_url": provider_api.base, "model": model,
                      "api_key": f"session-{model}"}
        with TestClient(server.app, base_url="http://127.0.0.1") as client:
            return client.post("/api/render", json={"source": SOURCE, "style": STYLE, "connection": connection})

    with ThreadPoolExecutor(max_workers=2) as pool:
        denied, allowed = pool.map(request, ("denied", "allowed"))
    assert denied.status_code == allowed.status_code == 200
    assert denied.headers["X-Jjokgyeol-Decision"] == "rule"
    assert denied.headers["X-Jjokgyeol-Decision-Reason"] == "authentication"
    assert allowed.headers["X-Jjokgyeol-Decision"] == "model"
    assert "X-Jjokgyeol-Decision-Reason" not in allowed.headers
    assert {(body["model"], headers["Authorization"]) for _, headers, body in provider_api.calls} == {
        ("denied", "Bearer session-denied"), ("allowed", "Bearer session-allowed")}
    assert b"private upstream text" not in denied.content


@pytest.mark.parametrize("provider,model,suffix,reason", [
    ("chat", "layout", "/unlisted", "address"), ("chat", "layout", "?invalid", "address"),
    ("gemini", "Gemini 2.5 Flash", "", "model"),
])
def test_rejected_provider_setting_returns_current_rule_proof(provider_api, provider, model, suffix, reason, workdirs):
    from test_decision import scan

    raw = {"provider": provider, "base_url": provider_api.base + suffix, "model": model, "api_key": ""}
    with TestClient(server.app, base_url="http://localhost") as client:
        check = client.post("/api/decision/check", json=raw)
        reply = client.post("/api/render", headers={"Accept": "multipart/form-data"}, json={
            "source": SOURCE, "style": STYLE, "connection": raw})
        assert reply.status_code == 200
        assert reply.headers["X-Jjokgyeol-Decision"] == "rule"
        assert reply.headers["X-Jjokgyeol-Decision-Reason"] == reason
        assert check.status_code == 422 and check.json()["code"] == reason
        baseline, = workdirs.glob("*/measure/book.pdf")
        assert reply.content == baseline.read_bytes()
        assert scan(reply.content)[0] == [9, 11]
        bad = client.post("/api/render", json={"source": None, "connection": raw})
        assert bad.status_code == 422 and bad.json()["field"] == "source"
        extra = client.post("/api/render", json={"source": SOURCE, "connection": {**raw, "extra": 1}})
        assert extra.status_code == 422 and extra.json()["code"] == "invalid_connection"
    assert not provider_api.calls


@pytest.mark.parametrize("failure", ["authentication", "model", "address"])
@pytest.mark.parametrize("success_first", [False, True])
def test_terminal_provider_failure_stops_requests_and_retains_verified_choices(tmp_path, failure, success_first):
    from time import monotonic
    from app.ai.decision import render_provider_choice
    from app.ai.providers import ProviderError
    from app.style import Style

    class Connection:
        calls = 0

        def predict(self, options, *, timeout):
            self.calls += 1
            if self.calls == 1 and success_first:
                return "other"
            raise ProviderError(failure)

    def engine(doc, style, folder, *, collect_candidates, paragraph_choices=None):
        picks = paragraph_choices or {}
        write_rows(folder, [{"block": n, "rule_id": "rule", "selected_id": picks.get(n, "rule"),
                             "candidates": candidates()[:2]} for n in (1, 2, 3)])
        return b"%PDF-" + json.dumps(picks).encode() + b"\n%%EOF"

    connection = Connection()
    pdf, status, reason = render_provider_choice(
        None, Style(), tmp_path, monotonic() + 25, connection, typesetter=engine)
    assert connection.calls == (2 if success_first else 1)
    assert (status, reason) == ("mixed" if success_first else "rule", failure)
    assert pdf == (b'%PDF-{"1": "other"}\n%%EOF' if success_first else b"%PDF-{}\n%%EOF")


@pytest.mark.parametrize("success_first", [False, True])
def test_rate_limit_stops_render_and_preserves_prior_choices(provider_api, success_first, workdirs):
    def answer(body):
        if success_first and len(provider_api.calls) == 1:
            options = json.loads(body["messages"][-1]["content"])
            pick = min(options, key=lambda key: options[key][0]["end_glyph"])
            return 200, {}, provider_reply("chat", json.dumps({"id": pick}))
        return 429, {"Retry-After": "60"}, b"private upstream error"

    provider_api.respond = answer
    raw = {"provider": "chat", "base_url": provider_api.base, "model": "fixture"}
    with TestClient(server.app, base_url="http://localhost") as client:
        reply = client.post("/api/render", json={
            "source": "풀잎 사이 Dew 은빛 이슬\n\n" * 4, "style": STYLE, "connection": raw})
        assert reply.status_code == 200
        assert len(provider_api.calls) == (2 if success_first else 1)
        assert reply.headers["X-Jjokgyeol-Decision"] == ("mixed" if success_first else "rule")
        assert reply.headers["X-Jjokgyeol-Decision-Reason"] == "rate_limit"
        expected, = workdirs.glob("*/replay/book.pdf" if success_first else "*/measure/book.pdf")
        assert reply.content == expected.read_bytes()
        check = client.post("/api/decision/check", json=raw)
        assert check.status_code == 429 and check.json()["code"] == "rate_limit"
        assert b"private upstream error" not in check.content


def test_remote_http_rejects_keyed_render_and_check_before_calling_provider(provider_api, monkeypatch):
    monkeypatch.setenv("JJOKGYEOL_ALLOWED_HOSTS", "editor.test:9000")
    raw = {"provider": "chat", "base_url": provider_api.base, "model": "layout", "api_key": "session-marker"}
    provider_api.respond = lambda body: (401, {}, b"private")
    with TestClient(server.app, base_url="http://editor.test:9000") as client:
        for route, payload in (("/api/render", {"source": SOURCE, "style": STYLE, "connection": raw}),
                               ("/api/decision/check", raw)):
            reply = client.post(route, json=payload, headers={"X-Forwarded-Proto": "https"})
            assert reply.status_code == 422
            assert reply.json()["code"] == "address" and reply.json()["field"] == "connection"
            assert b"session-marker" not in reply.content
    assert not provider_api.calls


@pytest.mark.parametrize(("origin", "key"), [("https://editor.test", "session-marker"),
    ("http://editor.test", ""), ("http://localhost", "session-marker"),
    ("http://127.0.0.2", "session-marker"), ("http://[::1]", "session-marker")])
def test_editor_transport_uses_asgi_origin_and_allows_keyless_requests(provider_api, monkeypatch, origin, key):
    monkeypatch.setenv("JJOKGYEOL_ALLOWED_HOSTS", "editor.test,127.0.0.2")
    raw = {"provider": "chat", "base_url": provider_api.base, "model": "layout", "api_key": key}

    def answer(body):
        options = json.loads(body["messages"][-1]["content"])
        return 200, {}, provider_reply("chat", json.dumps({"id": next(iter(options))}))

    provider_api.respond = answer
    with TestClient(server.app, base_url=origin) as client:
        assert client.post("/api/decision/check", json=raw).json() == {"ok": True}
    assert len(provider_api.calls) == 1


def test_remote_http_preserves_keyless_model_legacy_and_rule_renders(provider_api, monkeypatch):
    monkeypatch.setenv("JJOKGYEOL_ALLOWED_HOSTS", "editor.test")
    monkeypatch.delenv("JJOKGYEOL_DECISION_HOST", raising=False)

    def answer(body):
        options = json.loads(body["messages"][-1]["content"])
        return 200, {}, provider_reply("chat", json.dumps({"id": next(iter(options))}))

    provider_api.respond = answer
    model = {"provider": "chat", "base_url": provider_api.base, "model": "layout"}
    with TestClient(server.app, base_url="http://editor.test") as client:
        for fields, expected in [({}, "rule"), ({"connection": {"provider": "rule"}}, "rule"),
                                 ({"connection": model}, "model")]:
            response = client.post("/api/render", json={"source": SOURCE, "style": STYLE, **fields})
            assert response.status_code == 200 and response.content.startswith(b"%PDF-")
            assert response.headers["X-Jjokgyeol-Decision"] == expected
    assert len(provider_api.calls) == 1


def test_legacy_file_worker_preserves_plain_decision_status(tmp_path):
    (tmp_path / "request.json").write_text(json.dumps({"source": SOURCE, "style": STYLE}))
    subprocess.run(jobs.WORKER_CMD, cwd=tmp_path, check=True)
    assert (tmp_path / "decision-status").read_text("ascii") == "rule"
