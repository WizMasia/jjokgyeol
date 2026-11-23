import pytest

from app.article_edit import toggle_pin
from app.document import InputError, heading_tokens, parse_md
from app.paragraph_edit import edit_alignment


LEFT = "<!-- 쪽결 정렬=왼쪽 -->"
CENTER = "<!-- 쪽결 정렬=가운데 -->"
RIGHT = "<!-- 쪽결 정렬=오른쪽 -->"


def edit(source, first=0, last=None, **change):
    return edit_alignment(dict(source=source, first_line=first, last_line=first if last is None else last, **change))


def shown(block):
    return "".join(node.text for node in block.inlines if node.kind == "text")


def test_parser_attaches_only_the_last_marker_to_date_and_setext_heading():
    source = f"앞 문단\n{LEFT}\n{RIGHT}\n2026. 11. 23.  현장 점검팀\n둘째 줄\n{CENTER}\n제2조 목적[^m]\n---\n[^m]{{메모}}: 검토"
    doc = parse_md(source)
    assert [(b.kind, b.align, shown(b)) for b in doc.blocks] == [
        ("paragraph", None, "앞 문단"), ("paragraph", "right", "2026. 11. 23.  현장 점검팀 둘째 줄"),
        ("heading", "center", "제2조 목적")]
    assert doc.blocks[-1].level == 2 and doc.memos == {"m": "검토"}
    (head, inline), = heading_tokens(source)
    assert head.map == [6, 8] and inline.map == [6, 7]
    assert toggle_pin(source, 7, 7) == source.replace("목적[^m]", "목적[^m] {번호고정}")


def test_duplicate_marker_query_change_and_reset_preserve_physical_source():
    source = f"앞 문단\r{LEFT}\r\n{RIGHT}\n다음 문단\r\n뒷줄\r끝"
    assert edit(source, 1, 5) == dict(source=source, align="right", supported=True, message="")
    assert edit(source, 2, align="right")["source"] == source
    changed = edit(source, 4, align="center")
    assert changed == dict(source=f"앞 문단\r{CENTER}\n다음 문단\r\n뒷줄\r끝",
                           align="center", supported=True, message="")
    reset = edit(changed["source"], 1, align=None)["source"]
    assert reset.splitlines() == ["앞 문단", "", "다음 문단", "뒷줄", "끝"]
    assert reset.startswith("앞 문단\r") and reset.endswith("다음 문단\r\n뒷줄\r끝")


@pytest.mark.parametrize(("source", "row", "marker_row"), [
    ("\ufeff# 첫 제목\r\n\n끝", 0, 0), ("\ufeff두 줄\r계속\n\r\n끝", 1, 0),
    ("- 목록\n# 제목", 1, 1), ("> 인용\n# 제목", 1, 1),
    ("~~~\n예시\n~~~\n실제 제목\n---", 4, 3),
    ("들머리\r\n\r\n끝", 2, 2), ("한 줄", 0, 0),
])
def test_set_query_reset_round_trip(source, row, marker_row):
    result = edit(source, row, align="left")
    assert result["supported"] and result["align"] == "left"
    marked = result["source"]
    assert marked.startswith("\ufeff") == source.startswith("\ufeff")
    assert marked.splitlines()[marker_row].removeprefix("\ufeff") == LEFT
    assert edit(marked, marker_row, row + 1)["align"] == "left"
    assert [b.align for b in parse_md(marked).blocks].count("left") == 1
    assert edit(marked, marker_row, align=None)["source"] == source


@pytest.mark.parametrize(("source", "first", "last"), [
    ("", 0, 0), ("문단\n", 1, 1), ("하나\n\n둘", 0, 2), ("본문\n# 제목", 0, 1),
    (f"- 항목\n{CENTER}\n이어짐", 1, 2), (f"> 인용\n{CENTER}\n이어짐", 2, 2),
    (f"~~~~\n```\n{CENTER}\n# 예시\n~~~~\n본문", 2, 3),
    (f"    {CENTER}\n    # 예시", 1, 1), ("\t코드", 0, 0),
    (f"{CENTER}\nA | B\n-- | --\n{RIGHT} | 예시", 1, 3),
    (f"주[^a]\n{CENTER}\n[^a]: 각주", 2, 2),
    (f"주[^a]\n{CENTER}\n[^a]{{미주}}: 미주", 2, 2),
    (f"주[^a]\n{CENTER}\n[^a]{{메모}}: 메모", 2, 2),
])
def test_ineligible_selection_is_a_reasoned_noop(source, first, last):
    result = edit(source, first, last, align="right")
    assert not result["supported"] and result["message"]
    assert result["source"] == source and result["align"] is None
    assert all(b.align is None for b in parse_md(source).blocks)


@pytest.mark.parametrize("source", [
    f"{RIGHT}", f"{RIGHT}\n\n본문", "<!-- 쪽결 정렬=위 -->\n글",
    f" {RIGHT}\n글", f"{RIGHT} \n글", f"- 목록\n{RIGHT}\n이음",
    f"> 인용\n{RIGHT}\n이음", f"```md\n{RIGHT}\n본문\n```",
    f"    ~~~\n    {RIGHT}\n    ~~~", f"{RIGHT}\n- 목록",
    f"{RIGHT}\nA | B\n-- | --\n{LEFT} | 값", f"{RIGHT}\n[^m]{{메모}}: 내용",
])
def test_inactive_markers_remain_literal(source):
    blocks = parse_md(source).blocks
    assert "쪽결 정렬" in " ".join(map(shown, blocks))
    assert all(b.align is None for b in blocks)


def test_marked_block_stops_at_a_quote_or_fence_without_changing_its_example():
    source = f"{RIGHT}\n본문\n~~~\n{LEFT}\n예시\n~~~\n\n{CENTER}\n다음\n> 인용"
    assert [(b.align, shown(b)) for b in parse_md(source).blocks] == [
        ("right", "본문"), (None, f"~~~ {LEFT} 예시 ~~~"), ("center", "다음"), (None, "> 인용")]


def test_fence_title_and_list_reparses_keep_alignment_and_original_rows():
    source = f"~~~\n예시\n~~~\n제8조 목적\n===\n\n{RIGHT}\n작성자\n\n~~~\n7. 예시\n~~~\n- 항목\n\n{CENTER}\n# 제목"
    doc = parse_md(source)
    selected = doc.hierarchy_layout(lambda view: [i for i, b in enumerate(view.blocks) if b.kind == "list_item"])
    for view in (doc, selected):
        printed, _ = view.render_layout([{} for _ in view.blocks], number_articles=False,
                                        running_ids=list(range(len(view.blocks))))
        assert [(shown(b), b.align) for b in printed.blocks if b.align] == [("작성자", "right"), ("제목", "center")]


@pytest.mark.parametrize("updates", [
    {"source": None}, {"source": "\0"}, {"first_line": True}, {"last_line": 0.0},
    {"first_line": -1}, {"first_line": 1}, {"last_line": 1},
    {"align": "justify"}, {"align": False}, {"align": []}, {"other": 7},
])
def test_malformed_request_raises_input_error(updates):
    with pytest.raises(InputError):
        edit_alignment(dict({"source": "글", "first_line": 0, "last_line": 0}, **updates))


@pytest.mark.parametrize("source", [RIGHT, f"{RIGHT}\n7. 항목", f"{RIGHT}\n    코드", f"{RIGHT}\nA|B\n--|--"])
def test_a_literal_unbound_marker_cannot_be_claimed_by_another_marker(source):
    assert not edit(source, 0, align="left")["supported"]


def test_invalid_marker_before_numbered_list_cannot_hide_later_indented_prose():
    source = f"앞\n{LEFT}\n7. 그대로\n\n   {RIGHT}\n글"
    changed = edit(source, 5, align="center")["source"]
    assert changed == source.replace("   " + RIGHT, CENTER + "\n   " + RIGHT)
    first, second = parse_md(changed).blocks
    assert first.align is None and LEFT in shown(first)
    assert second.align == "center" and RIGHT in shown(second)
    assert edit(changed, 4, align=None)["source"] == source


def test_indented_fence_spelling_cannot_hide_an_aligned_article():
    source = f"    ~~~\n\n{RIGHT}\n# 제8조 목적"
    doc = parse_md(source)
    assert doc.blocks[-1].align == "right" and 1 in doc.alignment_blocks()
    assert toggle_pin(source, 3, 3) == source + " {번호고정}"


@pytest.mark.parametrize("indent", [" ", "   ", "\t", "    "])
def test_indented_lookalikes_are_preserved_when_editing_the_next_paragraph(indent):
    source = f"{indent}{LEFT}\n\n다음"
    assert edit(source, 2, align="right")["source"] == f"{indent}{LEFT}\n\n{RIGHT}\n다음"
    assert parse_md(source).blocks[0].align is None


def test_marked_prose_after_indented_code_and_a_legacy_list_starts_at_its_own_row():
    source = f"\t코드\n===\n7. 이음\n{RIGHT}\n   보통 글"
    assert [(shown(b), b.align) for b in parse_md(source).blocks][-1] == ("보통 글", "right")


def test_role_alignment_matches_editable_source_and_keeps_existing_fence_classification():
    source = "Plain Text\n\n    CODE\n\n> QUOTE\nlazy quote\n\n```text\nFENCED\n```\n\n# Title"
    doc = parse_md(source)
    assert [shown(b) for b in doc.blocks] == ["Plain Text", "CODE", "> QUOTE lazy quote", "```text FENCED ```", "Title"]
    assert doc.fenced_blocks == {3}
    assert doc.alignment_blocks() == {0, 4}
    assert [i for i in range(12) if edit(source, i)["supported"]] == [0, 11]


def test_role_alignment_does_not_reposition_a_legacy_joined_quote():
    assert parse_md("본문\n> 인용\n계속").alignment_blocks() == set()


def test_reparsed_role_eligibility_tracks_current_indices():
    source = "~~~\n예시\n~~~\n제목\n===\n\n    코드\n\n본문"
    doc = parse_md(source)
    assert doc.alignment_blocks() == {1, 3}
    printed, _ = doc.render_layout([{} for _ in doc.blocks], number_articles=False)
    assert printed.alignment_blocks() == {2}


def test_a_legacy_fence_title_split_keeps_the_editable_setext_range_intact():
    source = f"    ~~~\n{RIGHT}\n제8조 목적\n    ~~~\n계속\n---"
    doc = parse_md(source)
    assert doc.alignment_blocks() == {1}
    head, = (b for b in doc.blocks if b.align)
    assert head.kind == "heading" and shown(head).startswith("제8조 목적")
    (heading, _), = heading_tokens(source)
    assert heading.map == [2, 6]


def test_bom_in_an_unmarked_manuscript_does_not_make_literal_code_eligible():
    source = "\ufeff    코드\n\n본문"
    assert not edit(source, 0)["supported"]
    assert parse_md(source).alignment_blocks() == {1}


def test_an_inactive_marker_preserves_the_legacy_first_block_with_bom():
    prefix = "\ufeff# 제목\n\n~~~\n"
    plain = parse_md(prefix + "예시\n~~~")
    literal = parse_md(prefix + CENTER + "\n~~~")
    assert literal.blocks[0] == plain.blocks[0]


def test_marked_article_after_indented_fake_fence_prints_number_without_the_pin(tmp_path):
    from app.style import Style, apply_style
    from app.typeset import typeset
    from pdf_metrics import read_pages

    source = f"    ~~~\n\n{RIGHT}\n# 제8조 목적"
    style = apply_style(Style(), {"article_expr": "제{숫자}조"})
    for name, manuscript, expected in [("auto", source, "제1조목적"), ("pin", toggle_pin(source, 3, 3), "제8조목적")]:
        work = tmp_path / name
        work.mkdir()
        pdf = typeset(parse_md(manuscript), style, work)
        text = "".join(line.text.replace(" ", "") for page in read_pages(pdf) for line in page.lines)
        assert expected in text and "{번호고정}" not in text


def test_inherited_layout_splits_only_the_requested_role_from_literal_content():
    source = "본문\n> 인용\n계속\n\n~~~\n예시\n~~~\n제목\n==="
    doc = parse_md(source)
    assert doc.alignment_layout(set()) is doc
    assert doc.alignment_layout({"heading5"}) is doc
    body = doc.alignment_layout({"body"})
    assert [shown(b) for b in body.blocks[:2]] == ["본문", "> 인용 계속"]
    assert body.alignment_blocks() == {0, 3}
    printed, _ = body.render_layout([{} for _ in body.blocks], number_articles=False)
    assert [shown(b) for b in printed.blocks] == ["본문", "> 인용 계속", "~~~ 예시 ~~~ 제목"]
    assert printed.alignment_blocks() == {0}


def test_marker_before_a_list_continuation_stays_literal_in_the_printed_document():
    source = f"- first\n\n{RIGHT}\n  continuation"
    for row in [2, 3]:
        assert not edit(source, row, align="left")["supported"]
    assert [shown(b) for b in parse_md(source).blocks] == ["first", RIGHT + "   continuation"]


def test_inherited_heading_role_matches_direct_edit_after_bom():
    source = "\ufeff# 제목"
    legacy = parse_md(source)
    assert legacy.blocks[0].kind == "paragraph"  # Preserve the unmarked renderer.
    assert legacy.alignment_layout({"body"}).alignment_blocks() == set()
    heading = legacy.alignment_layout({"heading1"})
    assert heading.blocks[0].kind == "heading" and heading.alignment_blocks() == {0}
