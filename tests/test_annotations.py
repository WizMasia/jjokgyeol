from dataclasses import asdict

import pytest

from app.article_edit import toggle_pin
from app.articles import number_articles
from app.document import Inline, InputError, parse_md
from app.reference_rules import targets


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
