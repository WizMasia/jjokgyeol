"""Exercise annotation placement on the shaped lines of the rendered PDF."""
import pytest

from app.document import parse_md
from app.style import Style, apply_style
from app.typeset import typeset
from pdf_metrics import read_pages
from test_breaks import BP_PER_MM, assert_fits, render, text


@pytest.mark.parametrize(("source", "width", "groups", "printed"), [
    ("앞말 [판형]{풀이=크기}입니다", 20, ["판형(크기)"], "앞말판형(크기)입니다"),
    ("ABCDE[Book]{풀이=size}binding", 26, ["Book(size)"], "ABCDEBook(size)binding"),
    ("국제[판형]{풀이=크기}[규격]{풀이=표준}", 20, ["판형(크기)", "규격(표준)"], "국제판형(크기)규격(표준)"),
    ("가나다“[판]{풀이=책}”.이다", 20, ["“판(책)”."], "가나다“판(책)”.이다"),
])
def test_gloss_in_attached_text_stays_on_one_line(tmp_path, source, width, groups, printed):
    lines = render(source, tmp_path, width)
    rows = list(map(text, lines))
    assert "".join(rows) == printed
    for group in groups:
        assert any(group in row for row in rows), rows
    assert_fits(lines, width)


def test_gloss_uses_space_before_attached_suffix_wraps(tmp_path):
    lines = render("앞말 [판형]{풀이=크기}이다", tmp_path, 27)
    assert list(map(text, lines)) == ["앞말판형(크기)", "이다"]
    assert_fits(lines, 27)


def test_first_gloss_moves_to_wider_continuation(tmp_path):
    lines = render("[가나다]{풀이=기록}", tmp_path, 30, first_indent_mm=15)
    assert list(map(text, lines)) == ["가나다(기록)"]
    assert lines[0].chars[0].x0 == pytest.approx(10 * BP_PER_MM, abs=.05)
    assert_fits(lines, 30)


@pytest.mark.parametrize("layout", [{}, {"paragraph_layout": "balanced"}], ids=["legacy", "balanced"])
def test_heading_follows_gloss_to_next_page(tmp_path, layout):
    source = "도입\n\n도입\n\n# 표제\n\n[판형]{풀이=책의 크기}"
    style = apply_style(Style(), {"page_mm": [55, 48], "margins_mm": [10] * 4,
                                  "first_indent_mm": 23, **layout})
    pages = read_pages(typeset(parse_md(source), style, tmp_path))
    assert [list(map(text, page.lines)) for page in pages] == [["도입", "도입"], ["표제", "판형(책의크기)"]]


def test_gloss_fallback_keeps_quotes_across_literal_spaces(tmp_path):
    lines = render("한강   “  여름  ”   끝 [산]{풀이=뫼}", tmp_path, 17, paragraph_layout="balanced")
    assert list(map(text, lines)) == ["한강", "“여름”", "끝산(뫼)"]
    assert_fits(lines, 17)
    quote, first, last, close = (c for c in lines[1].chars if not c.text.isspace())
    for left, right in [(quote, first), (last, close)]:
        assert right.x0 - left.x0 - left.advance == pytest.approx(2 * left.size * 307 / 1024, abs=.04)


def test_list_mark_follows_first_gloss_to_wider_line(tmp_path):
    patch = {"section_rules": [{"path": [], "role": "list", "values": {"first_indent_mm": 19}}]}
    lines = render("- [가나다]{풀이=기록}", tmp_path, 40, **patch)
    assert list(map(text, lines)) == ["①가나다(기록)"]
    assert_fits(lines, 40)


@pytest.mark.parametrize(("source", "width", "style", "rows"), [
    ("[가]{풀이=책.}", 9.5, {}, ["가", "(책.)"]),
    ("[지도]{풀이=원본자료}", 8, {"gloss": {"color": "#000000"}}, ["지도", "(원본", "자료)"]),
])
def test_oversized_gloss_breaks_at_legal_glyphs(tmp_path, source, width, style, rows):
    lines = render(source, tmp_path, width, text_styles=style)
    assert list(map(text, lines)) == rows
    assert_fits(lines, width)


@pytest.mark.parametrize("refs", ["[^검토]", "[^검토] [^검토]"])
def test_memo_only_manuscript_renders_one_empty_page(tmp_path, refs):
    doc = parse_md(refs + "\n\n[^검토]{메모}: 교정지")
    pages = read_pages(typeset(doc, Style(), tmp_path))
    assert len(pages) == 1 and not pages[0].lines
    data = (tmp_path / "input.json").read_text()
    assert "교정지" not in data and "검토" not in data


def test_memo_after_article_number_does_not_change_numbering(tmp_path):
    source = "# 제8조[^검토] 제목\n\n# 제9조 다음\n\n[^검토]{메모}: 교정지"
    lines = render(source, tmp_path, 100, article_expr="제{숫자}조")
    assert list(map(text, lines)) == ["제1조제목", "제2조다음"]
