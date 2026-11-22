import io
import json
import struct
import zlib
from unittest.mock import patch

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject


SETTINGS = dict(max_bytes=2_000_000, max_pages=5, max_units=5, max_pixels=2_000_000,
                raster_width=256, timeout_seconds=15, vision_timeout_seconds=5,
                max_candidates_per_unit=32, max_excerpt_chars=300, max_evidence_chars=160,
                max_vision_response_bytes=65536, vision_max_tokens=900,
                vision_url="http://127.0.0.1/v1", vision_model="local-vision", vision_token="test-key")


def png(width=64, height=96):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    # No pixel buffer allocation for the oversize header validation case.
    raw = (b"\x00" + b"\xff" * min(width, 256)) * min(height, 256)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def pdf(pages):
    writer = PdfWriter()
    font = DictionaryObject({NameObject(k): NameObject(v) for k, v in
                             [("/Type", "/Font"), ("/Subtype", "/Type1"), ("/BaseFont", "/Helvetica")]})
    # Standard Korean CID font and CMap for the Unicode reference-page fixtures.
    from pypdf.generic import ArrayObject, NumberObject, TextStringObject
    cid = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                            NameObject("/Subtype"): NameObject("/CIDFontType0"),
                            NameObject("/BaseFont"): NameObject("/HYSMyeongJo-Medium"),
                            NameObject("/CIDSystemInfo"): DictionaryObject({
                                NameObject("/Registry"): TextStringObject("Adobe"),
                                NameObject("/Ordering"): TextStringObject("Korea1"),
                                NameObject("/Supplement"): NumberObject(1)})})
    korean = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                               NameObject("/Subtype"): NameObject("/Type0"),
                               NameObject("/BaseFont"): NameObject("/HYSMyeongJo-Medium"),
                               NameObject("/Encoding"): NameObject("/UniKS-UCS2-H"),
                               NameObject("/DescendantFonts"): ArrayObject([cid])})
    for lines in pages:
        page = writer.add_blank_page(width=360, height=504)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font, NameObject("/F2"): korean})})
        commands = []
        for label, size, x, y in lines:
            face, word = ("F1", label.encode("ascii")) if label.isascii() else ("F2", label.encode("utf-16-be"))
            commands.append(f"BT /{face} {size} Tf 0.2 0.4 0.6 rg {x} {y} Td <{word.hex()}> Tj ET")
        if not lines:
            # A visible raster-only page (inline grayscale image).
            commands.append("q 260 0 0 380 44 60 cm BI /W 1 /H 1 /BPC 8 /CS /G /F /AHx ID 80> EI Q")
        stream = DecodedStreamObject()
        stream.set_data("\n".join(commands).encode())
        page[NameObject("/Contents")] = writer._add_object(stream)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def analyze(data, fmt, **overrides):
    from app import reference_media
    return reference_media.analyze_media(data, fmt, {**SETTINGS, **overrides})


def vision_data():
    return {"section": "표지", "excerpt": "SCAN TITLE", "candidates": [
        {"role": "heading", "key": "font_pt", "value": 350, "evidence": "상단 큰 제목"},
        {"role": "body", "key": "color", "value": "#334455", "evidence": "본문 색"},
    ]}


def model_reply(payload):
    from http.client import HTTPConnection
    response = {"choices": [{"message": {"content": json.dumps(payload)}}]}
    call = patch.object(HTTPConnection, "request", autospec=True)
    reply = patch.object(HTTPConnection, "getresponse", autospec=True)
    return call, reply, json.dumps(response).encode()


def test_text_pdf_reads_real_geometry_and_measured_styles():
    lines = [("REFERENCE TITLE", 22, 36, 450), ("This is the body", 11, 36, 398),
             ("Another line", 11, 36, 382), ("1. First item", 11, 48, 342)]
    with patch("http.client.HTTPConnection.request", side_effect=AssertionError("text went to model")):
        result = analyze(pdf([lines]), "pdf")
    page, = result["units"]
    assert page["page"] == 1 and "REFERENCE TITLE" in page["excerpt"]
    assert page["preview"].startswith("data:image/")
    values = {(c["role"], c["key"]): c for c in page["candidates"]}
    assert values["document", "page_mm"]["value"] == pytest.approx([127, 177.8], abs=.02)
    assert values["heading", "font_pt"]["value"] == pytest.approx(22 * 72.27 / 72, abs=.18)
    assert values["body", "color"]["value"].lower() == "#336699"
    assert values["body", "font"]["basis"] == "confirm"
    assert values["body", "leading_pt"]["value"] == pytest.approx(16 * 72.27 / 72, abs=.1)
    assert values["list", "levels.1.expr"]["value"] == "{숫자}."
    assert values["document", "margins_mm"]["basis"] == "estimated"


def test_scan_returns_only_valid_candidates_and_relative_units():
    raw = vision_data()
    raw["candidates"].extend([
        {"role": "document", "key": "source", "value": "unwanted", "evidence": "x"},
        {"role": "body", "key": "font_pt", "value": True, "evidence": "x"},
        {"role": "heading", "key": "font_pt", "value": -1, "evidence": "x"},
        {"role": "body", "key": "leading_pt", "value": 81000, "evidence": "x"},
    ])
    raw["excerpt"] = "가" * 5000
    call, reply, body = model_reply(raw)
    with call as request, reply as response:
        response.return_value.status = 200
        response.return_value.read.return_value = body
        result = analyze(png(), "png")
    unit, = result["units"]
    assert unit["page"] == 1 and len(unit["excerpt"]) < 1000
    assert unit["section"] == "표지"
    assert [(c["key"], c.get("unit")) for c in unit["candidates"]] == [
        ("font_pt", "width_per_10000"), ("color", None)]
    assert unit["candidates"][0]["basis"] == "confirm"
    message = json.loads(request.call_args.kwargs["body"])
    assert request.call_args.args[2] == "/v1/chat/completions"
    assert message["model"] == "local-vision"
    image_url = next(p["image_url"]["url"] for p in message["messages"][-1]["content"] if p["type"] == "image_url")
    assert image_url == unit["preview"]
    assert request.call_args.kwargs["headers"]["Authorization"] == "Bearer test-key"


def test_mixed_pdf_keeps_text_results_and_retries_only_the_requested_page():
    source = pdf([[("REPORT", 16, 35, 400)], [], [("Last page", 11, 35, 380)]])
    with patch("http.client.HTTPConnection.request", side_effect=TimeoutError) as attempted:
        result = analyze(source, "pdf")
    assert attempted.call_count == 1
    assert [u["page"] for u in result["units"]] == [1, 2, 3]
    assert "REPORT" in result["units"][0]["excerpt"]
    assert len(result["warnings"]) == 1 and "2" in result["warnings"][0]
    call, reply, body = model_reply(vision_data())
    with call as attempted, reply as response:
        response.return_value.status = 200
        response.return_value.read.return_value = body
        retried = analyze(source, "pdf", page=2)
    assert attempted.call_count == 1
    unit, = retried["units"]
    assert unit["page"] == 2 and any(c["key"] == "font_pt" for c in unit["candidates"])
    with pytest.raises(ValueError, match="쪽"):
        analyze(source, "pdf", page=4)
    with pytest.raises(ValueError, match="쪽"):
        analyze(png(), "png", page=2)


@pytest.mark.parametrize(("data", "fmt", "limits"), [
    (png(1000, 1000), "png", {"max_pixels": 100}),
    (png(), "png", {"max_bytes": 5}),
    (b"not a jpeg", "jpeg", {}),
    (b"%PDF-broken", "pdf", {}),
    (pdf([[], []]), "pdf", {"max_pages": 1}),
])
def test_invalid_media_and_limits_never_reach_model(data, fmt, limits):
    with patch("http.client.HTTPConnection.request", side_effect=AssertionError("unexpected model")):
        with pytest.raises(ValueError):
            analyze(data, fmt, **limits)


def test_repeated_page_edges_are_editable_and_use_the_current_page_field():
    pages = [[("INSTITUTE", 8, 20, 484), ("Main text", 11, 36, 320), (str(i), 8, 170, 15)]
             for i in (1, 2)]
    result = analyze(pdf(pages), "pdf")
    for unit in result["units"]:
        values = {(c["role"], c["key"]): c["value"] for c in unit["candidates"]}
        assert values["header", "left"] == "INSTITUTE"
        assert values["footer", "center"] == "{현재쪽}"


def test_model_candidates_and_evidence_are_bounded():
    item = {"role": "body", "key": "font_pt", "value": 75, "evidence": "r" * 4000}
    call, reply, body = model_reply({"candidates": [item] + [dict(item, evidence="r")] * 80})
    with call, reply as response:
        response.return_value.status = 200
        response.return_value.read.return_value = body
        result = analyze(png(), "png")
    candidates = result["units"][0]["candidates"]
    assert 1 <= len(candidates) <= 40
    assert max(len(c["evidence"]) for c in candidates) < 1000


def test_expired_scan_budget_keeps_later_text_and_addressable_scan_pages(monkeypatch):
    from app import reference_media
    now = [200.0]
    monkeypatch.setattr(reference_media, "monotonic", lambda: now[0])
    source = pdf([[], [], [("LAST TEXT", 11, 35, 420)]])
    def slow_response(connection, *args, **kwargs):
        assert 0 < connection.timeout < 15
        now[0] += 15
        raise TimeoutError
    with patch("http.client.HTTPConnection.request", autospec=True, side_effect=slow_response) as attempted:
        result = analyze(source, "pdf", vision_timeout_seconds=90)
    assert attempted.call_count == 1
    assert [u["page"] for u in result["units"]] == [1, 2, 3]
    assert "LAST TEXT" in result["units"][2]["excerpt"]
    assert all(any(str(i) in warning for warning in result["warnings"]) for i in (1, 2))


def test_unusable_vision_reply_keeps_page_with_actionable_warning():
    call, reply, body = model_reply({"candidates": [
        {"role": "body_text", "key": "instruction", "value": "copied sentence", "evidence": "body"}]})
    with call as request, reply as response:
        response.return_value.status = 200
        response.return_value.read.return_value = body
        result = analyze(png(), "png")
    assert result["units"][0]["candidates"] == []
    assert result["warnings"] and "1쪽" in result["warnings"][0]
    spec = json.loads(request.call_args.kwargs["body"])["response_format"]
    fields = spec["json_schema"]["schema"]["properties"]["candidates"]["items"]["properties"]
    assert set(fields["role"]["enum"]) == {"document", "heading", "body", "list", "header", "footer"}
    assert {"font_pt", "offset_mm", "left", "center", "right"} <= set(fields["key"]["enum"])


def test_simple_scan_font_estimate_tracks_visible_ink_instead_of_model_number():
    from PIL import Image, ImageDraw
    image = Image.new("RGB", (480, 670), "white")
    draw = ImageDraw.Draw(image)
    for top, height, count in [(42, 30, 11), (140, 16, 24), (186, 16, 21), (232, 16, 23), (278, 16, 19)]:
        for i in range(count):
            x = 42 + i * 13
            draw.rectangle((x, top, x + 1, top + height - 1), fill="black")
            draw.rectangle((x + 7, top, x + 8, top + height - 1), fill="black")
            draw.line((x, top + height // 2, x + 8, top + height // 2), fill="black")
    for fmt in ("PNG", "JPEG"):
        out = io.BytesIO()
        image.save(out, fmt)
        call, reply, body = model_reply({"candidates": [
            {"role": "body", "key": "font_pt", "value": 10, "evidence": "본문"}]})
        with call, reply as response:
            response.return_value.status = 200
            response.return_value.read.return_value = body
            unit, = analyze(out.getvalue(), fmt.lower(), raster_width=640)["units"]
        fonts = {c["role"]: c for c in unit["candidates"] if c["key"] == "font_pt"}
        for role, height in (("body", 16), ("heading", 30)):
            c = fonts[role]
            assert c["basis"] == "estimated" and c["unit"] == "width_per_10000"
            assert c["value"] == pytest.approx(10000 * height / 480, rel=.10)
            assert "px" in c["evidence"]


def test_model_and_return_limits_come_from_operator_settings():
    call, reply, body = model_reply(vision_data())
    with call as req, reply as resp:
        resp.return_value.status = 200
        resp.return_value.read.return_value = body
        result = analyze(png(), "png", max_candidates_per_unit=1, max_excerpt_chars=4,
                         max_evidence_chars=5, max_vision_response_bytes=4096, vision_max_tokens=342)
    unit, = result["units"]
    assert len(unit["candidates"]) == 1 and len(unit["candidates"][0]["evidence"]) <= 5
    assert len(unit["excerpt"]) <= 4
    assert json.loads(req.call_args.kwargs["body"])["max_tokens"] == 342


def test_korean_contents_heading_starts_the_contents_section():
    from app.reference_media import Line, _text_styles, _unit
    unit = _unit(2)
    text = [Line("차례", 30, 60, 35, 20, 18, "NanumGothic", "#000000"),
            Line("첫 장", 30, 105, 40, 12, 11, "NanumMyeongjo", "#000000")]
    _text_styles(unit, text, 350, 500, set(), 11)
    assert unit["section"] == "목차"


def test_text_pdf_measures_list_label_gap_from_actual_word_positions():
    result = analyze(pdf([[("TITLE", 22, 24, 465), ("Paragraph", 11, 24, 420),
                           ("1. Item one", 11, 44, 330), ("2. Second item", 11, 44, 306)]]), "pdf")
    c = next(c for c in result["units"][0]["candidates"] if c["key"] == "levels.1.gap_mm")
    # Helvetica's space is 278/1000 em at 11 PDF points.
    assert c["value"] == pytest.approx(.278 * 11 * 25.4 / 72, abs=.04)
    assert c["basis"] == "estimated" and c["role"] == "list"
