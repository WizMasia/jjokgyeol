"""Measure list columns and page continuations in real engine output."""
import pytest

from app.document import InputError, parse_md
from app.style import Style, apply_style
from app.typeset import typeset
from pdf_metrics import read_pages


BP_PER_MM = 72 / 25.4


def baselines(pdf):
    # The PDF reader may split a marker and its text into separate text boxes.
    result = []
    for page_no, page in enumerate(read_pages(pdf), 1):
        lines = {}
        for line in page.lines:
            for c in line.chars:
                if not c.text.isspace():
                    lines.setdefault(round(c.baseline, 2), []).append(c)
        result.extend((page_no, sorted(chars, key=lambda c: c.x0))
                      for _, chars in sorted(lines.items(), reverse=True))
    return result


def text(chars):
    return "".join(c.text for c in chars)


def test_sibling_column_uses_printed_label_including_spaces(tmp_path):
    expr = r"  {{붙임}}  {숫자}.\%  "
    style = apply_style(Style(), {"ko_tracking_pct": -7, "first_indent_mm": 6, "rest_indent_mm": 8,
                                  "list_levels": [{"expr": expr, "indent_mm": 3, "gap_mm": 2}]})
    pdf = typeset(parse_md("9. 상류\n10. 하류"), style, tmp_path)
    first, second = [chars for _, chars in baselines(pdf)]
    assert [text(first), text(second)] == [r"{붙임}9.\%상류", r"{붙임}10.\%하류"]
    space = 11 * 72 / 72.27 * 307 / 1024
    left = 21 * BP_PER_MM
    for chars in (first, second):
        assert chars[0].x0 == pytest.approx(left + 2 * space, abs=.05)
        brace = next(c for c in chars if c.text == "}")
        digit = next(c for c in chars if c.text.isdigit())
        assert digit.x0 - brace.x0 - brace.advance == pytest.approx(2 * space, abs=.05)
        a, b = chars[1:3]
        assert b.x0 - a.x0 - a.advance == pytest.approx(-.07 * a.size, abs=.05)
    last = next(c for c in second if c.text == "%")
    origin = last.x0 + last.advance + 2 * space + 2 * BP_PER_MM
    assert [first[-2].x0, second[-2].x0] == pytest.approx([origin, origin], abs=.05)


@pytest.mark.parametrize(("preset", "marks"), [
    ("legal", ["①", "1.", "가.", "(1)", "(가)"]),
    ("report", ["ㅁ", "ㅇ", "-", "-", "-"]),
])
def test_five_levels_use_parent_text_origin(tmp_path, preset, marks):
    from app.style import editor_bootstrap

    style = Style()
    if preset == "report":
        style = apply_style(style, {"list_levels": editor_bootstrap()["list_presets"]["report"]})
    words = ["큰길", "봄길", "물길", "솔길", "숲길"]
    source = "# 제1조\n\n" + "\n".join("  " * i + "- " + w for i, w in enumerate(words))
    lines = [chars for _, chars in baselines(typeset(parse_md(source), style, tmp_path))]
    heading, *items = lines
    assert text(heading) == "제1조"
    parent = 18 * BP_PER_MM
    assert len(items) == 5
    for i, (chars, mark, word) in enumerate(zip(items, marks, words)):
        assert text(chars) == mark + word
        assert chars[0].x0 == pytest.approx(parent + (2 if i else 0) * BP_PER_MM, abs=.05)
        last = chars[len(mark)-1]
        parent = chars[len(mark)].x0
        assert parent == pytest.approx(last.x0 + last.advance + 2 * BP_PER_MM, abs=.05)


def test_mark_stays_with_text_across_pages_and_continued_paragraph(tmp_path):
    source = "19. 시작\n20. " + "풀길 " * 75 + "\n\n    이음 문단\n21. 종착[^주]\n\n[^주]: 강물 기록"
    style = apply_style(Style(), {"page_mm": [75, 76], "margins_mm": [9, 9, 9, 9]})
    lines = baselines(typeset(parse_md(source), style, tmp_path))
    body = [(page, [c for c in chars if abs(c.size - 11 * 72 / 72.27) < .05])
            for page, chars in lines]
    body = [(page, chars) for page, chars in body if chars]
    marks = ("⑲", "⑳", "(21)")
    firsts = [(page, chars, m) for page, chars in body for m in marks if text(chars).startswith(m)]
    assert [m for _, _, m in firsts] == list(marks)
    assert [chars[len(m)].text for _, chars, m in firsts] == ["시", "풀", "종"]
    origin = firsts[-1][1][4].x0
    assert firsts[-1][0] > firsts[0][0]
    assert any(text(chars).startswith("이음") for _, chars in body)
    for _, chars in body:
        mark = next((m for m in marks if text(chars).startswith(m)), "")
        assert chars[len(mark)].x0 == pytest.approx(origin, abs=.05)
    assert [page for page, chars in lines if "강물기록" in text(chars)] == [firsts[-1][0]]


@pytest.mark.parametrize(("mark", "indent"), [("M" * 15, 0), ("I", 28), pytest.param("가" * 4000, 0, id="wide-sp-sum")])
def test_unplaceable_item_reports_stage(tmp_path, mark, indent):
    style = apply_style(Style(), {"page_mm": [52, 90], "margins_mm": [10] * 4,
                                  "list_levels": [{"expr": mark, "indent_mm": indent, "gap_mm": 2}]})
    with pytest.raises(InputError) as error:
        typeset(parse_md("- 강가"), style, tmp_path)
    assert error.value.field.startswith("list_levels")
    assert "1단계" in error.value.message


def test_empty_parent_and_independent_group_width_after_heading(tmp_path):
    source = "1.\n   - 아이\n2. 꼬리\n\n# 제2조\n\n100. 새길"
    style = apply_style(Style(), {"list_levels": [
        {"expr": "{숫자}.", "indent_mm": 0, "gap_mm": 2},
        {"expr": "{숫자})", "indent_mm": 2, "gap_mm": 2},
    ]})
    lines = [chars for _, chars in baselines(typeset(parse_md(source), style, tmp_path))]
    assert list(map(text, lines)) == ["1.", "1)아이", "2.꼬리", "제2조", "100.새길"]
    _, child, sibling, _, new_group = lines
    assert child[0].x0 == pytest.approx(sibling[2].x0 + 2 * BP_PER_MM, abs=.05)
    assert new_group[0].x0 == pytest.approx(sibling[0].x0, abs=.05)
    assert new_group[4].x0 - sibling[2].x0 == pytest.approx(sum(c.advance for c in new_group[1:3]), abs=.05)


@pytest.mark.parametrize("spaces", [0, 1, 2])
def test_blank_marker_keeps_its_exact_width(tmp_path, spaces):
    style = apply_style(Style(), {"list_levels": [{"expr": " " * spaces, "indent_mm": 2, "gap_mm": 3}]})
    lines = baselines(typeset(parse_md("- 시내"), style, tmp_path))
    (_, chars), = lines
    assert text(chars) == "시내"
    space_width = 11 * 72 / 72.27 * 307 / 1024
    assert chars[0].x0 == pytest.approx(23 * BP_PER_MM + spaces * space_width, abs=.05)


@pytest.mark.parametrize("room_sp", [-1, 0, 1])
def test_long_marker_leaves_exactly_one_body_glyph(tmp_path, room_sp):
    from dataclasses import replace

    mark = "가" * 24
    style = apply_style(Style(), {"ko_tracking_pct": -7, "list_levels": [
        {"expr": mark, "indent_mm": 0, "gap_mm": 0},
    ]})
    # NanumMyeongjo's Hangul advance is 973/1024 em. Only the marker's
    # internal 23 gaps carry tracking; the body starts a fresh line state.
    advance = style.body_sp * 973 // 1024
    tracking = ((len(mark) - 1) * style.body_sp * 700 + 5000) // 10000
    width = (len(mark) + 1) * advance - tracking + room_sp
    style = replace(style, page_sp=(sum(style.margins_sp[1::2]) + width, style.page_sp[1]))
    doc = parse_md("- 나")
    if room_sp < 0:
        with pytest.raises(InputError) as error:
            typeset(doc, style, tmp_path)
        assert error.value.field == "list_levels" and "1단계" in error.value.message
    else:
        (_, chars), = baselines(typeset(doc, style, tmp_path))
        assert text(chars) == mark + "나"
