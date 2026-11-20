"""PDF evidence and packet plumbing for the justified reading study."""
import csv
import json
import sys

import pytest

from app.document import parse_md
from app.style import Style, apply_style
from app.typeset import typeset


@pytest.fixture(scope="module")
def measured_text(tmp_path_factory):
    # Two 2-syllable words fit exactly at a known NanumMyeongjo advance.
    size = 11
    width = (size * (4 * 973 + 307) / 1024 + .01) * 25.4 / 72.27
    style = {"body_font": "nanum-myeongjo", "body_pt": size, "leading_pt": 17,
             "page_mm": [36 + width, 210], "margins_mm": [20, 18, 20, 18],
             "space_pct": [70, 130], "ko_tracking_pct": 0, "ko_tracking_bounds_pct": [-7, 15],
             "paragraph_layout": "justified"}
    src = "봄날 봄날 봄날\n\n여름"
    fixed = {k: v for k, v in style.items() if k not in {"paragraph_layout", "ko_tracking_bounds_pct"}}
    pdf = typeset(parse_md(src), apply_style(Style(), {**fixed, "space_pct": [100, 100]}),
                  tmp_path_factory.mktemp("width"))
    return pdf, src, style


def test_measure_justified_lines_and_natural_finals(measured_text):
    from evaluation.packet import verify_justified_pdf

    report = verify_justified_pdf(*measured_text)
    assert report["line_ends"] == [4, 6, 8]
    assert report["nonfinal_lines"] == 1
    assert report["max_nonfinal_right_error_pt"] <= .05
    assert report["max_left_error_pt"] <= .05


@pytest.mark.parametrize(("change", "message"), [
    ("ragged", "right edge"), ("left", "left edge"), ("split word", "word boundary"),
    ("joined paragraphs", "paragraph boundary"), ("changed text", "source"),
    ("missing space", "space|tracking"), ("wrong face", "font"),
    ("overfull final", "right edge"),
])
def test_measure_rejects_bad_print(measured_text, change, message):
    from evaluation.packet import verify_justified_pdf

    pdf, src, style = measured_text
    if change == "ragged":
        style = {**style, "margins_mm": [20, 17, 20, 18]}
    elif change == "left":
        style = {**style, "margins_mm": [20, 18, 20, 17]}
    elif change == "split word":
        src = "봄날 봄날봄날\n\n여름"
    elif change == "joined paragraphs":
        src = "봄날\n\n봄날 봄날\n\n여름"
    elif change == "changed text":
        src = src.replace("여름", "겨울")
    elif change == "missing space":
        src = src.replace("봄날 봄날", "봄날봄날", 1)
    elif change == "wrong face":
        style = {**style, "body_font": "nanum-gothic"}
    elif change == "overfull final":
        src = "봄날 봄날\n\n봄날\n\n여름"
        style = {**style, "margins_mm": [20, 19, 20, 18]}
    with pytest.raises(ValueError, match=message):
        verify_justified_pdf(pdf, src, style)


def test_body_font_box_at_top_margin(measured_text):
    from evaluation.packet import verify_justified_pdf
    from tests.pdf_metrics import read_pages

    pdf, src, style = measured_text
    page, = read_pages(pdf)
    chars = page.lines[0].chars
    # All baselines fit, but the first row's font box extends 0.175 PDF pt past the margin.
    top = (page.height - max(c.y1 for c in chars) + .175) * 25.4 / 72
    assert max(c.baseline for c in chars) < page.height - top * 72 / 25.4
    with pytest.raises(ValueError, match="vertical margins"):
        verify_justified_pdf(pdf, src, {**style, "margins_mm": [top, 18, 20, 18]})


@pytest.mark.parametrize("options,expected", [({}, [-15, 15]), ({"tracking_bounds": [-7, 15]}, [-7, 15])])
def test_quay_packet_rejects_old_render_atomically(tmp_path, monkeypatch, options, expected):
    from fastapi.testclient import TestClient
    from evaluation import packet

    original = TestClient.post

    def legacy(client, url, *, json):
        assert url == "/api/render"
        current = json["style"]
        assert current["paragraph_layout"] == "justified"
        assert current["ko_tracking_bounds_pct"] == expected
        assert current["space_pct"] == [70, 130]
        assert json["source"] == (packet.HERE / "corpus/quay.md").read_text()
        fixed = {k: v for k, v in current.items() if k not in {"paragraph_layout", "ko_tracking_bounds_pct"}}
        return original(client, url, json={**json, "style": {**fixed, "space_pct": [100, 100]}})

    monkeypatch.setattr(TestClient, "post", legacy)
    with pytest.raises(ValueError, match="right edge"):
        packet.justification(tmp_path / "bad", **options)
    assert list(tmp_path.iterdir()) == []


def test_packet_contract_and_blank_reader_sheet(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from httpx import Response
    from evaluation import __main__ as cli, packet
    from evaluation.results import score

    # Transport, acceptance and blinding are separate from this orchestration check.
    calls = []

    def accepted(pdf, source, style):
        assert source == (packet.HERE / "corpus/quay.md").read_text()
        assert pdf == b"rendered"
        calls.append(style)
        return {"nonfinal_lines": 5}

    monkeypatch.setattr(packet, "verify_justified_pdf", accepted)
    monkeypatch.setattr(packet, "blind_pdf", lambda pdf: b"blinded")
    monkeypatch.setattr(TestClient, "post", lambda *args, **kwargs: Response(
        200, content=b"rendered", headers={"content-type": "application/pdf"}))
    dest = tmp_path / "study"
    monkeypatch.setattr(sys, "argv", ["evaluation", "justification", str(dest), "--tracking-bounds", "-9", "18",
                                      "--space-bounds", "75", "125"])
    cli.main()
    key = json.loads((dest / "private/key.json").read_text())
    assert key["experiment"] == "justification" and len(key["groups"]) == 4
    assert len(calls) == 12
    assert {(s["body_font"], s["body_pt"], s["ko_tracking_pct"]) for s in calls} == {
        (f, pt, t) for f in ["nanum-myeongjo", "nanum-gothic"] for pt in (11, 16) for t in (0, -3, -7)}
    assert all(s["paragraph_layout"] == "justified" and s["ko_tracking_bounds_pct"] == [-9, 18]
               and s["space_pct"] == [75, 125] for s in calls)
    for group in key["groups"].values():
        assert len(group["samples"]) == 3
        assert all(s["verification"]["nonfinal_lines"] == 5 for s in group["samples"].values())
    rows = list(csv.DictReader((dest / "reader/reading-sheet.csv").read_text().splitlines()))
    assert len(rows) == len(list((dest / "reader").glob("*.pdf"))) == 12
    assert all(not row[col] for row in rows for col in ("preferred", "crowding", "paragraph", "reason"))
    assert score(dest)["status"] == "awaiting_reader"


@pytest.mark.parametrize("command", ["reference", "pairs"])
@pytest.mark.parametrize("enabled", [False, True])
def test_model_profile_cli(tmp_path, monkeypatch, command, enabled):
    from evaluation import __main__ as cli

    ref = tmp_path / "ref.pdf"
    ref.write_bytes(b"fixture")
    args = ["reference", str(tmp_path), "drawer", str(ref), "--editor", "E", "--paragraph", "1", "--reason", "test"]
    if command == "pairs":
        args = ["pairs", str(tmp_path / "run"), str(tmp_path / "refs"), str(tmp_path / "bundle")]
    received = []
    monkeypatch.setattr(cli, "revision" if command == "reference" else "model_pairs",
                        lambda *a, **kw: received.append(kw))
    monkeypatch.setattr(sys, "argv", ["evaluation", *args, *(["--justified"] if enabled else [])])
    cli.main()
    assert received == ([{"justified": True}] if enabled else [{}])


def test_actual_render_service_packet(tmp_path):
    from pypdf import PdfReader
    from app.document import InputError
    from evaluation.packet import justification, read_json

    dest = tmp_path / "live"
    try:
        apply_style(Style(), {"paragraph_layout": "justified"})
    except InputError as exc:
        assert exc.field == "paragraph_layout"
        with pytest.raises(ValueError, match="render HTTP 4"):
            justification(dest)
        assert not dest.exists()
        return

    justification(dest)
    key = read_json(dest / "private/key.json")
    samples = {sid: s for g in key["groups"].values() for sid, s in g["samples"].items()}
    assert len(samples) == 12
    for sid, s in samples.items():
        assert s["style"]["ko_tracking_bounds_pct"] == [-15, 15]
        check = s["verification"]
        assert check["nonfinal_lines"] == len(check["line_ends"]) - 3 > 0
        assert max(check["max_nonfinal_right_error_pt"], check["max_left_error_pt"]) <= .05
        reader = PdfReader(dest / "reader" / (sid + ".pdf"))
        assert not reader.metadata
        assert all(page.extract_text() == "" and len(page.images) == 1 for page in reader.pages)


@pytest.mark.parametrize(("printed_tracking", "printed_space", "base", "spaces", "error"), [
    (12, 125, 0, [70, 130], "natural final"),
    (0, 125, 0, [70, 130], "natural final space"),
    (-7, 100, -7, [70, 130], None),
    (-3, 110, -3, [110, 130], None),
    (0, 95, 0, [70, 95], None),
])
def test_final_uses_preferred_tracking_and_bounded_natural_space(
        tmp_path, printed_tracking, printed_space, base, spaces, error):
    from evaluation.packet import verify_justified_pdf

    source = "한낮 계단 끝"
    fixed = {"body_font": "nanum-myeongjo", "page_mm": [148, 210], "margins_mm": [20, 18, 20, 18],
             "body_pt": 11, "ko_tracking_pct": printed_tracking, "space_pct": [printed_space] * 2}
    pdf = typeset(parse_md(source), apply_style(Style(), fixed), tmp_path)
    expected = {**fixed, "paragraph_layout": "justified", "ko_tracking_pct": base,
                "ko_tracking_bounds_pct": [-15, 15], "space_pct": spaces}
    if error:
        with pytest.raises(ValueError, match=error):
            verify_justified_pdf(pdf, source, expected)
    else:
        assert verify_justified_pdf(pdf, source, expected)["line_ends"] == [5]


@pytest.mark.parametrize("printed_tracking", [-10, -15])
def test_single_word_final_may_shrink_only_as_much_as_needed(tmp_path, printed_tracking):
    from evaluation.packet import verify_justified_pdf

    source = "봄날봄날"
    width_mm = (11 * (4 * 973 / 1024 - 3 * .10) + .01) * 25.4 / 72.27
    fixed = {"body_font": "nanum-myeongjo", "body_pt": 11, "ko_tracking_pct": printed_tracking,
             "space_pct": [100, 100], "page_mm": [36 + width_mm, 210], "margins_mm": [20, 18, 20, 18]}
    pdf = typeset(parse_md(source), apply_style(Style(), fixed), tmp_path)
    expected = {**fixed, "paragraph_layout": "justified", "ko_tracking_pct": 0,
                "ko_tracking_bounds_pct": [-15, 15], "space_pct": [70, 130]}
    if printed_tracking == -10:
        assert verify_justified_pdf(pdf, source, expected)["line_ends"] == [4]
    else:
        with pytest.raises(ValueError, match="natural final"):
            verify_justified_pdf(pdf, source, expected)


@pytest.mark.parametrize("fault", [None, "tracking_per_10000", "space_per_10000", "missing line", "extra line"])
def test_selected_line_spacing_matches_physical_gaps(measured_text, fault):
    from evaluation.packet import verify_justified_pdf

    lines = [{"tracking_per_10000": 0, "space_per_10000": 10000} for _ in range(3)]
    if fault == "missing line":
        lines.pop()
    elif fault == "extra line":
        lines.append(lines[-1])
    elif fault:
        lines[0][fault] += 1200
    if fault:
        with pytest.raises(ValueError, match="trace"):
            verify_justified_pdf(*measured_text, expected_lines=lines)
    else:
        assert verify_justified_pdf(*measured_text, expected_lines=lines)["line_ends"] == [4, 6, 8]
