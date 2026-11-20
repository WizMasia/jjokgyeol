"""T13. Offline contract, measured PDF replay and opt-in local acceptance."""
from contextlib import contextmanager
from email import policy
from email.parser import BytesParser
from functools import partial
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
import os
import shutil
import sys
from tempfile import TemporaryDirectory
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


def scan(pdf, indent=""):
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
    # NanumMyeongjo advances U+3000 by 973/1024 em, including on replay.
    first = body[0][0]
    assert first.x0 == pytest.approx(10 * 72 / 25.4 + len(indent) * first.size * 973 / 1024, abs=.05)
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


@pytest.mark.parametrize("indent", ["", "\u3000"], ids=["plain", "ideographic"])
def test_choice_reaches_pdf(installed, monkeypatch, tmp_path, indent):
    doc, style = parse_md(indent + SOURCE), apply_style(Style(), STYLE)
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
            printed, note = scan(pdf, indent)
            assert printed == [line["end_glyph"] for line in candidate["lines"]]
            assert candidate["lines"][0]["first_char"] == ord("풀")
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


@pytest.fixture
def measured_jobs(installed, monkeypatch, tmp_path):
    from app import jobs

    # Use the real worker and replay, with the offline model pin available in its process.
    launcher = (
        f"import runpy, sys; sys.path.insert(0, {str(Path(jobs.__file__).parents[1])!r}); "
        f"from pathlib import Path; from app.ai import decision; decision.MODEL_DIR = Path({str(tmp_path)!r}); "
        f"decision.PIN['files'] = {decision.PIN['files']!r}; "
        f"runpy.run_path({jobs.__file__!r}, run_name='__main__')"
    )
    monkeypatch.setattr(jobs, "WORKER_CMD", (sys.executable, "-IB", "-c", launcher))
    scratch = tmp_path / "jobs"
    scratch.mkdir()
    monkeypatch.setattr(jobs, "TemporaryDirectory", partial(TemporaryDirectory, dir=scratch, delete=False))
    return scratch


@pytest.mark.parametrize("expected", ["model", "mixed", "rule"])
def test_compare_returns_pdfs_from_one_measured_job(installed, measured_jobs, monkeypatch, expected):
    from fastapi.testclient import TestClient
    from app.server import app

    inventory, result = installed
    calls = 0

    def answer(path, data):
        nonlocal calls
        if data is None:
            return inventory
        calls += 1
        options = json.loads(data["messages"][-1]["content"])
        selected = min(options, key=lambda k: options[k][0]["end_glyph"])
        if expected == "rule" or (expected == "mixed" and calls == 2):
            selected = "unknown"
        result["choices"][0]["message"]["content"] = json.dumps({"id": selected})
        return result

    with model_server(answer) as port, TestClient(app) as client:
        monkeypatch.setenv("JJOKGYEOL_DECISION_PORT", str(port))
        response = client.post("/api/render", headers={"Accept": "multipart/form-data"},
                               json={"source": "풀잎 사이 Dew 은빛 이슬\n\n" * 2, "style": STYLE})
    assert response.status_code == 200
    assert response.headers["X-Jjokgyeol-Decision"] == expected
    assert calls == 2
    assert response.headers["Cache-Control"] == "no-store"
    job, = measured_jobs.iterdir()
    passes = {path.parent.name for path in job.rglob("book.pdf")}
    assert passes == ({"measure"} if expected == "rule" else {"measure", "replay"})
    baseline = (job / "measure/book.pdf").read_bytes()
    if expected == "rule":
        assert response.headers["Content-Type"] == "application/pdf"
        assert response.content == baseline
        return
    content_type = response.headers["Content-Type"]
    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {content_type}\r\n\r\n".encode() + response.content)
    assert message.get_content_type() == "multipart/form-data"
    parts = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="Content-Disposition")
        assert name not in parts and part.get_filename() == f"{name}.pdf"
        assert part.get_content_disposition() == "form-data"
        assert part.get_content_type() == "application/pdf"
        parts[name] = part.get_payload(decode=True)
    assert parts == {"rule": baseline, "model": (job / "replay/book.pdf").read_bytes()}
    assert parts["rule"] != parts["model"]


@pytest.mark.parametrize("mode", [None, "model"])
def test_single_pdf_with_connected_model_requires_explicit_choice(installed, measured_jobs, monkeypatch, mode):
    from fastapi.testclient import TestClient
    from app.server import app

    inventory, result = installed

    def answer(path, data):
        if data is None:
            return inventory
        options = json.loads(data["messages"][-1]["content"])
        result["choices"][0]["message"]["content"] = json.dumps({"id": next(iter(options))})
        return result

    payload = {"source": SOURCE, "style": STYLE}
    if mode is not None:
        payload["decision"] = mode
    with model_server(answer) as port, TestClient(app) as client:
        monkeypatch.setenv("JJOKGYEOL_DECISION_PORT", str(port))
        response = client.post("/api/render", json=payload)
    assert response.status_code == 200 and response.headers["Content-Type"] == "application/pdf"
    assert response.headers["X-Jjokgyeol-Decision"] == (mode or "rule")
    job, = measured_jobs.iterdir()
    assert response.content == (job / ("replay/book.pdf" if mode else "book.pdf")).read_bytes()


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


@pytest.mark.parametrize("mode", [None, "rule", "model"])
def test_only_explicit_evaluation_requests_collect_measured_rows(tmp_path, monkeypatch, mode):
    import subprocess
    from app.jobs import WORKER_CMD

    monkeypatch.delenv("JJOKGYEOL_DECISION_HOST", raising=False)
    payload = {"source": SOURCE, "style": STYLE}
    if mode is not None:
        payload["decision"] = mode
    (tmp_path / "request.json").write_text(json.dumps(payload))
    subprocess.run(WORKER_CMD, cwd=tmp_path, check=True)
    trace = tmp_path / "paragraph-candidates.jsonl"
    assert trace.exists() == (mode is not None)
    if trace.exists():
        row, = map(json.loads, trace.read_text().splitlines())
        assert row["selected_id"] == row["rule_id"]
        ends, _ = scan((tmp_path / "response.pdf").read_bytes())
        assert ends == next([line["end_glyph"] for line in c["lines"]]
                            for c in row["candidates"] if c["id"] == row["rule_id"])


@pytest.mark.parametrize(("expired", "stage"), [
    ((2,), "replay"), ((2,), "measurement"), ((1, 2), "measurement"),
    ((2,), "reply"), ((1, 2), "reply"),
])
def test_replay_expiry_returns_verified_paragraphs(installed, monkeypatch, tmp_path, expired, stage):
    source = "풀잎 사이 Dew 은빛 이슬\n\n" * 2
    doc, style = parse_md(source), apply_style(Style(), STYLE)
    inventory, result = installed

    replies = 0

    def answer(path, data):
        nonlocal replies
        if data is None:
            return inventory
        options = json.loads(data["messages"][-1]["content"])
        replies += 1
        pick = min(options, key=lambda key: options[key][0]["end_glyph"])
        if stage == "reply" and replies in expired:
            pick = "unknown-model-response"
        result["choices"][0]["message"]["content"] = json.dumps({"id": pick})
        return result

    def replay_expired(doc, style, folder, **kwargs):
        if stage == "replay" and "paragraph_choices" in kwargs:
            kwargs["paragraph_choices"] = {**kwargs["paragraph_choices"],
                                            **dict.fromkeys(expired, "expired-at-replay")}
        pdf = typeset(doc, style, folder, **kwargs)
        if stage == "measurement" and folder.name == "measure":
            # Give the selector an expired namespace. T11 regenerates the real IDs itself.
            rows = list(decision.measured_rows(folder))
            for row in rows:
                if row["block"] in expired:
                    for candidate in row["candidates"]:
                        candidate["id"] = "expired-" + candidate["id"]
                    row["rule_id"] = row["selected_id"] = "expired-" + row["rule_id"]
            write_rows(folder, rows)
        return pdf

    monkeypatch.setattr(decision, "typeset", replay_expired)
    with model_server(answer) as port:
        monkeypatch.setenv("JJOKGYEOL_DECISION_PORT", str(port))
        pdf, status = decision.render_choice(doc, style, tmp_path, time.monotonic() + 30)

    partial = len(expired) == 1
    assert status == ("mixed" if partial else "rule")
    assert pdf == (tmp_path / ("replay" if partial else "measure") / "book.pdf").read_bytes()
    rows = list(decision.measured_rows(tmp_path / ("replay" if partial else "measure")))
    assert len(rows) == 2
    (page,) = read_pages(pdf)
    printed = ["".join(c.text for c in line.chars if not c.text.isspace()) for line in page.lines]
    for index, row in enumerate(rows):
        is_rule = row["block"] in expired
        assert (row["selected_id"] == row["rule_id"]) == is_rule
        choice, = [c for c in row["candidates"] if c["id"] == row["selected_id"]]
        first, last = printed[2*index:2*index+2]
        assert first + last == "풀잎사이Dew은빛이슬"
        assert [len(first), len(first + last)] == [line["end_glyph"] for line in choice["lines"]]
        assert len(first) == (9 if is_rule else 7)


def write_rows(folder, rows):
    (folder / "paragraph-candidates.jsonl").write_text("\n".join(map(json.dumps, rows)) + "\n")


@pytest.mark.parametrize("fault", [
    "duplicate-block", "missing-block", "extra-block", "wrong-rule", "unknown-selected",
    "wrong-end", "invalid-width", "changed-lines", "duplicate-id", "unrequested-nonrule",
    "bad-pdf", "bad-rule-check", "invalid-expired-measurement",
])
def test_unverifiable_replay_returns_original_pdf(tmp_path, monkeypatch, fault):
    source = "풀잎 사이 Dew 은빛 이슬\n\n" * 2
    doc, style = parse_md(source), apply_style(Style(), STYLE)

    class Model:
        calls = 0

        def predict(self, options):
            self.calls += 1
            if fault == "unrequested-nonrule" and self.calls == 2:
                return "absent"
            return min(options, key=lambda k: options[k][0]["end_glyph"])

    monkeypatch.setattr(decision.LocalModel, "verified", Model)

    def engine(doc, style, folder, **kwargs):
        pdf = typeset(doc, style, folder, **kwargs)
        if fault == "invalid-expired-measurement" and folder.name == "measure":
            rows = list(decision.measured_rows(folder))
            other, = [c for c in rows[-1]["candidates"] if c["id"] != rows[-1]["rule_id"]]
            other.update(id="expired-invalid", lines=[])
            write_rows(folder, rows)
        if folder.name != "replay" or fault == "invalid-expired-measurement":
            return pdf
        if fault == "bad-pdf":
            return pdf[:-16]
        rows = list(decision.measured_rows(folder))
        row = rows[-1]
        other, = [c for c in row["candidates"] if c["id"] != row["rule_id"]]
        if fault == "duplicate-block":
            rows.append(row)
        elif fault == "missing-block":
            rows.pop()
        elif fault == "extra-block":
            rows.append({**row, "block": 3})
        elif fault == "wrong-rule":
            row["rule_id"] = other["id"]
        elif fault == "unknown-selected":
            row["selected_id"] = "absent"
        elif fault == "duplicate-id":
            row["candidates"].append(other)
        elif fault == "unrequested-nonrule":
            row["selected_id"] = other["id"]
        elif fault == "bad-rule-check":
            # Internally consistent, but an independent T11 pass cannot reproduce this catalog.
            other["id"] = "unverified"
            row["selected_id"] = row["rule_id"]
        else:
            key, value = {"wrong-end": ("end_glyph", 0), "invalid-width": ("remaining_sp", -1),
                          "changed-lines": ("remaining_sp", 10)}[fault]
            other["lines"][0][key] = value
        write_rows(folder, rows)
        return pdf

    monkeypatch.setattr(decision, "typeset", engine)
    pdf, status = decision.render_choice(doc, style, tmp_path, time.monotonic() + 30)
    assert (pdf, status) == ((tmp_path / "measure/book.pdf").read_bytes(), "rule")
    if (tmp_path / "replay/book.pdf").exists():
        assert pdf != (tmp_path / "replay/book.pdf").read_bytes()


@pytest.mark.skipif(not os.getenv("JJOKGYEOL_DECISION_TEST_BUNDLE"), reason="requires frozen T13 bundle and T14 protocol")
def test_prior_bundle_candidate_order(tmp_path, monkeypatch):
    from evaluation.model import load_arm
    from evaluation.packet import blind_pdf, corpus, digest, read_json, verify_split

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "packaging"))
    from decision_bundle import audit_pair

    bundle = Path(os.environ["JJOKGYEOL_DECISION_TEST_BUNDLE"])
    manifest = read_json(bundle / "manifest.json")
    pin = decision.PIN
    assert manifest["model_sha256"] == pin["files"]["model.safetensors"]
    assert manifest["runtime"] == f'{pin["variant"]} / {pin["runtime"]["name"]}@{pin["runtime"]["version"]}'
    manuscripts = corpus()
    verify_split(manuscripts, manifest["development"])
    for ms in (ms for ms in manuscripts if ms["split"] == "model"):
        original = bundle / ms["group"]
        arms = [load_arm(original / arm, ms, arm, manifest["model_sha256"]) for arm in ("rules", "model")]
        assert [arm[2]["applied"] for arm in arms] == ["rule", "model"]
        assert arms[0][3] == arms[1][3] and arms[0][4]["choices"] == arms[1][4]["choices"]
        assert blind_pdf(arms[0][0]) == blind_pdf(arms[1][0])
        assert all(not arm[4]["end_mismatches"] and not arm[4]["constraint_violations"] for arm in arms)

        group = tmp_path / ms["group"]
        shutil.copytree(original, group)
        audit_pair(group, ms)
        assert read_json(group / "outcome.json") == read_json(original / "outcome.json")
        folder = group / "model"
        rows = list(decision.measured_rows(folder))
        for row in rows:
            row["candidates"].reverse()
        receipt = read_json(folder / "measurement.json")
        for expected in (True, False):
            if not expected:
                row = rows[0]
                alternative = next(c for c in row["candidates"] if c["id"] != row["selected_id"])
                alternative["lines"][0]["remaining_sp"] += 1
            write_rows(folder, rows)
            log = (folder / "paragraph-candidates.jsonl").read_bytes()
            (folder / "measurement.json").write_text(json.dumps({**receipt, "log_sha256": digest(log)}))
            audit_pair(group, ms)
            assert read_json(group / "outcome.json")["same_candidate_sets"] is expected
