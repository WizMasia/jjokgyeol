"""T13. Offline contract, measured PDF replay and opt-in local acceptance."""
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
import os
from pathlib import Path
import time

import pytest

from app.ai import decision
from app.document import parse_md
from app.style import Style, apply_style
from app.typeset import typeset
from pdf_metrics import read_pages


@contextmanager
def model_server(answer):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.reply(answer(self.path, None))

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.reply(answer(self.path, payload))

        def reply(self, value):
            data = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except BrokenPipeError:
                pass

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_port
        finally:
            server.shutdown()
            thread.join()


@pytest.fixture
def installed(tmp_path, monkeypatch):
    key = "google/gemma-4-e2b"
    (tmp_path / "model.safetensors").write_bytes(b"offline test weight")
    monkeypatch.setattr(decision, "MODEL_DIR", tmp_path)
    monkeypatch.setitem(decision.PIN, "files", {
        "model.safetensors": hashlib.sha256(b"offline test weight").hexdigest()})
    monkeypatch.setenv("JJOKGYEOL_DECISION_HOST", "127.0.0.1")
    inventory = {"models": [{"key": key, "selected_variant": key + "@4bit",
                             "loaded_instances": [{"id": key}]}]}
    result = {"model": key, "runtime": dict(decision.PIN["runtime"]),
              "model_info": {"arch": "gemma4", "quant": "4bit", "format": "mlx"},
              "choices": [{"finish_reason": "stop", "message": {"content": '{"id":"other"}'}}]}
    return inventory, result


def candidates():
    line = {"end_glyph": 6, "first_char": ord("원"), "last_char": ord("고"), "start_sp": 0,
            "available_sp": 90000, "remaining_sp": 10000, "tracking_per_10000": 0,
            "space_per_10000": 10000, "tracking_delta": 0, "space_deviation": 0,
            "tracking_change": 0, "space_change": 0, "split_word": False,
            "private": "do not forward"}
    return [{"id": key, "lines": [dict(line)]} for key in ("other", "rule", "invalid")]


@pytest.mark.parametrize("field,value", [
    ("remaining_sp", -1), ("space_per_10000", 9999), ("tracking_per_10000", 1),
    ("end_glyph", 5), ("split_word", "do not forward")])
def test_only_legal_numeric_features_reach_model(installed, monkeypatch, field, value):
    inventory, result = installed
    sent = []
    def answer(path, data):
        if path == "/api/v1/models":
            return inventory
        assert path == "/api/v0/chat/completions"
        sent.append(json.dumps(data, ensure_ascii=False))
        return result

    with model_server(answer) as port:
        monkeypatch.setenv("JJOKGYEOL_DECISION_PORT", str(port))
        model = decision.LocalModel.verified()
        rows = candidates()
        rows[-1]["lines"][0][field] = value
        choice = decision.ParagraphDecision(Style(), "rule", model)
        assert choice.choose(rows) == "other" and choice.used_model
        payload, = sent
        assert all(text not in payload for text in ("invalid", "do not forward", "원", "고",
                                                    str(ord("원")), str(ord("고"))))


@pytest.mark.parametrize("problem", ["disabled", "remote", "missing", "hash", "variant", "unloaded",
                                     "runtime", "model", "stale", "malformed", "extra", "duplicate",
                                     "truncated", "unavailable"])
def test_fallback_uses_row_rule_even_when_listed_second(installed, monkeypatch, problem):
    inventory, result = installed
    if problem == "missing":
        (decision.MODEL_DIR / "model.safetensors").unlink()
    if problem == "hash":
        (decision.MODEL_DIR / "model.safetensors").write_bytes(b"different weight")
    if problem == "variant":
        inventory["models"][0]["selected_variant"] = "different"
    if problem == "unloaded":
        inventory["models"][0]["loaded_instances"] = []
    if problem == "model":
        result["model"] += "-wrong"
    if problem == "runtime":
        result["runtime"]["version"] = "unknown"
    if problem in ("malformed", "stale"):
        result["choices"][0]["message"]["content"] = ("garbage" if problem == "malformed"
                                                     else '{"id":"obsolete"}')
    if problem in ("extra", "duplicate"):
        result["choices"][0]["message"]["content"] = (
            '{"id":"other","unexpected":true}' if problem == "extra" else '{"id":"rule","id":"other"}')
    if problem == "truncated":
        result["choices"][0]["finish_reason"] = "length"
    seen = []
    def answer(path, data):
        seen.append(path)
        return result if data else inventory
    with model_server(answer) as port:
        monkeypatch.setenv("JJOKGYEOL_DECISION_PORT", str(port))
        if problem == "disabled":
            monkeypatch.delenv("JJOKGYEOL_DECISION_HOST")
        if problem == "remote":
            monkeypatch.setenv("JJOKGYEOL_DECISION_HOST", "example.com")
        model = decision.LocalModel.verified()
        if problem != "unavailable":
            choice = decision.ParagraphDecision(Style(), "rule", model)
            assert choice.choose(candidates()) == "rule" and not choice.used_model
    if problem == "unavailable":
        assert model is not None
        choice = decision.ParagraphDecision(Style(), "rule", model)
        assert choice.choose(candidates()) == "rule" and not choice.used_model
    if problem in ("disabled", "remote", "missing", "hash"):
        assert not seen
    if problem in ("variant", "unloaded"):
        assert "/api/v0/chat/completions" not in seen


SOURCE = (Path(__file__).with_name("fixtures") / "decision-dew.md").read_text("utf-8")
STYLE = {"page_mm": [58, 100], "margins_mm": [10] * 4, "ko_tracking_pct": -3,
         "space_pct": [100, 100], "body_pt": 11, "leading_pt": 17}


def scan(pdf):
    page, = read_pages(pdf)
    words = ["풀잎", "사이", "Dew", "은빛", "이슬"]
    body, note, markers = [], [], []
    for line in page.lines:
        chars = [c for c in line.chars if not c.text.isspace()]
        run = [c for c in chars if c.size == pytest.approx(11 * 72 / 72.27, abs=.02)]
        if run:
            body.append(run)
        for c in chars:
            if c.text == "1":
                markers.append(c)
            elif c not in run:
                note.append(c)
    assert "".join(c.text for row in body for c in row) == "".join(words)
    assert "".join(c.text for c in note) == "아침수집함."
    assert len(markers) == 2 and min(c.baseline for c in body[-1]) > note[0].baseline
    ref = max(markers, key=lambda c: c.baseline)
    assert body[-1][-1].baseline < ref.baseline < body[-1][-1].baseline + 11
    assert abs(ref.x0 - body[-1][-1].x0 - body[-1][-1].advance) < 1
    ends, index = [], 0
    boundaries = {2, 4, 7, 9}
    for row in body:
        for left, right in zip(row, row[1:]):
            index += 1
            gap = right.x0 - left.x0 - left.advance
            if index in boundaries:
                assert gap == pytest.approx(left.size * 307 / 1024, abs=.04)
            elif "가" <= left.text <= "힣" and "가" <= right.text <= "힣":
                assert gap == pytest.approx(-.03 * left.size, abs=.04)
        index += 1
        ends.append(index)
    return ends, [(c.text, c.x0, c.baseline) for c in note]


def test_choice_reaches_pdf(installed, monkeypatch, tmp_path):
    doc, style = parse_md(SOURCE), apply_style(Style(), STYLE)
    measure = tmp_path / "measure"
    measure.mkdir()
    typeset(doc, style, measure, collect_candidates=True)
    row, = map(json.loads, (measure / "paragraph-candidates.jsonl").read_text().splitlines())
    options = row["candidates"]
    assert len(options) >= 2
    inventory, result = installed
    def answer(path, data):
        return result if data else inventory

    with model_server(answer) as port:
        monkeypatch.setenv("JJOKGYEOL_DECISION_PORT", str(port))
        prints, pdfs = [], []
        for index, candidate in enumerate(options[:2]):
            result["choices"][0]["message"]["content"] = json.dumps({"id": candidate["id"]})
            job = tmp_path / str(index)
            job.mkdir()
            pdf, status = decision.render_choice(doc, style, job, time.monotonic() + 25)
            assert status == "model"
            printed, note = scan(pdf)
            assert printed == [line["end_glyph"] for line in candidate["lines"]]
            pdfs.append(pdf)
            prints.append((printed, note))
    assert pdfs[0] != pdfs[1] and prints[0][0] != prints[1][0]
    assert prints[0][1] == prints[1][1]


def test_job_deadline_returns_measured_rule_pdf(installed, monkeypatch, tmp_path):
    source = "풀잎 사이 Dew 은빛 이슬\n\n" * 3
    doc, style = parse_md(source), apply_style(Style(), STYLE)
    inventory, result = installed
    calls = []
    def answer(path, data):
        if data:
            calls.append(time.monotonic())
            options = json.loads(data["messages"][-1]["content"])
            result["choices"][0]["message"]["content"] = json.dumps({"id": next(iter(options))})
            time.sleep(.45)
            return result
        return inventory
    with model_server(answer) as port:
        monkeypatch.setenv("JJOKGYEOL_DECISION_PORT", str(port))
        monkeypatch.setitem(decision.PIN, "decision_seconds", .8)
        pdf, status = decision.render_choice(doc, style, tmp_path, time.monotonic() + 25)
    assert len(calls) == 2
    assert status == "rule" and pdf == (tmp_path / "measure/book.pdf").read_bytes()


@pytest.mark.skipif(not os.getenv("JJOKGYEOL_DECISION_TEST_URL"), reason="explicit local deployment acceptance")
def test_real_local_model():
    import httpx
    url = os.environ["JJOKGYEOL_DECISION_TEST_URL"]
    endpoint = httpx.URL(url)
    assert endpoint.scheme == "http" and endpoint.host == "127.0.0.1"
    with httpx.Client(base_url=url, trust_env=False, timeout=35) as client:
        pair = []
        for mode in ("rule", "model"):
            r = client.post("/api/render", json={"source": SOURCE, "style": STYLE, "decision": mode})
            r.raise_for_status()
            assert r.headers["X-Jjokgyeol-Decision"] == mode
            pair.append(scan(r.content))
    assert pair[0][0] != pair[1][0]
    assert pair[0][1] == pair[1][1]
