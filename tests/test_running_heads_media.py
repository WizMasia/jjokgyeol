import base64
import io
import json
from unittest.mock import patch

import pytest
from PIL import Image, ImageDraw
from test_reference_media import analyze, model_reply, pdf, png


def rows(header, footer, x=156, y=16):
    return [(header, 8, 22, 484), ("Manuscript text", 11, 36, 330), (footer, 8, x, y)]


def field(unit, role, key):
    found = [c for c in unit["candidates"] if (c["role"], c["key"]) == (role, key)]
    assert len(found) == 1, (role, key, found)
    return found[0]


def test_pdf_recovers_fixed_year_and_number_field_with_cross_page_evidence_on_retry():
    source = pdf([rows("2026 보고서", "－ 17 －"), [], rows("2026 보고서", "－ 19 －")])
    with patch("http.client.HTTPConnection.request", side_effect=TimeoutError) as attempted:
        full = analyze(source, "pdf")
        retry = analyze(source, "pdf", page=3)
    assert attempted.call_count == 1
    assert len(full["warnings"]) == 1 and not retry["warnings"]
    for unit in (full["units"][0], full["units"][2], *retry["units"]):
        header, footer = field(unit, "header", "left"), field(unit, "footer", "center")
        assert header["value"] == "2026 보고서" and "options" not in header
        assert footer["value"] == "－ {현재쪽} －" and "options" not in footer
        assert "1쪽" in footer["evidence"] and "3쪽" in footer["evidence"]
        assert footer["source_group"] and footer["variant"] == "unknown"
        assert unit["excerpt"] == "Manuscript text"
        assert field(unit, "body", "font_pt")["value"] == pytest.approx(11, abs=.1)
        assert field(unit, "footer", "color")["value"] == "#336699"
        assert field(unit, "footer", "offset_mm")["value"] > 0
    retried, = retry["units"]
    assert retried["page"] == 3
    assert field(retried, "footer", "center") == field(full["units"][2], "footer", "center")


def test_pdf_infers_only_the_changing_digit_run_and_escapes_literal_braces():
    source = pdf([rows("2026 보고서", f"{{현재쪽}} 2026 / {p}") for p in (17, 18)])
    for unit in analyze(source, "pdf")["units"]:
        assert field(unit, "footer", "center")["value"] == "{{현재쪽}} 2026 / {현재쪽}"


@pytest.mark.parametrize(("last", "x", "y"), [("－ 40 －", 156, 16), ("－ 18 －", 202, 16),
                                               ("－ 18 －", 156, 35)])
def test_pdf_does_not_promote_discontinuous_or_differently_positioned_numbers(last, x, y):
    units = analyze(pdf([rows("2026 보고서", "－ 17 －"), rows("2026 보고서", last, x, y)]), "pdf")["units"]
    for unit in units:
        assert field(unit, "header", "left")["value"] == "2026 보고서"
        assert all("{현재쪽}" not in str(c["value"]) for c in unit["candidates"] if c["role"] == "footer")


def scan_result(footer, **overrides):
    reply = {"section": "본문", "excerpt": "본문", "candidates": [
        {"role": "header", "key": "right", "value": "{현재쪽}", "evidence": "오른쪽 위"},
        {"role": "footer", "key": "center", "value": footer, "evidence": "가운데 아래"},
        {"role": "footer", "key": "offset_mm", "value": 240, "evidence": "아래 여백"},
        {"role": "body", "key": "left", "value": "본문 노출", "evidence": "본문"},
        {"role": "header", "key": "right", "value": ["잘못된 형식"], "evidence": "상단"},
        {"role": "footer", "key": "leading_pt", "value": 210, "evidence": "잘못된 역할"},
    ]}
    call, response, body = model_reply(reply)
    with call, response as server:
        server.return_value.status = 200
        server.return_value.read.return_value = body
        unit, = analyze(png(), "png", **overrides)["units"]
    return unit


def test_scan_requires_explicit_choice_and_keeps_identity_when_transcription_changes():
    unit = scan_result("－ 17 －")
    assert len(unit["candidates"]) == 3
    header = field(unit, "header", "right")
    assert header["value"] == "{{현재쪽}}" and "options" not in header
    num = field(unit, "footer", "center")
    assert num["value"] == "－ 17 －" and num["basis"] == "confirm"
    assert num["options"] == [{"kind": "literal", "value": "－ 17 －"},
                               {"kind": "page", "value": "－ {현재쪽} －"}]
    assert num["variant"] == "unknown" and num["source_group"]
    assert field(scan_result("－ 11 －", page=1), "footer", "center")["source_group"] == num["source_group"]
    offset = field(unit, "footer", "offset_mm")
    assert (offset["value"], offset["unit"]) == (240, "width_per_10000")


def test_model_reads_each_edge_slot_even_when_it_reports_only_body_styles_in_candidates():
    raw = {"section": "본문", "excerpt": "본문",
           "header": {"left": "2026 보고서", "center": "", "right": ""},
           "footer": {"left": "", "center": "－ 17 －", "right": ""},
           "candidates": [{"role": "body", "key": "color", "value": "#000000", "evidence": "본문 색"}]}
    call, reply, body = model_reply(raw)
    with call as request, reply as server:
        server.return_value.status = 200
        server.return_value.read.return_value = body
        unit, = analyze(png(), "png")["units"]
    assert field(unit, "header", "left")["value"] == "2026 보고서"
    assert field(unit, "footer", "center")["options"][1]["value"] == "－ {현재쪽} －"
    assert len(unit["candidates"]) == 3
    schema = json.loads(request.call_args.kwargs["body"])["response_format"]["json_schema"]["schema"]
    assert {"header", "footer"} <= set(schema["required"])
    for role in ("header", "footer"):
        assert set(schema["properties"][role]["required"]) == {"left", "center", "right"}
    content = json.loads(request.call_args.kwargs["body"])["messages"][-1]["content"]
    images = [Image.open(io.BytesIO(base64.b64decode(part["image_url"]["url"].split(",")[1])))
              for part in content if part["type"] == "image_url"]
    assert len(images) == 3
    assert all(image.width == images[0].width and image.height < images[0].height / 5 for image in images[1:])


def test_pdf_distinguishes_alternating_headers_and_proposes_first_page_at_the_same_position():
    headers = ["Cover", "Even", "Odd", "Even", "Odd", "Even", "Odd"]
    source = pdf([rows(title, f"- {n} -") for n, title in enumerate(headers, 1)])
    units = analyze(source, "pdf", max_pages=7, max_units=7)["units"]
    bands = [field(unit, "header", "left") for unit in units]
    assert [c["variant"] for c in bands] == ["first", "even", "odd", "even", "odd", "even", "odd"]
    assert bands[0]["basis"] == "confirm" and "1쪽" in bands[0]["evidence"]
    assert "2쪽" in bands[1]["evidence"] and "6쪽" in bands[1]["evidence"]
    assert "3쪽" in bands[2]["evidence"] and "7쪽" in bands[2]["evidence"]
    assert all(field(u, "footer", "center")["value"] == "- {현재쪽} -" for u in units)
    assert field(analyze(source, "pdf", max_pages=7, max_units=7, page=4)["units"][0], "header", "left") == bands[3]


def test_pdf_single_page_is_limited_evidence_and_keeps_a_possible_title_in_the_excerpt():
    unit, = analyze(pdf([rows("UNIT TITLE", "- 17 -")]), "pdf")["units"]
    header, footer = field(unit, "header", "left"), field(unit, "footer", "center")
    assert (header["variant"], header["basis"]) == ("unknown", "confirm")
    assert "1쪽" in header["evidence"] and "UNIT TITLE" in unit["excerpt"]
    assert (footer["value"], footer["variant"], footer["basis"]) == ("- 17 -", "unknown", "confirm")


def test_pdf_mirrored_page_numbers_get_parity_from_both_outer_positions():
    pages = [rows("Report", f"- {n} -", x=310 if n % 2 else 20) for n in range(1, 5)]
    for unit in analyze(pdf(pages), "pdf")["units"]:
        odd = unit["page"] % 2
        c = field(unit, "footer", "right" if odd else "left")
        assert c["value"] == "- {현재쪽} -" and c["variant"] == ("odd" if odd else "even")


def test_long_pdf_expression_keeps_bounded_field_and_number_evidence_on_retry():
    from app.running_heads import parse_running_text
    source = pdf([[("Body", 11, 36, 330), ("i" * 180 + f" {n}", 2, 20, 14)] for n in (17, 18)])
    for settings in ({}, {"page": 1}):
        for unit in analyze(source, "pdf", max_excerpt_chars=48, max_evidence_chars=60, **settings)["units"]:
            c = field(unit, "footer", "left")
            assert len(c["value"]) <= 48 and ("page", "") in parse_running_text(c["value"])
            assert c["basis"] == "confirm"
            assert all(s in c["evidence"] for s in ("1쪽", "2쪽", "17→18", "한도"))


def test_scan_caps_escaped_literal_and_each_option_without_splitting_a_brace_pair():
    from app.running_heads import parse_running_text
    c = field(scan_result("{" * 6 + "17", max_excerpt_chars=8), "footer", "center")
    assert c["basis"] == "confirm" and "한도" in c["evidence"]
    assert c["value"] == c["options"][0]["value"]
    for option in c["options"]:
        assert len(option["value"]) <= 8
        parts = parse_running_text(option["value"])
        assert ("page", "") in parts if option["kind"] == "page" else parts[-1][1].endswith("17")


def test_scan_preserves_a_fitting_literal_when_only_the_page_option_needs_clipping():
    c = field(scan_result("report17", max_excerpt_chars=8), "footer", "center")
    assert c["value"] == c["options"][0]["value"] == "report17"
    assert c["options"][1]["value"].endswith("{현재쪽}") and len(c["options"][1]["value"]) <= 8


def test_pdf_does_not_expand_running_properties_past_the_unit_budget(monkeypatch):
    from app import reference_media
    original = reference_media._candidate
    emitted = []
    def counted(role, key, *args, **kwargs):
        if role in ("header", "footer"):
            emitted.append((role, key))
        return original(role, key, *args, **kwargs)
    monkeypatch.setattr(reference_media, "_candidate", counted)
    units = analyze(pdf([rows("Report", "- 17 -"), rows("Report", "- 18 -")]), "pdf", max_candidates_per_unit=2)["units"]
    returned = [(c["role"], c["key"]) for u in units for c in u["candidates"] if c["role"] in ("header", "footer")]
    assert emitted == returned and len(returned) == 2


@pytest.mark.parametrize("margin_ink", ["center", "both"])
def test_scan_checks_horizontal_position_only_when_the_ink_is_unambiguous(margin_ink):
    page = Image.new("RGB", (360, 504), "white")
    draw = ImageDraw.Draw(page)
    draw.text((150, 470), "- 17 -", fill="black")
    if margin_ink == "both":
        draw.text((302, 470), "REPORT", fill="black")
    data = io.BytesIO()
    page.save(data, "PNG")
    call, reply, body = model_reply({"section": "본문", "excerpt": "", "candidates": [],
                                    "footer": {"left": "", "center": "", "right": "- 17 -"}})
    with call, reply as server:
        server.return_value.status = 200
        server.return_value.read.return_value = body
        unit, = analyze(data.getvalue(), "png", raster_width=600)["units"]
    c = field(unit, "footer", "center" if margin_ink == "center" else "right")
    assert c["options"][1]["value"] == "- {현재쪽} -"
    if margin_ink == "center":
        assert "px" in c["evidence"] and "가운데" in c["evidence"] and "모델" in c["evidence"]
