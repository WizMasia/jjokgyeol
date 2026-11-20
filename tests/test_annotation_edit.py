from dataclasses import asdict

import pytest

from app.article_edit import toggle_pin
from app.articles import number_articles
from app.document import Inline, InputError, parse_md
from app.reference_rules import targets


def numbered(source, expr):
    doc = parse_md(source)
    blocks = [asdict(block) for block in doc.blocks]
    number_articles(blocks, expr, doc.fenced_blocks)
    return blocks[-1]["inlines"]


def test_gloss_inside_pin_spelling_is_title_text():
    title = "제8조 목적 {**[번호고정]{풀이=용어}**}"
    source = "\ufeff### " + title + "  ###  \r\n본문\r"
    pinned = "\ufeff### " + title + " {번호고정}  ###  \r\n본문\r"
    assert toggle_pin(source, 0, 0) == pinned
    assert toggle_pin(pinned, 0, 0) == source
    for suffix in ("", " {번호고정}"):
        assert targets("# " + title + suffix)[0]["path"] == ["제8조 목적 {번호고정}"]
    spans = numbered("# " + title, "제{숫자}조")
    assert spans[0]["text"] == "제1조 목적 {"
    assert spans[1]["children"][0]["children"][0]["text"] == "번호고정"
    assert spans[-1]["text"] == "}"


def test_fenced_setext_title_only_splits_for_a_real_pin():
    source = "~~~\r\n예시\r\n~~~\r\n제8조 {번호[고정]{풀이=표시}}\r\n===\r\n"
    doc = parse_md(source)
    joined, _ = doc.render_layout([{} for _ in doc.blocks], number_articles=False)
    assert len(joined.blocks) < len(doc.blocks)
    pinned = toggle_pin(source, 3, 4)
    assert toggle_pin(pinned, 3, 4) == source
    doc = parse_md(pinned)
    split, _ = doc.render_layout([{} for _ in doc.blocks], number_articles=False)
    assert split.blocks[-1].inlines == doc.blocks[-1].inlines


@pytest.mark.parametrize("draft", ["[말]{풀이=[^초안]", "[말]{풀이=**임시**"])
def test_unfinished_explanation_is_literal_on_its_own_line(draft):
    source = draft + "\r\n다음[^자료]\r\n\r\n[^자료]: 보통"
    assert parse_md(source).blocks[0].inlines == [
        Inline("text", text=draft + " 다음"), Inline("note_ref", id="자료"),
    ]
    with pytest.raises(InputError, match="정의"):
        parse_md(draft + "\n다음[^없음]")


@pytest.mark.parametrize(("expr", "before", "ordinal", "after"), [
    ("§{숫자}", "§", "1", " 목적"),
    ("1.{{조}}{한글}/{숫자}", "1.{조}", "가", "/1 목적"),
    ("{원숫자}항", "", "①", "항 목적"),
])
def test_custom_label_keeps_the_ordinal_in_its_original_gloss(expr, before, ordinal, after):
    spans = numbered("# 제[8]{풀이=숫자}조 목적", expr)
    assert spans[0]["text"] == before
    assert spans[1] == asdict(Inline("gloss", text="숫자", children=[Inline("text", text=ordinal)]))
    assert spans[-1]["text"] == after


def test_pinning_cannot_close_a_draft_gloss_in_the_manuscript():
    source = "# 제8조 [말]{풀이=아직 [^초안]  ###\r\n본문"
    with pytest.raises(InputError, match="풀이"):
        toggle_pin(source, 0, 0)


def test_custom_label_keeps_gloss_on_significant_digit_after_leading_zero():
    spans = numbered("# 제0[8]{풀이=순번}조 목적", "§{숫자}")
    assert spans[0]["text"] == "§"
    assert spans[1] == asdict(Inline("gloss", text="순번", children=[Inline("text", text="1")]))


def test_hidden_number_has_no_orphan_explanation():
    source = "# 제[8]{풀이=순번}조 목적"
    spans = numbered(source, "")
    assert all(span["kind"] == "text" for span in spans)
    assert "".join(span["text"] for span in spans) == " 목적"


@pytest.mark.parametrize(("source", "message"), [
    ("[^memo-누락]\n\n[^별개]{미주}: 자료", "각주 참조에 해당하는 정의가 없습니다."),
    ("[^end-주]: 하나\n[^end-주]: 둘", "각주 정의가 중복되었습니다."),
    ("[^주]: 하나\n[^주]{미주}: 둘", "주석 정의가 중복되었습니다."),
    ("[^주]{메모}: 하나\n[^주]: 둘", "주석 정의가 중복되었습니다."),
])
def test_note_errors_preserve_editor_messages(source, message):
    with pytest.raises(InputError) as caught:
        parse_md(source)
    assert caught.value.errors == {"source": message}
