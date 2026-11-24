"""Spacing follows the delivered PDF, including a supervisor-recovered baseline."""
from email import policy
from email.parser import BytesParser
import json
import sys

from fastapi.testclient import TestClient
import pytest

from app import jobs, server
from test_font_transport import frame


RULE = b"%PDF-rule\n%%EOF"
MODEL = b"%PDF-selected\n%%EOF"
ROW = {"before_line": 0, "after_line": 2, "gap_sp": 0, "source": "rule"}
SELECTED = {**ROW, "gap_sp": 360448, "source": "ai"}
RULE_LEADING = {"line": 7, "leading_sp": 1179648, "source": "manual", "variable": False}
MODEL_LEADING = {"line": 7, "leading_sp": 1245184, "source": "ai", "variable": False}
REPLAY_LEADING = {"line": 9, "leading_sp": 1310720, "source": "rule", "variable": False}
VARIABLE_LEADING = {"line": 7, "leading_sp": None, "source": "manual", "variable": True}
HEADERS = {"Accept": "multipart/form-data", "X-Jjokgyeol-Spacing": "1"}


@pytest.fixture
def api(tmp_path, monkeypatch):
    # Model the worker's committed files; the real supervisor selects and removes them.
    worker = tmp_path / "worker.py"
    worker.write_text(f"""
import json, os, sys
from pathlib import Path
raw = sys.stdin.buffer.read()
if raw[:1] == b'\\0':
    assert '--local-font-frame' in sys.argv
    raw = json.loads(raw[4:])['request']
else:
    raw = json.loads(raw)
case = raw['source']
def record(path, row):
    (path / 'spacing.json').write_text(json.dumps([dict(row, valid=True, private='PRIVATE-TEXT')]))
def record_leading(path, row):
    (path / 'leading.json').write_text(json.dumps([dict(row, valid=True, lines=3, kind='paragraph',
        level=0, font_sp=720896, remaining_sp=200000, auto=False, private='PRIVATE-TEXT')]))
root = Path.cwd()
if case != 'plain':
    measure = root / 'measure'
    measure.mkdir()
    (measure / 'book.pdf').write_bytes({RULE!r})
    record(measure, {ROW!r})
record(root, {ROW!r} if case == 'plain' else {SELECTED!r})
if '--spacing-receipt' in sys.argv and case != 'no-leading':
    if case != 'plain':
        record_leading(measure, {RULE_LEADING!r})
    record_leading(root, {MODEL_LEADING!r} if case == 'paired' else {RULE_LEADING!r})
if case == 'replay-crash':
    replay = root / 'replay'
    replay.mkdir()
    (replay / 'book.pdf').write_bytes({MODEL!r})
    record(replay, dict({SELECTED!r}, source='rule'))
    if '--spacing-receipt' in sys.argv:
        record_leading(replay, {REPLAY_LEADING!r})
        provisional = root / 'spacing'
        provisional.mkdir()
        record_leading(provisional, dict({MODEL_LEADING!r}, valid=False, leading_sp='PRIVATE'))
    (root / 'line-replay-ready').touch()
if case.endswith('crash'):
    Path('baseline-ready').write_text(sys.argv[1])
    os._exit(1)
Path('response.pdf').write_bytes({MODEL!r} if case == 'paired' else {RULE!r})
Path('decision-status').write_text(json.dumps(['mixed' if case == 'paired' else 'rule', None]))
""")
    monkeypatch.setattr(jobs, "WORKER_CMD", (sys.executable, "-IB", str(worker)))
    with TestClient(server.app, base_url="http://127.0.0.1") as client:
        yield client


def parts(response):
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("multipart/form-data")
    packet = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {response.headers['content-type']}\r\n\r\n".encode() + response.content)
    return {part.get_param("name", header="Content-Disposition"): part.get_payload(decode=True)
            for part in packet.iter_parts()}


@pytest.mark.parametrize("font", [False, True])
@pytest.mark.parametrize("case", ["plain", "paired", "crash", "measured-rule"])
def test_spacing_part_matches_each_delivered_pdf(api, case, font):
    request = {"source": case}
    if font:
        response = api.post("/api/render", headers={**HEADERS, "Content-Type": server.FONT_MEDIA_TYPE},
                            content=frame(request, []))
    else:
        response = api.post("/api/render", headers=HEADERS, json=request)
    fields = parts(response)
    report = json.loads(fields.pop("spacing"))
    assert fields == ({"rule": RULE, "model": MODEL} if case == "paired" else {"rule": RULE})
    expected = {"rule": [ROW], "model": [SELECTED]} if case == "paired" else {"rule": [ROW]}
    expected["leading"] = ({"rule": [RULE_LEADING], "model": [MODEL_LEADING]}
                           if case == "paired" else {"rule": [RULE_LEADING]})
    assert report == expected
    assert not any("spacing" in key for key in response.headers)
    assert response.headers["cache-control"] == "no-store"


def test_existing_clients_keep_pdf_and_pair_bytes(api):
    single = api.post("/api/render", headers={"Accept": "multipart/form-data"}, json={"source": "plain"})
    assert single.headers["content-type"] == "application/pdf" and single.content == RULE
    pair = api.post("/api/render", headers={"Accept": "multipart/form-data"}, json={"source": "paired"})
    assert parts(pair) == {"rule": RULE, "model": MODEL}


def test_spacing_receipt_without_leading_file_keeps_legacy_shape(api):
    response = api.post("/api/render", headers=HEADERS, json={"source": "no-leading"})
    assert json.loads(parts(response)["spacing"]) == {"rule": [ROW]}


def test_hard_stop_after_committed_line_replay_recovers_that_pdf_and_receipt(api):
    response = api.post("/api/render", headers=HEADERS, json={"source": "replay-crash"})
    assert response.headers["X-Jjokgyeol-Decision"] == "mixed"
    assert response.headers["X-Jjokgyeol-Decision-Reason"] == "invalid_response"
    fields = parts(response)
    assert json.loads(fields.pop("spacing")) == {
        "rule": [ROW], "model": [dict(SELECTED, source="rule")],
        "leading": {"rule": [RULE_LEADING], "model": [REPLAY_LEADING]},
    }
    assert fields == {"rule": RULE, "model": MODEL}
    old = api.post("/api/render", json={"source": "replay-crash"})
    assert old.content == MODEL and old.headers["content-type"] == "application/pdf"


def test_invalid_or_oversized_receipt_is_optional(tmp_path):
    from app.jobs import read_leading, read_spacing
    file = tmp_path / "spacing.json"
    assert read_spacing(tmp_path) is None
    assert read_leading(tmp_path) is None
    for raw in (b"[" * 1600, b"[]" + b" " * jobs.LIMITS.request_body_bytes, b"{}"):
        file.write_bytes(raw)
        assert read_spacing(tmp_path) is None
    leading_file = tmp_path / "leading.json"
    for raw in (b"[" * 1600, b"[]" + b" " * jobs.LIMITS.request_body_bytes, b"{}"):
        leading_file.write_bytes(raw)
        assert read_leading(tmp_path) is None
    file.write_text(json.dumps([dict(ROW, valid=False), dict(ROW, valid=True, gap_sp=True),
                                dict(ROW, valid=True, source="private"),
                                dict(ROW, valid=True, before_line=-1),
                                dict(SELECTED, valid=True, text="private")]))
    assert read_spacing(tmp_path) == [SELECTED]
    leading_file.write_text(json.dumps([
        dict(RULE_LEADING, valid=True, lines=3),
        dict(VARIABLE_LEADING, valid=False, lines=3),
        dict(RULE_LEADING, valid=False, lines=3),
        dict(RULE_LEADING, valid=True, lines=1),
        dict(RULE_LEADING, valid=True, lines=3, leading_sp=1 << 53),
        dict(RULE_LEADING, valid=True, lines=3, source="private"),
    ]))
    assert read_leading(tmp_path) == [RULE_LEADING, VARIABLE_LEADING]


def test_boundary_receipt_marks_only_a_measured_page_break(tmp_path):
    from app.jobs import read_spacing

    (tmp_path / "spacing.json").write_text(json.dumps([
        dict(ROW, valid=True, left_page=1, page=2),
        dict(ROW, valid=True, gap_sp=0, left_page=1, page=1),
        dict(ROW, valid=True, gap_sp=4 * 65536, left_page=1, page=2),
    ]))
    rows = read_spacing(tmp_path)
    assert rows == [dict(ROW, page_break=True), ROW, dict(ROW, gap_sp=4 * 65536)]


def test_unstyled_preview_reports_the_existing_gap_and_keeps_pdf_geometry():
    from pdf_metrics import read_pages
    request = {"source": "가을\n\n겨울", "connection": {"provider": "rule"}}
    with TestClient(server.app, base_url="http://127.0.0.1") as client:
        legacy = client.post("/api/render", json=request)
        actual = parts(client.post("/api/render", json=request, headers=HEADERS))
    assert json.loads(actual["spacing"])["rule"] == [
        {"before_line": 0, "after_line": 2, "gap_sp": 4 * 65536, "source": "default"}]
    assert read_pages(actual["rule"]) == read_pages(legacy.content)


def test_spacing_opt_in_reports_manual_leading_from_real_web_worker():
    source = "<!-- 쪽결 leading_sp=1179648 -->\n" + "여러 글줄의 기준선을 측정할 문단입니다 " * 12
    request = {"source": source, "connection": {"provider": "rule"}}
    with TestClient(server.app, base_url="http://127.0.0.1") as client:
        response = client.post("/api/render", headers=HEADERS, json=request)
    actual = parts(response)
    receipt = json.loads(actual["spacing"])
    assert receipt["leading"]["rule"] == [
        {"line": 1, "leading_sp": 1179648, "source": "manual", "variable": False}]
    assert actual["rule"].startswith(b"%PDF-")
