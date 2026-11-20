"""Read line ends from real PDFs using the shipping font and paragraph callback."""
import unicodedata

import pytest

from app.document import parse_md
from app.style import Style, apply_style
from app.typeset import typeset
from pdf_metrics import read_pages

BP_PER_MM = 72 / 25.4


def render(source, work, width, **patch):
    style = apply_style(Style(), {
        "page_mm": [width + 20, 100], "margins_mm": [10, 10, 10, 10], **patch,
    })
    return [line for page in read_pages(typeset(parse_md(source), style, work))
            for line in page.lines]


def text(line):
    return unicodedata.normalize("NFC", "".join(line.text.split()))


def assert_fits(lines, width):
    for line in lines:
        last = line.chars[-1]
        assert last.x0 + last.advance <= (10 + width) * BP_PER_MM + 0.05


def test_ordinary_word_moves_whole(tmp_path):
    lines = render("한강 가나다라 서울", tmp_path, 18)
    assert [text(line) for line in lines] == ["한강", "가나다라", "서울"]


@pytest.mark.parametrize("source", [
    "ABCDEFGHIJKLMNO" * 5,
    "https://example.test/chapters/volume/12345678901234567890",
    "가나(다라마바사)아자차카타파하." * 3,
    unicodedata.normalize("NFD", "가각간") * 8,
])
def test_long_cluster_safe_word(tmp_path, source):
    lines = render(source, tmp_path, 22)
    assert len(lines) > 1
    assert_fits(lines, 22)
    assert "".join(map(text, lines)) == unicodedata.normalize("NFC", source)
    for line in lines:
        assert not text(line).endswith("(")
        assert not text(line).startswith((")", "."))
        # No line begins with the medial or final jamo of a Hangul syllable.
        assert not 0x1160 <= ord(text(line)[0]) <= 0x11FF


@pytest.mark.parametrize(("first", "rest", "first_line"), [(8, 0, "강"), (0, 8, "강가나다라")])
def test_first_and_continuation_width(tmp_path, first, rest, first_line):
    lines = render("강 가나다라 가나다라", tmp_path, 22,
                   first_indent_mm=first, rest_indent_mm=rest)
    assert text(lines[0]) == first_line
    assert "".join(map(text, lines)) == "강가나다라가나다라"
    assert_fits(lines, 22)
    for n, line in enumerate(lines):
        assert line.chars[0].x0 == pytest.approx((10 + (first if n == 0 else rest)) * BP_PER_MM, abs=.05)
    if first:
        assert text(lines[1]) == "가나다라"
    else:
        assert len(lines) >= 3


def test_heading_uses_its_own_width_and_footnote_stays_attached(tmp_path):
    lines = render("# ABCDEFGHIJKLMNOPQRSTUVWX\n\n가나 다람쥐[^n]\n\n[^n]: 각주 본문", tmp_path, 23)
    assert_fits(lines, 23)
    assert "".join(map(text, lines)).startswith("ABCDEFGHIJKLMNOPQRSTUVWX")
    assert any("다람쥐1" in text(line) for line in lines)
    assert sum("각주본문" in text(line) for line in lines) == 1


def test_first_word_does_not_leave_indented_line_blank(tmp_path):
    lines = render("가나다라마", tmp_path, 25, first_indent_mm=14)
    assert len(lines) == 2
    assert lines[0].chars[0].x0 == pytest.approx(24 * BP_PER_MM, abs=.05)
    assert "".join(map(text, lines)) == "가나다라마"
    assert_fits(lines, 25)


def test_unplaceable_cluster_is_a_local_width_error(tmp_path):
    import subprocess

    with pytest.raises((subprocess.CalledProcessError, ValueError)):
        render("가", tmp_path, 2)
    assert (tmp_path / "layout-error.txt").read_bytes() == b"line_width"


def test_keep_standalone_punctuation_with_its_word(tmp_path):
    lines = render("한강 ( 여름 )", tmp_path, 13)
    assert "".join(map(text, lines)) == "한강(여름)"
    assert all(not text(line).startswith(")") and not text(line).endswith("(") for line in lines)
    assert_fits(lines, 13)


def test_continuation_indent_survives_page_break(tmp_path):
    style = apply_style(Style(), {
        "page_mm": [48, 48], "margins_mm": [7, 8, 7, 8], "rest_indent_mm": 9,
    })
    pages = read_pages(typeset(parse_md(" ".join(["가나다라"] * 20)), style, tmp_path))
    assert len(pages) >= 2
    assert pages[0].lines[0].chars[0].x0 == pytest.approx(8 * BP_PER_MM, abs=.05)
    for page in pages[1:]:
        assert page.lines[0].chars[0].x0 == pytest.approx(17 * BP_PER_MM, abs=.05)


def test_explicit_tex_line_break(tmp_path, monkeypatch):
    import importlib
    import shutil

    engine = importlib.import_module("app.typeset")
    templates = tmp_path / "tex"
    shutil.copytree(engine.TEMPLATES, templates)
    book = templates / "book.tex"
    book.write_text(book.read_text().replace(
        r"\directlua{book.document()}", r"\noindent 첫줄\newline 둘째줄\par",
    ))
    monkeypatch.setattr(engine, "TEMPLATES", templates)
    work = tmp_path / "job"
    work.mkdir()
    assert [text(line) for line in render("", work, 80)] == ["첫줄", "둘째줄"]
