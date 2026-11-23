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


def test_memos_are_zero_width_for_article_number_path_and_pin():
    heading = "제[^앞]8조[^뒤] 목적"
    definitions = "\r\n\r\n[^앞]{메모}: 번호 확인\r\n[^뒤]{메모}: 제목 확인\r\n"
    source = "\ufeff### " + heading + "  ###  " + definitions
    pinned = "\ufeff### " + heading + " {번호고정}  ###  " + definitions
    assert toggle_pin(source, 0, 0) == pinned
    assert toggle_pin(pinned, 0, 0) == source
    for suffix, number in [("", "1"), (" {번호고정}", "8")]:
        manuscript = "# " + heading + suffix + definitions
        assert targets(manuscript)[0]["path"] == ["제8조 목적"]
        inlines = numbered(manuscript, "제{숫자}조")
        assert "".join(s["text"] for s in inlines if s["kind"] == "text") == f"제{number}조 목적"
        assert [s["id"] for s in inlines if s["kind"] == "memo_ref"] == ["앞", "뒤"]


@pytest.mark.parametrize(("pinned", "unpinned"), [
    ("제8조 끝  [^주] {번호고정}", "제8조 끝  [^주]"),
    ("제8조 끝 {번호고정}[^주]", "제8조 끝[^주]"),
    ("제8조 끝 {번호[^주]고정} *[^주]*", "제8조 끝[^주] *[^주]*"),
    ("제8조 끝 *{번호고정}[^주]*", "제8조 끝 *[^주]*"),
    ("제8조 끝 {**번호**고정}**[^주]**", "제8조 끝 **[^주]**"),
])
def test_unpin_preserves_memos_and_their_source_markup(pinned, unpinned):
    def manuscript(title):
        return "### " + title + "  ###\r\n\r\n[^주]{메모}: 보존\r"
    assert targets(manuscript(pinned)) == targets(manuscript(unpinned))
    assert toggle_pin(manuscript(pinned), 0, 0) == manuscript(unpinned)
    assert toggle_pin(toggle_pin(manuscript(unpinned), 0, 0), 0, 0) == manuscript(unpinned)


def test_unpin_setext_keeps_trailing_memo_line_and_original_line_endings():
    source = "제8조 목적  \r\n {번호고정}\n  [^주]\r===\r\n\r\n[^주]{메모}: 보존"
    expected = "제8조 목적  \r\n  [^주]\r===\r\n\r\n[^주]{메모}: 보존"
    assert toggle_pin(source, 0, 0) == expected
    assert targets(source) == targets(expected)


@pytest.mark.parametrize("definition", ["[^주]: 각주", "[^주]{미주}: 미주"])
def test_printed_note_still_interrupts_article_matching(definition):
    source = "# 제8조[^주] 제목\n\n" + definition
    with pytest.raises(InputError, match="조 제목"):
        toggle_pin(source, 0, 0)


def test_unpin_setext_keeps_emphasis_closed_on_the_title():
    source = "**제8조 목적  \r\n{번호고정}**\n===\r\n"
    assert toggle_pin(source, 0, 0) == "**제8조 목적**  \r\n===\r\n"


@pytest.mark.parametrize("title", [r"제8조 \*표시\*", r"제8조 \[x\] **강조**[^주]"])
def test_pin_edits_preserve_raw_escaped_punctuation(title):
    source = "### " + title + "  ###\r\n\r\n[^주]{메모}: 원문"
    pinned = "### " + title + " {번호고정}  ###\r\n\r\n[^주]{메모}: 원문"
    assert toggle_pin(source, 0, 0) == pinned
    assert toggle_pin(pinned, 0, 0) == source


def test_pin_roundtrip_preserves_spaces_inside_a_bold_label():
    source = "# 제1조 **제목:  **"
    pinned = toggle_pin(source, 0, 0)
    assert pinned == source + " {번호고정}"
    assert toggle_pin(pinned, 0, 0) == source
