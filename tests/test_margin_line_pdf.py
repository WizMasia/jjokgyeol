"""Measure proof line numbers against the manuscript glyphs in real PDFs."""
from collections import defaultdict

import pytest

from app.document import InputError, parse_md
from app.style import Style, apply_style
from app.typeset import typeset
from pdf_metrics import read_pages
from test_reference_typeset import BP_PER_MM, BP_PER_PT


def render(work, source, **patch):
    work.mkdir()
    return read_pages(typeset(parse_md(source), apply_style(Style(), patch), work))


def glyphs(page):
    return [c for row in page.lines for c in row.chars]


def assert_same_glyphs(before, after):
    def order(c):
        return c.text, round(c.baseline, 1), c.x0

    for a, b in zip(sorted(before, key=order), sorted(after, key=order), strict=True):
        assert (a.text, a.font.split("+")[-1]) == (b.text, b.font.split("+")[-1])
        assert (b.x0, b.x1, b.baseline, b.size) == pytest.approx(
            (a.x0, a.x1, a.baseline, a.size), abs=.02)


@pytest.mark.parametrize(("side", "layout"), [("left", {}), ("right", {"paragraph_layout": "justified"})],
                         ids=["left-legacy", "right-justified-framed"])
def test_numbers_match_printed_lines_without_moving_any_text(tmp_path, side, layout):
    source = ("# " + "탑" * 26 + "\n\n" + "강물 " * 35 + "[^끝]\n\n- " + "솔잎 " * 95
              + "\n\n    숲[^주]\n\n[^주]: " + "각주 " * 120 + "\n[^끝]{미주}: 미주자료")
    patch = {
        "page_mm": [85, 95], "margins_mm": [12, 9, 12, 13], "gutter_mm": 4,
        "first_indent_mm": 3, "rest_indent_mm": 1, "body_font": "nanum-gothic",
        "heading_font": "nanum-myeongjo",
        "text_styles": {"note": {"font_pt": 9},
                        **({"heading1": {"frame": "box"}} if layout else {})},
        "section_rules": [
            {"path": [], "role": "header", "values": {"visible": True, "left": "머리", "offset_mm": 2}},
            {"path": [], "role": "footer", "values": {"visible": True, "right": "{현재쪽}", "offset_mm": 2}},
        ],
        **layout,
    }
    off = render(tmp_path / "off", source, **patch)
    numbered = render(tmp_path / side, source, line_numbers=side, **patch)
    next_number = 1
    wrapped = defaultdict(set)
    assert len(off) >= 3
    for page, (old, new) in enumerate(zip(off, numbered, strict=True), 1):
        assert (new.width, new.height) == (old.width, old.height)
        left, right = ((17, 76) if page % 2 else (9, 68))
        left, right = left * BP_PER_MM, right * BP_PER_MM
        original = glyphs(old)
        baselines = set()
        for c in original:
            if c.text in {"탑", "강", "물", "솔", "잎", "숲"}:
                y = round(c.baseline, 2)
                baselines.add(y)
                wrapped[c.text].add((page, y))
        labels, manuscript = defaultdict(list), []
        for c in glyphs(new):
            if c.x1 < left - 1 or c.x0 > right + 1:
                labels[round(c.baseline, 2)].append(c)
            else:
                manuscript.append(c)
        assert sorted(labels) == pytest.approx(sorted(baselines), abs=.02)
        for y in sorted(labels, reverse=True):
            digits = sorted(labels[y], key=lambda c: c.x0)
            assert "".join(c.text for c in digits) == str(next_number)
            assert all(c.font.split("+")[-1] == "NanumGothic" for c in digits)
            assert all(abs(c.size - 8 * BP_PER_PT) < .02 for c in digits)
            gap = left - digits[-1].x1 if side == "left" else digits[0].x0 - right
            assert gap == pytest.approx(6 * BP_PER_PT, abs=.03)
            next_number += 1
        assert_same_glyphs(original, manuscript)
    assert len(wrapped["탑"]) > 1 and len(wrapped["강"]) > 1
    assert len({page for page, _ in wrapped["솔"]}) > 1 and next_number > 10
    assert any(not any(c.text in "탑강물솔잎숲" for c in glyphs(p)) for p in off)
    assert {"각", "자", "머"} <= {c.text for p in off for c in glyphs(p)}


@pytest.mark.parametrize(("side", "outer", "inner"), [("left", 2, 12), ("right", 12, 2)])
def test_reports_first_physical_page_with_insufficient_margin(tmp_path, side, outer, inner):
    with pytest.raises(InputError) as error:
        render(tmp_path / "narrow", "물\n\n" * 20, page_mm=[70, 68],
               margins_mm=[10, outer, 10, inner], gutter_mm=0, line_numbers=side)
    assert error.value.field == "line_numbers"
    assert (tmp_path / "narrow/layout-error.txt").read_text() == "line_numbers:2"


def test_margin_must_fit_the_actual_number_of_digits(tmp_path):
    # Four millimetres fit the gap and one digit in the locked body font.
    patch = {"margins_mm": [10, 4, 10, 10], "line_numbers": "right"}
    page, = render(tmp_path / "nine", "줄\n\n" * 9, **patch)
    assert any(c.text == "9" for c in glyphs(page))
    with pytest.raises(InputError) as error:
        render(tmp_path / "ten", "줄\n\n" * 10, **patch)
    assert error.value.field == "line_numbers"
    assert (tmp_path / "ten/layout-error.txt").read_text() == "line_numbers:1"


def test_empty_indent_and_memo_are_skipped_but_a_printed_list_mark_counts(tmp_path):
    source = "[가나다]{풀이=기록}\n\n[^검토]\n\n- [^검토]\n\n[^검토]{메모}: 비공개"
    page, = render(tmp_path / "indent", source, page_mm=[50, 100], margins_mm=[10] * 4,
                   first_indent_mm=15, line_numbers="left")
    chars = glyphs(page)
    labels = sorted((c for c in chars if c.x1 < 10 * BP_PER_MM - 1), key=lambda c: -c.baseline)
    assert [c.text for c in labels] == ["1", "2"]
    for label, marker in zip(labels, ("가", "①"), strict=True):
        printed, = (c for c in chars if c.text == marker)
        assert label.baseline == pytest.approx(printed.baseline, abs=.02)


@pytest.mark.parametrize(("odd", "even"), [("right", "left"), ("left", "right"), ("off", "right"), ("left", "off")])
def test_line_numbers_follow_physical_page_and_count_hidden_lines(tmp_path, odd, even):
    source = "\n\n".join("교정 문장" for _ in range(20))
    patch = {"page_mm": [90, 65], "margins_mm": [10, 12, 10, 16], "gutter_mm": 2}
    original = render(tmp_path / "original", source, **patch)
    proof = render(tmp_path / "separate", source, line_numbers={"odd": odd, "even": even}, **patch)
    assert len(proof) >= 3
    number = 0
    for page, (before, after) in enumerate(zip(original, proof, strict=True), 1):
        side = odd if page % 2 else even
        left, right = (18, 78) if page % 2 else (12, 72)
        body, labels = [], defaultdict(list)
        for c in glyphs(after):
            if c.x1 < left * BP_PER_MM or c.x0 > right * BP_PER_MM:
                labels[round(c.baseline, 2)].append(c)
            else:
                body.append(c)
        assert_same_glyphs(glyphs(before), body)
        lines = sorted({round(c.baseline, 2) for c in glyphs(before) if c.text == "교"}, reverse=True)
        assert set(labels) == (set() if side == "off" else set(lines))
        for y in lines:
            number += 1
            if side != "off":
                digits = sorted(labels[y], key=lambda c: c.x0)
                assert "".join(c.text for c in digits) == str(number)
                distance = left * BP_PER_MM - digits[-1].x1 if side == "left" else digits[0].x0 - right * BP_PER_MM
                assert distance == pytest.approx(6 * BP_PER_PT, abs=.03)
    assert number == 20
