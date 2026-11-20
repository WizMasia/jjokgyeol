"""Measure physical page margins and line breaks in the LuaLaTeX PDF."""
import pytest

from app.document import InputError, parse_md
from app.style import Style, apply_style, from_stored_style
from app.typeset import typeset
from pdf_metrics import read_pages


MM = 72 / 25.4
BASE = {"page_mm": [128, 100], "margins_mm": [15, 10, 15, 12]}


def pages(work, source, **patch):
    work.mkdir()
    style = apply_style(Style(), {**BASE, **patch})
    return read_pages(typeset(parse_md(source), from_stored_style(style.to_stored()), work))


def test_mirrored_text_and_folios(tmp_path):
    rules = [
        {"path": [], "role": "header", "values": {"visible": True, "offset_mm": 3,
                                                   "left": "왼", "center": "중", "right": "끝"}},
        {"path": [], "role": "footer", "values": {"visible": True, "offset_mm": 3}},
        {"path": [], "role": "footer", "face": "odd", "values": {"right": "{현재쪽}"}},
        {"path": [], "role": "footer", "face": "even", "values": {"left": "{현재쪽}"}},
        {"path": [], "role": "footer", "face": "first", "values": {"visible": False}},
    ]
    book = pages(tmp_path / "mirror", "# 제목\n\n" + "본문\n\n" * 35, gutter_mm=6, section_rules=rules)
    assert len(book) >= 3
    for i, page in enumerate(book, 1):
        left, center, right = (18, 68, 118) if i % 2 else (10, 60, 110)
        chars = [c for line in page.lines for c in line.chars]
        header = {c.text: c for c in chars if c.baseline > page.height - 14 * MM}
        assert header.keys() == {"왼", "중", "끝"}
        assert header["왼"].x0 == pytest.approx(left * MM, abs=.04)
        assert (header["중"].x0 + header["중"].x1) / 2 == pytest.approx(center * MM, abs=.04)
        assert header["끝"].x1 == pytest.approx(right * MM, abs=.04)
        body_starts = [c.x0 for c in chars if c.text in ("제", "본")]
        assert body_starts and body_starts == pytest.approx([left * MM] * len(body_starts), abs=.04)
        footer = [c for c in chars if c.baseline < 14 * MM]
        assert "".join(c.text for c in footer) == (str(i) if i > 1 else "")
        if i > 1:
            position = footer[-1].x1 if i % 2 else footer[0].x0
            assert position == pytest.approx((right if i % 2 else left) * MM, abs=.04)


@pytest.mark.parametrize(("patch", "even_left"), [({}, 12), ({"gutter_mm": 0}, 10)],
                         ids=["fixed", "zero-gutter"])
def test_fixed_asymmetric_pages_and_mirrored_zero_after_reopening(tmp_path, patch, even_left):
    book = pages(tmp_path / "reopened", "강물\n\n" * 25, **patch)
    assert len(book) >= 2
    for i, page in enumerate(book, 1):
        start = (12 if i % 2 else even_left) * MM
        assert [line.chars[0].x0 for line in page.lines] == pytest.approx([start] * len(page.lines), abs=.04)


def test_headerless_list_continuations_and_note_only_pages_translate_all_glyphs(tmp_path):
    source = "# 제목\n\n- " + "돌" * 530 + "\n\n    이음[^주]\n\n[^주]: " + "풀이 " * 550
    options = {"page_mm": [128, 76], "list_levels": [{"expr": "[{숫자}]", "indent_mm": 3, "gap_mm": 2}]}
    fixed = pages(tmp_path / "fixed", source, **options, margins_mm=[15, 14, 15, 14])
    mirrored = pages(tmp_path / "mirrored", source, **options, gutter_mm=6)
    assert len(mirrored) == len(fixed)
    assert sum(any("돌" in line.text for line in p.lines) for p in fixed) >= 2
    assert any(all(c.size < 8.1 for line in p.lines for c in line.chars) for p in fixed)
    for i, (before, after) in enumerate(zip(fixed, mirrored, strict=True), 1):
        dx = (4 if i % 2 else -4) * MM
        assert [line.text for line in after.lines] == [line.text for line in before.lines]
        for old, new in zip(before.lines, after.lines, strict=True):
            for a, b in zip(old.chars, new.chars, strict=True):
                assert (b.text, b.font, b.size) == (a.text, a.font, a.size)
                assert (b.baseline, b.y0, b.y1) == pytest.approx((a.baseline, a.y0, a.y1), abs=.002)
                assert (b.x0, b.x1) == pytest.approx((a.x0 + dx, a.x1 + dx), abs=.04)


def test_gutter_changes_line_width_for_body_and_list(tmp_path):
    source = "토" * 78 + "\n\n- " + "풀" * 78
    wide = pages(tmp_path / "wide", source, page_mm=[68, 175], gutter_mm=0)
    narrow = pages(tmp_path / "narrow", source, page_mm=[68, 175], gutter_mm=12)
    for text in ("토", "풀"):
        counts = [[line.text.count(text) for p in book for line in p.lines if text in line.text]
                  for book in (wide, narrow)]
        assert sum(counts[0]) == sum(counts[1]) == 78
        assert max(counts[1]) < max(counts[0])
        assert len(counts[1]) > len(counts[0])


def test_running_text_must_fit_the_width_after_binding(tmp_path):
    options = {"page_mm": [60, 70], "section_rules": [
        {"path": [], "role": "header", "values": {"visible": True, "left": "나" * 11, "font_pt": 10}}]}
    assert len(pages(tmp_path / "wide", "원고", **options, gutter_mm=0)) == 1
    with pytest.raises(InputError) as error:
        pages(tmp_path / "narrow", "원고", **options, gutter_mm=6)
    assert error.value.field == "header" and "1쪽" in error.value.message
