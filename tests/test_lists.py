"""Manuscript structure and list mark grammar, independent of typesetting."""
import pytest

from app.document import InputError, parse_md


ARTICLES = """# 제1조

- 원칙
  1. 첫째
  2. 둘째
     - 대상
       - 세목
         - 내용
- 예외
  1. 조건

# 제2조

- 시행
"""


def test_legal_report_and_literals():
    from app.list_marks import format_mark

    doc = parse_md(ARTICLES)
    assert [(b.kind, b.inlines[0].text) for b in doc.blocks] == [
        ("heading", "제1조"), ("list_item", "원칙"), ("list_item", "첫째"),
        ("list_item", "둘째"), ("list_item", "대상"), ("list_item", "세목"),
        ("list_item", "내용"), ("list_item", "예외"), ("list_item", "조건"),
        ("heading", "제2조"), ("list_item", "시행"),
    ]
    items = [b for b in doc.blocks if b.kind == "list_item"]
    assert [(b.level, b.group, b.parent_group, b.ordinal) for b in items] == [
        (1, 1, None, 1), (2, 2, 1, 1), (2, 2, 1, 2), (3, 3, 2, 1),
        (4, 4, 3, 1), (5, 5, 4, 1), (1, 1, None, 2), (2, 6, 1, 1),
        (1, 7, None, 1),
    ]
    assert all(b.first for b in items)
    legal = ["{원숫자}", "{숫자}.", "{한글}.", "({숫자})", "({한글})"]
    assert [format_mark(legal[b.level - 1], b.ordinal) for b in items] == [
        "①", "1.", "2.", "가.", "(1)", "(가)", "②", "1.", "①",
    ]
    assert [format_mark(expr, 2) for expr in ("ㅁ", "ㅇ", "-", "[붙임 {숫자}호]", "({한글})")] == [
        "ㅁ", "ㅇ", "-", "[붙임 2호]", "(나)",
    ]
    assert format_mark("  {{ {숫자}/{한글}/{원숫자}/{숫자} }}  ", 2) == "  { 2/나/②/2 }  "
    assert format_mark("{{{숫자}}} {{숫자}}", 3) == "{3} {숫자}"


def test_ordered_start_and_parent_continuation():
    doc = parse_md("8. 첫 *강조*[^n]\n   - 하위 **굵게**\n\n   다시 부모\n"
                   "1. 다음\n\n밖의 글\n\n3) 새 목록\n\n[^n]: 주 내용")
    a, child, continued, b, plain, restarted = doc.blocks
    assert [(item.list_kind, item.ordinal, item.first) for item in (a, child, continued, b, restarted)] == [
        ("ordered", 8, True), ("bullet", 1, True), ("ordered", 8, False),
        ("ordered", 9, True), ("ordered", 3, True),
    ]
    assert a.group == continued.group == b.group == child.parent_group
    assert len({a.group, child.group, restarted.group}) == 3
    assert [node.kind for node in a.inlines] == ["text", "emphasis", "note_ref"]
    assert a.inlines[1].children[0].text == "강조" and a.inlines[-1].id == "n"
    assert child.inlines[1].children[0].text == "굵게"
    assert doc.footnotes == {"n": "주 내용"}
    assert continued.inlines[0].text == "다시 부모"
    assert plain.kind == "paragraph" and plain.inlines[0].text == "밖의 글"


def test_empty_items_and_nonpositive_markdown_start():
    doc = parse_md("0.\n   - 자식\n\n   뒤 문단\n1.\n2. 끝")
    assert [(b.level, b.ordinal, b.first, [n.text for n in b.inlines]) for b in doc.blocks] == [
        (1, 1, True, []), (2, 1, True, ["자식"]), (1, 1, False, ["뒤 문단"]),
        (1, 2, True, []), (1, 3, True, ["끝"]),
    ]


def test_nesting_overflow_is_reported_before_content_is_lost():
    levels = ["  " * i + f"- 항목{i + 1}" for i in range(10)]
    parsed = parse_md("\n".join(levels[:9]) + "\n\n마지막 문단").blocks
    assert [b.level for b in parsed] == [*range(1, 10), None]
    assert [b.inlines[0].text for b in parsed] == [f"항목{i}" for i in range(1, 10)] + ["마지막 문단"]
    with pytest.raises(InputError) as caught:
        parse_md("\n".join(levels) + "\n\n마지막 문단")
    assert caught.value.field == "source" and "목록" in caught.value.message


@pytest.mark.parametrize("expr", ["{모름}", "{}", "앞{", "뒤}", "{숫자", "{ {한글}}", "x\x00", "\ud800", None])
def test_invalid_mark(expr):
    from app.list_marks import format_mark

    with pytest.raises(ValueError):
        format_mark(expr, 1)


@pytest.mark.parametrize("ordinal", [0, -1, True, 1.5])
def test_ordinal_must_be_a_positive_integer(ordinal):
    from app.list_marks import format_mark

    with pytest.raises(ValueError):
        format_mark("고정", ordinal)


def test_numbering_beyond_single_glyphs():
    from app.list_marks import format_mark

    assert [format_mark("{한글}", n) for n in (1, 14, 15, 16, 28, 29, 210, 211)] == [
        "가", "하", "가가", "가나", "가하", "나가", "하하", "가가가",
    ]
    assert [format_mark("{원숫자}", n) for n in (19, 20, 21, 51)] == ["⑲", "⑳", "(21)", "(51)"]


def test_list_preserves_authored_ideographic_indents():
    source = "- 　가\n  - 　　*나*\n\n    　 **다**\n\n  　부모\n- - 　라"
    blocks = parse_md(source).blocks
    assert [(b.level, b.first, b.inlines[0].text if b.inlines else None) for b in blocks] == [
        (1, True, "　가"), (2, True, "　　"), (2, False, "　 "),
        (1, False, "　부모"), (1, True, None), (2, True, "　라"),
    ]
    assert blocks[1].inlines[1].kind == "emphasis" and blocks[2].inlines[1].kind == "strong"


@pytest.mark.parametrize("control", ["\r", "\n", "\t", "\x07", "\x1f", "\x7f", "\u0085", "\u2028", "\u2029"])
def test_single_line_marks_reject_control_characters(control):
    from app.list_marks import format_mark

    with pytest.raises(ValueError, match="문자"):
        format_mark("앞" + control + "뒤", 1)
    assert format_mark(" 　{숫자}　 ", 2) == " 　2　 "
