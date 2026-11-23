import pytest

from app.article_edit import toggle_pin
from app.document import Block, Inline, heading_tokens, parse_md


def cell(text):
    return [Inline("text", text=text)]


def test_table_contract_keeps_inline_markup_alignment_and_empty_cells():
    source = (r"| **날짜** | 가운데 | 끝 | 비고 |" "\n"
              "| :-- | :--: | --: | -- |\n"
              r"| 11월 | *가\|나* | | [^m] |" "\n"
              "| | 둘 |\n\n[^m]{메모}: 확인")
    doc = parse_md(source)
    table, = doc.blocks
    assert table.kind == "table" and table.inlines == []
    assert table.alignments == ["left", "center", "right", "left"]
    assert table.rows == [
        [[Inline("strong", children=cell("날짜"))], cell("가운데"), cell("끝"), cell("비고")],
        [cell("11월"), [Inline("emphasis", children=cell("가|나"))], [], [Inline("memo_ref", id="m")]],
        [[], cell("둘"), [], []],
    ]


@pytest.mark.parametrize("text", [
    "일반 문장의 가 | 나",
    "가 | 나\n--- | --- | ---\n값 | 값",
    "| 가 | 나 |\n| --- | 잘못 |\n| 갑 | 을 |",
])
def test_malformed_tables_and_pipes_remain_literal(text):
    assert parse_md(text).blocks == [Block("paragraph", cell(text.replace("\n", " ")))]


def test_table_ends_before_prose_and_preserves_original_heading_edit_ranges():
    source = ("소개\r\n가 | 나\r\n-- | --\r\n1 | 2\r\n"
              "다음 문장\r\n# 제8조 목적  ##\r\n| 끝 |\r\n| -- |\r\n마침\r\n===\r\n")
    blocks = parse_md(source).blocks
    assert [b.kind for b in blocks] == ["paragraph", "table", "paragraph", "heading", "table", "heading"]
    assert blocks[1].rows == [[cell("가"), cell("나")], [cell("1"), cell("2")]]
    assert blocks[4].rows == [[cell("끝")]]
    assert [b.inlines for b in blocks if b.kind != "table"] == list(map(cell, ["소개", "다음 문장", "제8조 목적", "마침"]))
    assert [(head.map, inline.content) for head, inline in heading_tokens(source)] == [
        ([5, 6], "제8조 목적"), ([8, 10], "마침")]
    assert toggle_pin(source, 5, 5) == source.replace("목적  ##", "목적 {번호고정}  ##")


def test_surplus_cells_remain_visible_after_the_last_valid_row():
    source = "| A | B |\n| -- | -- |\n1 | 2\n3 | 4 | extra\n# End"
    table, overflow, end = parse_md(source).blocks
    assert table.rows == [[cell("A"), cell("B")], [cell("1"), cell("2")]]
    assert overflow == Block("paragraph", cell("3 | 4 | extra"))
    assert end == Block("heading", cell("End"), 1)


def test_table_at_nested_item_start_preserves_parent_and_sibling_ordinals():
    source = "7. 부모\n   - A | B\n     -- | --\n     x | y\n     이음\n   - 형제\n8. 다음"
    parent, child, table, continued, sibling, last = parse_md(source).blocks
    assert (parent.ordinal, child.first, child.inlines, table.kind) == (7, True, [], "table")
    assert table.rows == [[cell("A"), cell("B")], [cell("x"), cell("y")]]
    assert (continued.group, continued.ordinal, continued.first, continued.inlines) == (
        child.group, 1, False, cell("이음"))
    assert (sibling.group, sibling.ordinal, sibling.first) == (child.group, 2, True)
    assert child.parent_group == parent.group == last.group and last.ordinal == 8


@pytest.mark.parametrize("fence", ["````", "~~~"])
def test_fenced_table_examples_keep_legacy_layout_and_table_source_rows(fence):
    source = (f"{fence}md\n| F | G |\n| -- | -- |\n| x | y |\n{fence}\n"
              "제목\n===\n| A | B |\n| -- | -- |\n| 1 | 2 |\n"
              f"{fence}\n| C | D |\n| -- | -- |\n{fence}")
    doc = parse_md(source)
    example, title, table, after = doc.blocks
    assert example == Block("paragraph", cell(f"{fence}md | F | G | | -- | -- | | x | y | {fence}"))
    assert title == Block("heading", cell("제목"), 1)
    assert table.rows == [[cell("A"), cell("B")], [cell("1"), cell("2")]]
    assert after == Block("paragraph", cell(f"{fence} | C | D | | -- | -- | {fence}"))
    assert doc.fenced_blocks == {0, 3}
    joined, styles = doc.render_layout([{}, {}, {"body_pt": 12}, {}], number_articles=False)
    assert [block.kind for block in joined.blocks] == ["heading", "table", "paragraph"]
    assert joined.blocks[1] == table and styles == [{}, {"body_pt": 12}, {}]


def test_list_fence_reparse_keeps_examples_literal_with_a_real_table_in_the_item():
    source = ("- 앞\n  ~~~\n  A | B\n  -- | --\n  ~~~\n"
              "  C | D\n  -- | --\n  1 | 2\n- 뒤")
    doc = parse_md(source)
    view = doc.hierarchy_layout(lambda d: [i for i, b in enumerate(d.blocks) if b.kind == "list_item"])
    parent, example, table, sibling = view.blocks
    assert parent.inlines == cell("앞")
    assert example.inlines == cell("~~~ A | B -- | -- ~~~")
    assert table.rows == [[cell("C"), cell("D")], [cell("1"), cell("2")]]
    assert view.fenced_blocks == {1} and sibling.ordinal == 2


def test_outdented_table_ends_list_continuation_without_a_blank_line():
    source = "- 부모\n  - 자식\nA | B\n-- | --\nx | y\n- 새 목록"
    parent, child, table, last = parse_md(source).blocks
    assert [parent.inlines, child.inlines, last.inlines] == list(map(cell, ["부모", "자식", "새 목록"]))
    assert table.rows == [[cell("A"), cell("B")], [cell("x"), cell("y")]]
    assert last.group != parent.group and last.first


def test_only_escaped_pipes_cannot_turn_a_setext_heading_into_a_table():
    source = r"왼쪽\|오른쪽" + "\n---"
    assert parse_md(source).blocks == [Block("heading", cell(r"왼쪽\|오른쪽"), 2)]


def test_practical_sparse_table_keeps_all_rows_and_pads_missing_cells():
    source = "|A|B|C|D|E|F|\n|---|---|---|---|---|---|\n" + "|x|\n" * 24
    blocks = parse_md(source).blocks
    assert [b.kind for b in blocks] == ["table"]
    assert blocks[0].rows[1:] == [[cell("x"), [], [], [], [], []]] * 24


def test_row_exceeding_markdown_it_padding_limit_remains_literal():
    # Each short row needs 256 missing cells. The first 256 use the 65,536-cell budget.
    source = "|A" * 257 + "|\n" + "|---" * 257 + "|\n" + "|x|\n" * 256 + "|last|"
    table, tail = parse_md(source).blocks
    assert len(table.rows) == 257
    assert table.rows[-1] == [cell("x")] + [[]] * 256
    assert tail == Block("paragraph", cell("|last|"))


@pytest.mark.parametrize("source", [
    "    A | B\n    -- | --\n    1 | 2",
    "A | B\n    -- | --\n    1 | 2",
])
def test_code_indented_header_or_separator_keeps_literal_pipes(source):
    block, = parse_md(source).blocks
    assert block.kind == "paragraph"
    assert block.inlines[0].text.split() == "A | B -- | -- 1 | 2".split()


@pytest.mark.parametrize("prefix", ["", "- 항목\n"])
def test_code_indented_table_rows_stop_relative_to_the_list_content(prefix):
    indent = "  " if prefix else ""
    source = prefix + "\n".join(indent + line for line in (
        "   A | B", "   -- | --", "   1 | 2", "    3 | 4"))
    blocks = parse_md(source).blocks
    table, literal = blocks[-2:]
    assert table.kind == "table" and table.rows == [[cell("A"), cell("B")], [cell("1"), cell("2")]]
    assert literal.inlines == cell("3 | 4")
    if prefix:
        assert literal.kind == "list_item" and literal.group == blocks[0].group and not literal.first
    else:
        assert literal.kind == "paragraph"


def test_code_indented_table_inside_list_continuation_stays_literal():
    source = "- 항목\n\n      A | B\n      -- | --\n      1 | 2\n- 다음"
    item, example, sibling = parse_md(source).blocks
    assert example.kind == "list_item" and example.group == item.group == sibling.group
    assert not example.first and example.inlines[0].text.split() == "A | B -- | -- 1 | 2".split()
