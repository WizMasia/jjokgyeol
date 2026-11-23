"""In-process layout decisions through the same web and native worker contract."""
import socket
import sys
from importlib.machinery import ModuleSpec
from types import ModuleType
from time import monotonic

import app.ai
import pytest
from fastapi.testclient import TestClient

from app import server
from app.ai import decision, providers
from app.document import parse_md
from app.style import Style, apply_style
from test_decision import SOURCE, STYLE, scan

BUILTIN = {"provider": "builtin"}


@pytest.mark.parametrize("desktop", [False, True])
def test_missing_builtin_is_reported_without_trying_operator_or_remote(monkeypatch, desktop):
    monkeypatch.setenv("JJOKGYEOL_DECISION_HOST", "127.0.0.1")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a: pytest.fail("unexpected DNS lookup"))
    model, reason = providers.parse_render_connection(BUILTIN, desktop=desktop)
    assert model is None and reason == "builtin_unavailable"
    with pytest.raises(providers.ProviderError, match="builtin_unavailable"):
        providers.check_connection(BUILTIN, desktop=desktop)
    for field in ("model", "api_key", "base_url", "path"):
        with pytest.raises(providers.ProviderError, match="invalid_connection"):
            providers.check_connection({**BUILTIN, field: "private"}, desktop=desktop)


@pytest.fixture
def bundled_module(monkeypatch):
    name = "app.ai.trained_decision"
    module = ModuleType(name)
    module.__spec__ = ModuleSpec(name, loader=None)
    monkeypatch.setitem(sys.modules, name, module)
    return module


def test_builtin_inference_and_pdf_replay_without_network(tmp_path, monkeypatch, bundled_module):
    def short_first_line(options, *, timeout):
        assert 0 < timeout <= decision.PIN["decision_seconds"]
        for lines in options.values():
            assert {"end_glyph", "tracking_delta", "split_word", "first_char_class"} <= lines[0].keys()
        return min(options, key=lambda key: options[key][0]["end_glyph"])

    bundled_module.predict = short_first_line
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a: pytest.fail("unexpected DNS lookup"))
    monkeypatch.setattr(socket.socket, "connect", lambda *a: pytest.fail("unexpected socket connection"))
    providers.check_connection(BUILTIN, desktop=True)
    model, reason = providers.parse_render_connection(BUILTIN)
    assert reason is None
    pdf, status, reason = decision.render_provider_choice(parse_md(SOURCE), apply_style(Style(), STYLE),
                                                          tmp_path, monotonic() + 30, model)
    assert (status, reason) == ("model", None)
    rule, notes = scan((tmp_path / "measure/book.pdf").read_bytes())
    selected, selected_notes = scan(pdf)
    assert (rule, selected) == ([9, 11], [7, 11])
    assert notes == selected_notes


@pytest.mark.parametrize("answer,code", [("unknown", "invalid_response"), (RuntimeError("private"), "invalid_response"),
                                         (FileNotFoundError("weights"), "builtin_unavailable"),
                                         (TimeoutError(), "timeout")])
def test_builtin_check_validates_selection_and_contains_model_errors(bundled_module, answer, code):
    def predict(options, *, timeout):
        if isinstance(answer, Exception):
            raise answer
        return answer

    bundled_module.predict = predict
    with pytest.raises(providers.ProviderError, match=f"^{code}$"):
        providers.check_connection(BUILTIN)


def test_web_builtin_without_weights_delivers_rule_pdf(monkeypatch):
    monkeypatch.setenv("JJOKGYEOL_DECISION_HOST", "127.0.0.1")
    with TestClient(server.app, base_url="http://127.0.0.1") as client:
        checked = client.post("/api/decision/check", json=BUILTIN)
        assert checked.json()["code"] == "builtin_unavailable"
        response = client.post("/api/render", json={"source": SOURCE, "style": STYLE, "connection": BUILTIN})
    assert response.status_code == 200
    assert response.headers["X-Jjokgyeol-Decision"] == "rule"
    assert response.headers["X-Jjokgyeol-Decision-Reason"] == "builtin_unavailable"
    assert scan(response.content)[0] == [9, 11]


def test_bundled_module_loads_after_baseline_and_inside_time_budget(tmp_path, monkeypatch):
    marker = tmp_path / "module-loaded"
    module = tmp_path / "trained_decision.py"
    module.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
                      "def predict(options, *, timeout):\n    return next(iter(options))\n")
    monkeypatch.setattr(app.ai, "__path__", [str(tmp_path), *app.ai.__path__])
    try:
        model, reason = providers.parse_render_connection(BUILTIN)
        assert model is not None and reason is None
        assert not marker.exists()
        assert model.predict(providers.EXAMPLE, timeout=1) in providers.EXAMPLE
        assert marker.exists()
    finally:
        sys.modules.pop("app.ai.trained_decision", None)


def test_builtin_cannot_add_a_candidate_to_its_own_probe(bundled_module):
    def predict(options, *, timeout):
        options["a"][0].clear()
        options["invented"] = []
        return "invented"

    bundled_module.predict = predict
    with pytest.raises(providers.ProviderError, match="invalid_response"):
        providers.check_connection(BUILTIN)
    assert set(providers.EXAMPLE) == {"a", "b"}
    assert "end_glyph" in providers.EXAMPLE["a"][0]
