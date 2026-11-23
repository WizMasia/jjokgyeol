"""Exercise the Lua renderer directly with the table block supplied by the parser."""
import pytest

from app.document import Block, Document, Inline, InputError, Table
from app.style import Style, apply_style
from app.typeset import typeset
from pdf_metrics import read_pages


def cell(text, note=None):
    spans = [Inline("text", text)]
    if note:
        spans.append(Inline("note_ref", id=note))
    return spans


def test_split_row_repeats_heading_but_prints_each_footnote_once(tmp_path):
    table = Table("table", [], alignments=["left", "right"], rows=[
        [cell("Date", "head"), cell("Description")],
        [cell("23"), cell("X" * 1500 + "TAIL", "body")],
    ])
    doc = Document([table, Block("paragraph", cell("After", "body"))],
                   {"head": "HeaderNote", "body": "BodyNote"})
    style = apply_style(Style(), {"page_mm": [90, 90], "margins_mm": [10] * 4})
    pages = read_pages(typeset(doc, style, tmp_path))
    texts = ["".join(line.text for line in p.lines).replace(" ", "") for p in pages]
    parts = [s for s in texts if "X" in s]
    assert len(parts) >= 3
    assert all(s.count("Date") == s.count("Description") == 1 for s in parts)
    assert "HeaderNote" in parts[0] and "BodyNote" in parts[-1]
    whole = "".join(texts)
    assert whole.count("X") == 1500
    assert all(whole.count(word) == 1 for word in ("TAIL", "HeaderNote", "BodyNote", "After"))
    for p in pages:
        for line in p.lines:
            for c in line.chars:
                assert 28 <= c.x0 <= c.x1 <= p.width - 28
                assert 27 <= c.baseline <= p.height - 28


@pytest.mark.parametrize(("header", "columns", "marker"), [
    ("W", 70, "table_width"), ("길" * 1200, 1, "table_height"),
])
def test_unplaceable_table_reports_layout_error(tmp_path, header, columns, marker):
    table = Table("table", [], alignments=["left"] * columns,
                  rows=[[cell(header)] * columns, [cell("끝")] * columns])
    style = apply_style(Style(), {"page_mm": [80, 75], "margins_mm": [10] * 4})
    with pytest.raises(InputError):
        typeset(Document([table], {}), style, tmp_path)
    assert (tmp_path / "layout-error.txt").read_text() == marker


def test_wrapped_gloss_color_stays_in_its_cell(tmp_path):
    from app.document import parse_md
    from test_reference_typeset import color, glyphs

    source = "| 해설 | 내용 |\n| --- | --- |\n| [말]{풀이=" + "봄" * 60 + "} | " + "눈" * 50 + " |"
    style = apply_style(Style(), {"page_mm": [85, 80], "margins_mm": [10] * 4,
                                  "text_styles": {"gloss": {"color": "#FF0000"}}})
    chars = glyphs(typeset(parse_md(source), style, tmp_path))
    assert [color(c) for c in chars if c.get_text() == "봄"] == [(1, 0, 0)] * 60
    assert [color(c) for c in chars if c.get_text() == "눈"] == [(0, 0, 0)] * 50


def test_row_notes_follow_their_marks_across_page_breaks(tmp_path):
    from app.document import parse_md

    rows = [f"| R{i} | Data[^f{i}] |" for i in range(9)]
    notes = [f"[^f{i}]: Note{i}" for i in range(9)]
    doc = parse_md("\n".join(["| Alpha | Beta |", "| --- | --- |", *rows, "", *notes]))
    style = apply_style(Style(), {"page_mm": [74, 70], "margins_mm": [12, 10, 12, 10]})
    pages = read_pages(typeset(doc, style, tmp_path))
    for page in pages:
        text = "".join(line.text for line in page.lines)
        assert text.count("Alpha") == 1
        assert "Data" in text
        for i in range(9):
            assert (f"R{i}" in text) == (f"Note{i}" in text)


def test_long_footnotes_start_on_header_and_reference_page(tmp_path):
    from app.document import parse_md

    rows = [f"| D{i} | Z{i} |" for i in range(1, 24)]
    doc = parse_md("\n".join(["| A[^f] | B |", "| --- | --- |", "| 1 | hi[^x] |", *rows, "",
                              "[^f]: " + "Headnote " * 30, "[^x]: " + "Bodynote " * 25]))
    style = apply_style(Style(), {"page_mm": [74, 70], "margins_mm": [12, 10, 12, 10]})
    pages = read_pages(typeset(doc, style, tmp_path))
    texts = ["".join(line.text for line in page.lines) for page in pages]
    assert "hi" in texts[0] and "Headnote" in texts[0]
    assert "".join(texts).count("Headnote") == 30
    assert "".join(texts).count("Bodynote") == 25
    for p in pages:
        titles = [c for l in p.lines for c in l.chars if c.text == "A"]
        assert p.lines and len(titles) <= 1
        assert min(c.baseline for l in p.lines for c in l.chars) >= 33.9


@pytest.mark.parametrize("with_note", [False, True])
def test_near_page_height_row_has_no_empty_or_header_only_page(tmp_path, with_note):
    from app.document import parse_md

    if with_note:
        source = "| A |\n| --- |\n| " + "봄" * 73 + "[^s] |\n\n후속\n\n[^s]: 본문각주"
        page, margins = [75, 65], [10] * 4
    else:
        source = "| A | B |\n| --- | --- |\n| 1 | " + "X" * 74 + " |\n| Done | Y |"
        page, margins = [74, 70], [12, 10, 12, 10]
    style = apply_style(Style(), {"page_mm": page, "margins_mm": margins})
    pages = read_pages(typeset(parse_md(source), style, tmp_path))
    for p in pages:
        text = "".join(line.text for line in p.lines)
        assert p.lines and text.count("A") == int(any(s in text for s in ("봄", "X", "Done")))
        assert min(c.baseline for l in p.lines for c in l.chars) >= margins[2] * 72 / 25.4 - .1
