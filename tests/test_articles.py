from copy import deepcopy
from dataclasses import asdict

import pytest

from app.document import InputError, parse_md
from app.style import Style, apply_style
from app.typeset import typeset
from pdf_metrics import read_pages


def numbered(source, expr="제{숫자}조"):
    from app.articles import number_articles

    doc = parse_md(source)
    blocks = [asdict(block) for block in doc.blocks]
    number_articles(blocks, expr, doc.fenced_blocks)
    return blocks


def visible(inlines):
    return "".join(n["text"] if n["kind"] == "text" else visible(n["children"]) for n in inlines)


@pytest.mark.parametrize(("original", "printed"), [
    (["제8조(목적)", "제2조 시행"], ["제1조(목적)", "제2조 시행"]),
    (["제8조(목적)", "제8조의2 추가", "제2조 시행"], ["제1조(목적)", "제1조의2 추가", "제2조 시행"]),
    (["제2조 시행", "제8조(목적)", "제8조 정의", "제8조의5 추가"],
     ["제1조 시행", "제2조(목적)", "제3조 정의", "제3조의5 추가"]),
    (["제7조의2 발췌", "제8조의005 이동", "제4조 시작", "제8조 기준"],
     ["제7조의2 발췌", "제2조의005 이동", "제1조 시작", "제2조 기준"]),
    (["제8조의2 모호", "제8조 가", "제8조의3 나", "제8조 다", "제8조의4 라"],
     ["제8조의2 모호", "제1조 가", "제1조의3 나", "제2조 다", "제2조의4 라"]),
    (["제9조 가 {번호고정}", "제5조 나", "제5조의03 고정 {번호고정}", "제5조의4 가지", "제1조 다"],
     ["제9조 가", "제10조 나", "제5조의03 고정", "제10조의4 가지", "제11조 다"]),
])
def test_numbering_after_edits_and_moves(original, printed):
    blocks = numbered("\n\n".join("### " + title for title in original))
    assert [visible(b["inlines"]) for b in blocks] == printed


def test_preserve_inline_emphasis_title_spacing_and_references():
    source = ("## 제**8**조의02(범위)  *상세*[^n] ###\n\n"
              "# **제8조**　일반\n\n"
              "# **제7조  *고정* {번호고정}** ##\n\n"
              "# 제8조항 {번호고정}\n\n# 다른 제목 {번호고정}\n\n"
              "# 제7조의3뒤 {번호고정}\n\n# 제7조 내용{번호고정}\n\n"
              "본문 제8조 {번호고정}\n\n- # 제8조 인용 {번호고정}\n\n[^n]: 각주")
    blocks = numbered(source)
    assert [visible(b["inlines"]) for b in blocks] == [
        "제1조의02(범위)  상세", "제1조　일반", "제7조  고정",
        "제8조항 {번호고정}", "다른 제목 {번호고정}", "제7조의3뒤 {번호고정}",
        "제8조 내용{번호고정}", "본문 제8조 {번호고정}", "제8조 인용 {번호고정}",
    ]
    spans = blocks[0]["inlines"]
    assert [(n["kind"], visible([n])) for n in spans] == [
        ("text", "제"), ("strong", "1"), ("text", "조의02(범위)  "), ("emphasis", "상세"), ("note_ref", ""),
    ]
    assert spans[-1]["id"] == "n"
    assert blocks[2]["inlines"][0]["kind"] == "strong"
    assert any(n["kind"] == "emphasis" for n in blocks[2]["inlines"][0]["children"])


def test_only_first_top_level_group_of_each_article_restarts():
    source = ("7. 밖\n\n# 제8조 제목\n\n중간 문단\n\n## 일반 절\n\n"
              "7. 하나\n\n   5. 하위\n\n   이음\n8. 둘\n\n중간\n\n"
              "9. 두번째 그룹\n\n# 제8조의2 가지\n\n7. 가지항\n\n"
              "# 제4조 고정 {번호고정}\n\n7. 고정항")
    items = [b for b in numbered(source) if b["kind"] == "list_item"]
    assert [(visible(b["inlines"]), b["ordinal"]) for b in items] == [
        ("밖", 7), ("하나", 1), ("하위", 5), ("이음", 1), ("둘", 2),
        ("두번째 그룹", 9), ("가지항", 1), ("고정항", 1),
    ]


def test_oversized_number_is_a_source_error_only_when_enabled():
    title = "제" + "9" * 6000 + "조 목적"
    source = "# " + title + " {번호고정}"
    assert visible(numbered(source, None)[0]["inlines"]) == title
    with pytest.raises(InputError) as error:
        numbered(source)
    assert error.value.field == "source"


def test_only_enabled_article_headings_request_literal_spacing():
    source = "# 일반  제목\n\n# 제8조 목적\n\n# 제8조의02  추가\n\n- 항"
    for expr in ("", "  "):
        ordinary, base, branch, item = numbered(source, expr)
        assert [visible(b["inlines"]) for b in (base, branch)] == [expr + " 목적", expr + "의02  추가"]
        assert base["preserve_spaces"] is branch["preserve_spaces"] is True
        assert "preserve_spaces" not in ordinary and "preserve_spaces" not in item
    assert all("preserve_spaces" not in block for block in numbered(source, None))


def test_pinned_digits_keep_their_existing_inline_styles():
    pinned, = numbered("# 제1**2**조 제목 {번호고정}")
    spans = pinned["inlines"]
    assert [(n["kind"], visible([n])) for n in spans] == [
        ("text", "제1"), ("strong", "2"), ("text", "조 제목"),
    ]


@pytest.mark.parametrize("fence", ["````", "~~~"])
def test_examples_inside_fences_keep_their_original_titles(tmp_path, fence):
    source = (f"# 제8조(시작)\n\n{fence}md\n# 제2조 예시 {{번호고정}}\n\n"
              f"본문 제9조\n{fence}\n\n# 제9조(끝)")
    doc = parse_md(source)
    for enabled in (False, True):
        work = tmp_path / str(enabled)
        work.mkdir()
        style = apply_style(Style(), {"article_expr": "제{숫자}조"}) if enabled else Style()
        pdf = typeset(doc, style, work)
        body = "".join("".join(line.text.split()) for page in read_pages(pdf) for line in page.lines)
        assert "제2조예시{번호고정}" in body and "본문제9조" in body
        assert body.startswith("제1조(시작)" if enabled else "제8조(시작)")
        assert body.endswith("제2조(끝)" if enabled else "제9조(끝)")


def test_fenced_headings_and_list_examples_do_not_restart_or_claim_real_lists():
    source = ("# 제8조 시작\n\n```\n\n7. 예시 항\n```\n\n본문\n\n"
              "7. 실제 첫 항\n\n```\n# 제7조 코드 예시\n```\n\n"
              "4. 실제 둘째 목록\n\n# 제9조 목적\n\n7. 새 조 첫 항")
    blocks = numbered(source)
    assert [visible(b["inlines"]) for b in blocks if b["kind"] == "heading"] == [
        "제1조 시작", "제7조 코드 예시", "제2조 목적",
    ]
    assert [b["ordinal"] for b in blocks if b["kind"] == "list_item" and b["first"]] == [7, 1, 4, 1]


def test_editor_reads_the_same_heading_ranges_text_and_pins_as_the_renderer():
    from app.articles import match_article
    from app.document import heading_text, heading_tokens

    source = ("# **제8조**（목적） {번호고정} ###\r\n\r\n"
              "제7조  *제목*[^n]\r\n둘째 줄\r\n{번호고정}\r\n-----\r\n\r\n"
              "- # 제6조 목록\r\n\r\n~~~\r\n# 제5조 코드\r\n~~~\r\n\r\n"
              "# 제0조 일반 {번호고정}\r\n\r\n[^n]: 주")
    titles = list(heading_tokens(source))
    assert [(head.map, inline.map) for head, inline in titles] == [
        ([0, 1], [0, 1]), ([2, 6], [2, 5]), ([13, 14], [13, 14]),
    ]
    for _, inline in titles[:2]:
        shown = heading_text(inline)
        match, pin = match_article(shown)
        assert shown[pin:].strip() == "{번호고정}"
        assert match[1] in ("8", "7")
    assert heading_text(titles[1][1]) == "제7조  제목\0 둘째 줄 {번호고정}"
    assert match_article(heading_text(titles[2][1])) is None


@pytest.mark.parametrize("pinned", [False, True])
def test_setext_article_immediately_after_closing_fence(tmp_path, pinned):
    from app.document import heading_tokens

    suffix = " {번호고정}" if pinned else ""
    source = ("~~~\n# 제7조 예시 {번호고정}\n~~~\n제8조 목적" + suffix
              + "\n===\n\n7. 첫 항\n\n# 제9조 시행")
    headings = list(heading_tokens(source))
    assert [(op.map, inline.content) for op, inline in headings] == [
        ([3, 5], "제8조 목적" + suffix), ([8, 9], "제9조 시행")]
    doc = parse_md(source)
    for enabled in (False, True):
        work = tmp_path / str(enabled)
        work.mkdir()
        style = apply_style(Style(), {"article_expr": "제{숫자}조"}) if enabled else Style()
        pdf = typeset(doc, style, work)
        page, = read_pages(pdf)
        rows = ["".join(row.text.split()) for row in page.lines]
        body = "".join(rows)
        assert "제7조예시{번호고정}" in body and body.count("{번호고정}") == 1
        if enabled:
            assert ("제8조목적" if pinned else "제1조목적") in rows
            assert body.endswith("①첫항" + ("제9조시행" if pinned else "제2조시행"))
        else:
            assert body.endswith("제8조목적⑦첫항제9조시행")
            if not pinned:
                assert "~~~제8조목적" in rows  # Existing zero-parser geometry.


def test_toggle_and_custom_expression_in_real_pdf_preserves_parsed_document(tmp_path):
    doc = parse_md("# **제8조** 목적 {번호고정} ##\n\n7. 첫항\n\n"
                   "# 제8조의2(가지)\n\n7. 가지항\n\n# 제2조 시행\n\n7. 시행항")
    original = deepcopy(doc)
    for i, (expr, expected) in enumerate([
        (None, "제8조목적⑦첫항제8조의2(가지)⑦가지항제2조시행⑦시행항"),
        ("[{{법}}{숫자}]", "[{법}8]목적①첫항[{법}8]의2(가지)①가지항[{법}9]시행①시행항"),
        (None, "제8조목적⑦첫항제8조의2(가지)⑦가지항제2조시행⑦시행항"),
    ]):
        work = tmp_path / str(i)
        work.mkdir()
        style = Style() if expr is None else apply_style(Style(), {"article_expr": expr})
        pdf = typeset(doc, style, work)
        pages = read_pages(pdf)
        assert len(pages) == 1
        assert "".join("".join(line.text.split()) for line in pages[0].lines) == expected
        assert "{번호고정}" not in (work / "input.json").read_text()
        assert doc == original


def test_active_heading_preserves_expression_and_title_spaces_in_pdf(tmp_path):
    source = "# 제7조(가)\n\n# 제8조 (나)\n\n# 제9조  (다)"
    measurements = []
    for count in (0, 1, 2):
        work = tmp_path / str(count)
        work.mkdir()
        pad = " " * count
        expr = pad + "제" + pad + "{숫자}조" + pad
        pdf = typeset(parse_md(source), apply_style(Style(), {"article_expr": expr}), work)
        page, = read_pages(pdf)
        rows = [{c.text: c for c in row.chars} for row in page.lines]
        assert len(rows) == 3
        first = rows[0]
        measurements.append([first["제"].x0, first["1"].x0 - first["제"].x1,
                             first["("].x0 - first["조"].x1])
        gaps = [row["("].x0 - row["조"].x1 for row in rows]
        assert gaps[1] - gaps[0] > 1
        assert gaps[2] - gaps[1] == pytest.approx(gaps[1] - gaps[0], abs=.03)
    for zero, one, two in zip(*measurements):
        assert one - zero > 1
        assert two - one == pytest.approx(one - zero, abs=.03)


@pytest.mark.parametrize("expr", ["", "   "])
def test_blank_number_only_heading_still_produces_one_page(tmp_path, expr):
    pdf = typeset(parse_md("# 제7조"), apply_style(Style(), {"article_expr": expr}), tmp_path)
    page, = read_pages(pdf)
    assert all(not line.text.strip() for line in page.lines)
