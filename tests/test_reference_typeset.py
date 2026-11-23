"""Inspect printed glyphs after applying reference styles to manuscript sections."""
from io import BytesIO

import pytest
from pdfminer.high_level import extract_pages
from pdfminer.layout import LTChar, LTContainer

from app.document import parse_md
from app.style import Style, apply_style
from app.typeset import typeset


BP_PER_PT = 72 / 72.27
BP_PER_MM = 72 / 25.4


def glyphs(pdf):
    def walk(container):
        for child in container:
            if isinstance(child, LTChar):
                yield child
            elif isinstance(child, LTContainer):
                yield from walk(child)

    return [glyph for page in extract_pages(BytesIO(pdf)) for glyph in walk(page)]


def matching(chars, text):
    return sorted((c for c in chars if c.get_text() == text), key=lambda c: (-c.y0, c.x0))


def color(glyph):
    value = glyph.graphicstate.ncolor
    if value is None:
        return (0, 0, 0)
    return (value,) * 3 if isinstance(value, (int, float)) else value


def assert_appearance(chars, pt, face, rgb):
    assert chars
    for char in chars:
        assert char.size == pytest.approx(pt * BP_PER_PT, abs=.015)
        assert char.fontname.split("+")[-1] == face
        assert color(char) == pytest.approx(rgb, abs=.001)


def test_section_heading_body_face_color_and_paragraph_geometry(tmp_path):
    source = ("# 표지\n\n겉\n\n# 제8조 본문\n\n" + "산" * 45 +
              "[^주]\n\n- 잎\n\n# 꼬리\n\n끝\n\n[^주]: 풀이")
    rules = [
        {"path": ["표지"], "role": "heading", "values": {
            "font_pt": 24, "font": "nanum-gothic", "color": "#0000FF"}},
        {"path": ["제8조 본문"], "role": "body", "values": {
            "font_pt": 14, "leading_pt": 22, "font": "nanum-gothic", "color": "#cc3300",
            "first_indent_mm": 2, "rest_indent_mm": 6}},
    ]
    # Both global families use Myeongjo: the requested Gothic must still be available.
    style = apply_style(Style(), {"heading_font": "nanum-myeongjo", "article_expr": "제{숫자}조",
                                  "page_mm": [90, 190], "margins_mm": [10] * 4, "section_rules": rules})
    chars = glyphs(typeset(parse_md(source), style, tmp_path))
    assert_appearance(matching(chars, "표"), 24, "NanumGothicBold", (0, 0, 1))
    assert_appearance(matching(chars, "제"), 17.6, "NanumMyeongjoBold", (0, 0, 0))
    assert_appearance(matching(chars, "산"), 14, "NanumGothic", (.8, .2, 0))
    assert_appearance(matching(chars, "풀"), 8, "NanumMyeongjo", (0, 0, 0))
    footnote_label = min(matching(chars, "1"), key=lambda c: c.y0)
    assert color(footnote_label) == (0, 0, 0)
    for letter in ("겉", "잎", "끝"):
        assert_appearance(matching(chars, letter), 11, "NanumMyeongjo", (0, 0, 0))
    rows = {}
    for c in matching(chars, "산"):
        rows.setdefault(round(c.matrix[5], 3), c.x0)
    assert len(rows) >= 3
    assert list(rows.values()) == pytest.approx([12 * BP_PER_MM] + [16 * BP_PER_MM] * (len(rows) - 1), abs=.015)
    assert [a - b for a, b in zip(rows, list(rows)[1:])] == pytest.approx([22 * BP_PER_PT] * (len(rows) - 1), abs=.015)


def test_list_boxes_use_effective_face_and_own_sibling_width(tmp_path):
    rule = {"path": ["안"], "role": "list", "values": {
        "font_pt": 16, "leading_pt": 24, "font": "nanum-gothic", "color": "#0080FF",
        "first_indent_mm": 2, "rest_indent_mm": 5,
        "levels.1.expr": "{숫자}.", "levels.1.indent_mm": 3, "levels.1.gap_mm": 4,
        "levels.2.expr": "[{한글}]", "levels.2.gap_mm": 1}}
    style = apply_style(Style(), {"page_mm": [92, 200], "margins_mm": [10] * 4,
                                  "list_levels": [{"expr": "{숫자}.", "indent_mm": 0, "gap_mm": 1}],
                                  "section_rules": [rule]})
    source = "# 밖\n\n9. 샘\n10. 숲\n\n# 안\n\n9. 들\n10. " + "밭" * 28 + "\n    - 꽃\n\n    속\n\n돌"
    chars = glyphs(typeset(parse_md(source), style, tmp_path))

    def line_with(letter):
        c = matching(chars, letter)[0]
        row = sorted((a for a in chars if abs(c.matrix[5] - a.matrix[5]) < .005), key=lambda a: a.x0)
        return [a for a in row if not a.get_text().isspace()]

    outer = line_with("숲")
    first = line_with("들")
    second = line_with("밭")
    nested = line_with("꽃")
    assert "".join(c.get_text() for c in outer) == "10.숲"
    assert "".join(c.get_text() for c in first) == "9.들"
    assert "".join(c.get_text() for c in second).startswith("10.밭")
    assert "".join(c.get_text() for c in nested) == "[가]꽃"
    assert_appearance(outer, 11, "NanumMyeongjo", (0, 0, 0))
    for row in (first, second, nested):
        assert_appearance(row, 16, "NanumGothic", (0, 128 / 255, 1))
    origin = second[2].x0 + second[2].adv + 4 * BP_PER_MM
    assert first[0].x0 == pytest.approx(15 * BP_PER_MM, abs=.02)
    assert first[-1].x0 == pytest.approx(origin, abs=.02)
    assert second[3].x0 == pytest.approx(origin, abs=.02)
    assert nested[0].x0 == pytest.approx(origin, abs=.02)
    assert nested[-1].x0 == pytest.approx(nested[-2].x0 + nested[-2].adv + BP_PER_MM, abs=.02)
    rows = {}
    for c in matching(chars, "밭"):
        rows.setdefault(round(c.matrix[5], 3), c.x0)
    assert len(rows) >= 2
    rest = origin + (5 - 2) * BP_PER_MM
    assert list(rows.values())[1:] == pytest.approx([rest] * (len(rows) - 1), abs=.02)
    continued, = line_with("속")
    assert continued.x0 == pytest.approx(rest, abs=.02)
    assert [a - b for a, b in zip(rows, list(rows)[1:])] == pytest.approx(
        [24 * BP_PER_PT] * (len(rows) - 1), abs=.02)
    untouched, = matching(chars, "돌")
    assert color(untouched) == (0, 0, 0) and untouched.fontname.endswith("NanumMyeongjo")


def test_rule_finds_advertised_setext_section_after_example_without_article_numbering(tmp_path):
    from app.reference_rules import targets
    from app.style import from_stored_style

    source = "~~~\n# 제7조 예시\n~~~\n제8조 목적\n===\n\n쌀"
    assert targets(source) == [{"path": ["제8조 목적"], "count": 1, "level": 1}]
    style = apply_style(Style(), {"section_rules": [
        {"path": ["제8조 목적"], "role": "body", "values": {"font_pt": 14}}]})
    reopened = from_stored_style(style.to_stored())
    chars = glyphs(typeset(parse_md(source), reopened, tmp_path))
    grain, = matching(chars, "쌀")
    assert grain.size == pytest.approx(14 * BP_PER_PT, abs=.015)
    assert matching(chars, "8") and matching(chars, "7")


def test_scoped_color_preserves_unrelated_fenced_heading_geometry(tmp_path):
    doc = parse_md("# 선택\n\n달\n\n~~~\n# 제7조 예시\n~~~\n제8조 목적\n===\n\n쌀")
    geometry = []
    for label, patch in (("before", {}), ("after", {"section_rules": [
        {"path": ["선택"], "role": "body", "values": {"color": "#FF0000"}}]})):
        work = tmp_path / label
        work.mkdir()
        chars = glyphs(typeset(doc, apply_style(Style(), patch), work))
        assert color(matching(chars, "달")[0]) == ((1, 0, 0) if patch else (0, 0, 0))
        assert color(matching(chars, "쌀")[0]) == (0, 0, 0)
        geometry.append([(c.get_text(), c.bbox) for c in chars])
    assert geometry[0] == geometry[1]


def test_only_selected_setext_heading_separates_from_its_fence(tmp_path):
    from app.reference_rules import targets

    source = ("~~~\n# 예시\n~~~\n제8조 목적\n===\n\n쌀\n\n"
              "~~~\n# 예시\n~~~\n적용 범위\n---\n\n달")
    path = ["제8조 목적", "적용 범위"]
    assert targets(source) == [{"path": path[:1], "count": 1, "level": 1},
                               {"path": path, "count": 1, "level": 2}]
    style = apply_style(Style(), {"section_rules": [
        {"path": path, "role": "heading", "values": {"font_pt": 24, "color": "#0000FF"}},
        {"path": path, "role": "body", "values": {"color": "#FF0000"}}]})
    chars = glyphs(typeset(parse_md(source), style, tmp_path))
    tilde_rows = {c.matrix[5] for c in matching(chars, "~")}
    assert matching(chars, "목")[0].matrix[5] in tilde_rows
    selected, = matching(chars, "범")
    assert selected.matrix[5] not in tilde_rows
    assert_appearance([selected], 24, "NanumGothicBold", (0, 0, 1))
    assert color(matching(chars, "달")[0]) == (1, 0, 0)
    assert color(matching(chars, "쌀")[0]) == (0, 0, 0)


def test_table_with_editor_memo_prints_only_public_cells(tmp_path):
    doc = parse_md("| 구분 | 내용 |\n| --- | --- |\n| 10.16. | 공개**항목[^m]** |\n\n[^m]{메모}: 내부검토")
    style = apply_style(Style(), {"section_rules": [{"path": [], "role": "body",
                                                     "values": {"font": "nanum-gothic", "font_pt": 14}}]})
    chars = glyphs(typeset(doc, style, tmp_path))
    visible = "".join(c.get_text() for c in chars)
    assert "공개항목" in visible
    assert "|" not in visible and "내부검토" not in visible
    assert_appearance(matching(chars, "개"), 14, "NanumGothic", (0, 0, 0))
    assert_appearance(matching(chars, "항"), 14, "NanumGothicBold", (0, 0, 0))


@pytest.mark.parametrize(("header", "message"), [
    (["칸"] * 40, "폭"),
    (["제목 " * 400, "구분"], "높이"),
])
def test_impossible_table_reports_which_dimension_to_adjust(tmp_path, header, message):
    from app.document import InputError

    rows = [header, ["---"] * len(header), ["글"] * len(header)]
    source = "\n".join("| " + " | ".join(row) + " |" for row in rows)
    style = apply_style(Style(), {"page_mm": [70, 70], "margins_mm": [9] * 4})
    with pytest.raises(InputError, match=message) as error:
        typeset(parse_md(source), style, tmp_path)
    assert error.value.field == "style"
