from dataclasses import asdict
from pathlib import Path

import pytest

from app.article_edit import toggle_pin
from app.articles import number_articles
from app.document import Inline, InputError, parse_md
from app.reference_rules import targets
from test_reference_acceptance import editor, url


def test_definition_sets_type_and_preserves_reference_order():
    doc = parse_md("판형 [판형]{풀이=책의 크기}[^memo-a][^b][^c][^b]\r\n\r\n"
                   "[^c]{메모}: 편집자 의견\r\n[^b]{미주}: 출처 #1\r\n[^memo-a]: 보통 각주")
    assert doc.footnotes == {"memo-a": "보통 각주"}
    assert doc.endnotes == {"b": "출처 #1"}
    assert doc.memos == {"c": "편집자 의견"}
    block, = doc.blocks
    assert block.inlines == [
        Inline("text", text="판형 "), Inline("gloss", text="책의 크기", children=[Inline("text", text="판형")]),
        Inline("note_ref", id="memo-a"), Inline("endnote_ref", id="b"),
        Inline("memo_ref", id="c"), Inline("endnote_ref", id="b"),
    ]


@pytest.mark.parametrize("definition", ["[^a]{미주}: 둘", "[^a]{메모}: 셋"])
def test_duplicate_id_across_kinds(definition):
    with pytest.raises(InputError, match="중복"):
        parse_md("본문[^a]\n\n[^a]: 각주\n" + definition)


def test_annotation_in_title_preserves_path_pin_and_numbering():
    title = "# [제8조]{풀이=조 번호} 기록[^m]"
    source = title + "\n\n[^m]{메모}: 제목 정리"
    assert targets(source) == [{"path": ["제8조 기록"], "count": 1, "level": 1}]
    pinned = toggle_pin(source, 0, 0)
    assert pinned.startswith(title + " {번호고정}")
    assert toggle_pin(pinned, 0, 0) == source
    blocks = [asdict(block) for block in parse_md(source).blocks]
    number_articles(blocks, "제{숫자}조")
    assert blocks[0]["inlines"][0]["children"][0]["text"] == "제1조"


def test_incomplete_gloss_remains_text():
    source = "판형(책의 크기) [판형]{풀이=편집 중"
    assert parse_md(source).blocks[0].inlines == [Inline("text", text=source)]


def test_setext_after_fence_keeps_notes_in_joined_output():
    source = "```\n예시\n```\n[표제]{풀이=제목}[^n][^m]\n===\n\n[^n]{미주}: 출처\n[^m]{메모}: 편집 중"
    doc = parse_md(source)
    assert targets(source) == [{"path": ["표제"], "count": 1, "level": 1}]
    joined, _ = doc.render_layout([{} for _ in doc.blocks], number_articles=False)
    assert [s.kind for b in joined.blocks for s in b.inlines][-3:] == ["gloss", "endnote_ref", "memo_ref"]
    assert joined.endnotes == {"n": "출처"} and joined.memos == {"m": "편집 중"}


def test_gloss_base_is_one_word_and_cannot_hide_pin_syntax():
    source = "# 제8조 [두 단어]{풀이=임시} [{번호고정}]{풀이=기호}"
    assert parse_md(source).blocks[0].inlines == [Inline("text", text=source[2:])]
    assert toggle_pin(toggle_pin(source, 0, 0), 0, 0) == source


def test_gloss_wraps_with_its_target_and_uses_local_size(tmp_path):
    from test_breaks import assert_fits, render, text

    lines = render("판형 판형 [판형]{풀이=책의 크기}\n\n(종이)", tmp_path, 27)
    assert [text(line) for line in lines] == ["판형판형", "판형(책의크기)", "(종이)"]
    assert_fits(lines, 27)
    assert all(c.size == pytest.approx(8.8 * 72 / 72.27, abs=.02)
               for c in lines[1].chars[2:] if not c.text.isspace())
    assert all(c.size == pytest.approx(11 * 72 / 72.27, abs=.02) for c in lines[-1].chars)


def test_oversize_gloss_splits_inside_explanation(tmp_path):
    from test_breaks import assert_fits, render, text

    lines = render("산 강 [새단어]{풀이=가나다라마바사아자차카타파하 아주 긴 풀이}", tmp_path, 18)
    texts = list(map(text, lines))
    assert "".join(texts) == "산강새단어(가나다라마바사아자차카타파하아주긴풀이)"
    assert any("새단어(" in line for line in texts)
    assert len(lines) > 2
    assert_fits(lines, 18)


def test_endnotes_first_use_memo_omitted(tmp_path):
    from app.style import Style
    from app.typeset import typeset
    from pdf_metrics import read_pages

    source = ("본문[^z][^m] 하나[^f] 둘[^a] 셋[^z]\n\n"
              "[^a]{미주}: 나중자료\n[^z]{미주}: 먼저자료\n[^m]{메모}: 편집비공개\n"
              "[^f]: 각주자료\n[^u]{미주}: 미사용자료")
    pages = read_pages(typeset(parse_md(source), Style(), tmp_path))
    printed = "".join("".join(row.text.split()) for p in pages for row in p.lines)
    assert "미1" in printed and "미2" in printed
    assert printed.count("먼저자료") == printed.count("나중자료") == 1
    assert printed.index("먼저자료") < printed.index("나중자료")
    assert "각주자료" in printed and "편집비공개" not in printed and "미사용자료" not in printed
    assert "편집비공개" not in (tmp_path / "input.json").read_text()


def test_reopen_and_reflow(editor, tmp_path):
    from playwright.sync_api import expect
    from pdf_metrics import read_pages
    from test_article_acceptance import save
    from test_list_acceptance import download_proof

    original = (Path(__file__).resolve().parents[1] / "proofs/annotations/manuscript.md").read_bytes()
    original = original.replace(b"\n", b"\r\n")
    control = editor.locator("#file")
    control.set_input_files({"name": "견본.md", "mimeType": "text/markdown", "buffer": original})
    saved = save(editor, "#save")
    assert original == saved
    editor.locator("#source").fill("임시 원고")
    control.set_input_files({"name": "다시.md", "mimeType": "text/markdown", "buffer": saved})
    expect(editor.locator("#source")).to_have_value(original.decode().replace("\r\n", "\n"))
    editor.locator("#style summary").click()
    editor.locator("input[name=page_mm]").nth(1).fill("70")
    for idx in (0, 2):
        editor.locator("input[name=margins_mm]").nth(idx).fill("15")
    placements = []
    for size, leading in ((11, 17), (16, 24)):
        editor.locator("#body_pt").fill(str(size))
        editor.locator("#leading_pt").fill(str(leading))
        pages = read_pages(download_proof(editor, tmp_path / f"annotations-{size}.pdf"))
        rows = [(i, "".join(line.text.split())) for i, p in enumerate(pages) for line in p.lines]
        ref, = [i for i, row in rows if "강변기록" in row]
        note, = [i for i, row in rows if "물길을따라" in row]
        assert ref == note
        placements.append(ref)
        text = "".join(row for _, row in rows)
        assert "판형(책의크기)" in text and "제본전교정지" not in text
        assert text.count("원본대장") == text.count("도서관기록") == 1
        assert text.index("원본대장") < text.index("도서관기록")
        assert save(editor, "#save") == original
    assert placements[1] > placements[0]


def test_gloss_with_small_section_and_repeated_endnote_style(tmp_path):
    from app.style import Style, apply_style
    from app.typeset import typeset
    from pdf_metrics import read_pages

    patch = {"text_styles": {"gloss": {"font_pt": 10, "font": "nanum-gothic", "weight": "bold"},
                             "note": {"font_pt": 9, "font": "nanum-gothic"}},
             "section_rules": [{"path": ["문"], "role": "body", "values": {"font_pt": 9}}]}
    source = "[판]{풀이=큰}[^b]\n\n# 문\n\n[달]{풀이=작}[^b]\n\n[^b]{미주}: 밤하늘"
    pages = read_pages(typeset(parse_md(source), apply_style(Style(), patch), tmp_path))
    glyphs = [c for p in pages for line in p.lines for c in line.chars]
    for text, points, face in (("큰", 10, "NanumGothicBold"), ("작", 7.2, "NanumGothicBold"),
                               ("밤", 9, "NanumGothic")):
        glyph, = [c for c in glyphs if c.text == text]
        assert glyph.size == pytest.approx(points * 72 / 72.27, abs=.02)
        assert glyph.font.split("+")[-1] == face


@pytest.mark.parametrize("term,width", [("가나다라마바사아자차", 19), ("지도", 8)])
def test_gloss_in_very_narrow_measure(tmp_path, term, width):
    from test_breaks import assert_fits, render, text

    lines = render(f"[{term}]{{풀이=원본자료}}", tmp_path, width)
    assert "".join(map(text, lines)) == term + "(원본자료)"
    assert_fits(lines, width)
