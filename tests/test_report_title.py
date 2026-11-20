"""The optional report title frame, from sparse style files to printed PDF rules."""
from io import BytesIO

import pytest
from pdfminer.high_level import extract_pages
from pdfminer.layout import LTCurve

from app.document import InputError, parse_md
from app.style import Style, apply_style, from_stored_style
from app.typeset import typeset
from pdf_metrics import read_pages
from test_reference_typeset import BP_PER_MM, BP_PER_PT, assert_appearance, glyphs, matching


def test_heading_frame_round_trips_sparsely():
    frame = {"heading1": {"frame": "box"}}
    style = apply_style(Style(), {"text_styles": frame})
    assert style.to_editor()["text_styles"] == style.to_stored()["text_styles"] == frame
    edited = apply_style(style, {"text_styles": {"heading1": {"font_pt": 20}}})
    assert edited.to_stored()["text_styles"] == {"heading1": {"frame": "box", "font_sp": 20 * 65536}}
    assert from_stored_style(edited.to_stored()) == apply_style(Style(), edited.to_editor()) == edited
    assert "text_styles" not in from_stored_style(Style().to_stored()).to_editor()


@pytest.mark.parametrize(("role", "value"), [("heading1", "circle"), ("heading2", "box"), ("body", "box")])
def test_only_heading_one_accepts_box(role, value):
    patch = {"text_styles": {role: {"frame": value}}}
    with pytest.raises(InputError) as editor:
        apply_style(Style(), patch)
    with pytest.raises(InputError) as stored:
        from_stored_style(Style().to_stored() | patch)
    assert editor.value.field == stored.value.field == f"text_styles.{role}.frame"


def test_long_heading_wraps_centered_in_full_width_black_rules(tmp_path):
    title = "올해 바람 관측 자료를 모은 기상 연구 결과 보고서"
    style = apply_style(Style(), {
        "page_mm": [100, 150], "margins_mm": [10, 12, 10, 16], "gutter_mm": 4,
        "text_styles": {"heading1": {"frame": "box", "font_pt": 20}},
        "section_rules": [{"path": [title], "role": "heading", "values": {
            "font_pt": 16, "leading_pt": 23, "font": "nanum-myeongjo", "color": "#0000FF"}}],
    })
    pdf = typeset(parse_md(f"# {title}\n\n## 소제목\n\n본문"), style, tmp_path)
    page, = extract_pages(BytesIO(pdf))
    rules = [obj for obj in page if isinstance(obj, LTCurve)]
    horizontal = sorted((r for r in rules if r.width > r.height), key=lambda r: r.y0)
    vertical = sorted((r for r in rules if r.width < r.height), key=lambda r: r.x0)
    bottom, top = horizontal
    left, right = vertical
    for rule in rules:
        ink = rule.non_stroking_color if rule.fill else rule.stroking_color
        assert ink in (None, 0, (0, 0, 0))
    for edge in horizontal:
        assert (edge.x0, edge.x1) == pytest.approx((20 * BP_PER_MM, 88 * BP_PER_MM), abs=.5)
    for edge, x in ((left, bottom.x0), (right, bottom.x1)):
        assert edge.x0 == pytest.approx(x, abs=.5)
        assert (edge.y0, edge.y1) == pytest.approx((bottom.y0, top.y1), abs=.6)

    rows = read_pages(pdf)[0].lines
    title_rows = [row for row in rows if bottom.y1 < row.chars[0].baseline < top.y0]
    assert "".join("".join(row.text.split()) for row in title_rows) == "".join(title.split())
    assert len(title_rows) >= 2
    for row in title_rows:
        first, last = row.chars[0], row.chars[-1]
        assert bottom.x0 < first.x0 < last.x1 < bottom.x1
        assert (first.x0 + last.x1) / 2 == pytest.approx((bottom.x0 + bottom.x1) / 2, abs=.1)
    assert title_rows[0].chars[0].baseline - title_rows[1].chars[0].baseline == pytest.approx(23 * BP_PER_PT, abs=.02)
    chars = glyphs(pdf)
    assert_appearance(matching(chars, "올"), 16, "NanumMyeongjoBold", (0, 0, 1))
    subheading, = matching(chars, "소")
    assert subheading.x0 == pytest.approx(20 * BP_PER_MM, abs=.02)
    assert subheading.y1 < bottom.y0
    assert_appearance([subheading], 16.5, "NanumGothicBold", (0, 0, 0))


def test_framed_title_keeps_footnotes_with_page_text(tmp_path):
    style = apply_style(Style(), {"text_styles": {
        "heading1": {"frame": "box", "font_pt": 20}, "note": {"color": "#008000"}}})
    source = "# 관측**초안[^첫]**\n\n본문[^첫]과 설명[^둘]\n\n[^첫]: 구름 기록\n[^둘]: 햇빛 기록"
    pdf = typeset(parse_md(source), style, tmp_path)
    chars = glyphs(pdf)
    assert_appearance(matching(chars, "관"), 20, "NanumGothicBold", (0, 0, 0))
    body, = matching(chars, "본")
    for label, first_letter, expected_marks in (("1", "구", 3), ("2", "햇", 2)):
        note, = matching(chars, first_letter)
        assert_appearance([note], 8, "NanumMyeongjo", (0, 128 / 255, 0))
        assert note.y1 < body.y0 / 2
        marks = matching(chars, label)
        assert len(marks) == expected_marks
        assert abs(marks[-1].y0 - note.y0) < 8


@pytest.mark.parametrize("page_width", [22, 28])
def test_too_narrow_frame_reports_layout_error(tmp_path, page_width):
    style = apply_style(Style(), {"page_mm": [page_width, 150], "margins_mm": [10] * 4,
                                 "text_styles": {"heading1": {"frame": "box", "font_pt": 20}}})
    with pytest.raises(InputError) as exc:
        typeset(parse_md("# 봄"), style, tmp_path)
    assert exc.value.field == "style"


def test_wrapping_beyond_page_height_reports_heading_field(tmp_path):
    style = apply_style(Style(), {"page_mm": [38, 80], "margins_mm": [10] * 4,
                                 "text_styles": {"heading1": {"frame": "box", "font_pt": 20}}})
    with pytest.raises(InputError) as exc:
        typeset(parse_md("# " + "관측 보고 " * 12), style, tmp_path)
    assert exc.value.field == "text_styles.heading1.frame"
