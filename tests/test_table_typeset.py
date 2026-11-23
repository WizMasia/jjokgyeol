"""Measure Markdown tables in the delivered PDF, in PDF points from bottom left."""
from io import BytesIO
from itertools import groupby
from time import monotonic

import pytest
from pdfminer.high_level import extract_pages
from pdfminer.layout import LTCurve

from app.ai.decision import measured_rows, render_provider_choice
from app.document import parse_md
from app.style import Style, apply_style
from app.typeset import typeset
from test_decision import STYLE as DECISION_STYLE
from test_list_typeset import BP_PER_MM, baselines, text
from test_reference_typeset import glyphs


def render(work, source, *, page_mm, **settings):
    style = apply_style(Style(), {"page_mm": page_mm, "margins_mm": [10] * 4, **settings})
    return typeset(parse_md(source), style, work)


def data_lines(lines, header):
    """Each table page starts with exactly one complete header and contains data."""
    data = []
    for page, group in groupby(lines, key=lambda line: line[0]):
        if data:
            assert page == data[-1][0] + 1
        first, *body = group
        assert text(first[1]) == header
        assert body and all(text(chars) != header for _, chars in body)
        data.extend(body)
    return data


def test_long_description_gets_more_width_and_keeps_its_inline_faces(tmp_path):
    word = "봄여름가을겨울" * 6
    source = f"| 때 | 설명 |\n| --- | --- |\n| 월 | **굵** *빗* {word} |"
    pdf = render(tmp_path, source, page_mm=[100, 100])
    (page, header), *body = baselines(pdf)
    assert page == 1 and text(header) == "때설명"
    assert all("Bold" in c.font for c in header)
    assert "".join(text(chars) for _, chars in body) == "월굵빗" + word
    assert len(body) > 1 and body[-1][0] == 1
    short_x, description_x = header[0].x0, header[1].x0
    left, right = 10 * BP_PER_MM, 90 * BP_PER_MM
    assert left - .05 <= short_x < description_x < (left + right) / 2
    assert body[0][1][0].x0 == pytest.approx(short_x, abs=.05)
    for i, (_, chars) in enumerate(body):
        first = chars[1] if i == 0 else chars[0]
        assert first.x0 == pytest.approx(description_x, abs=.05)
        assert chars[-1].x1 <= right + .05
    assert max(chars[-1].x1 for _, chars in body) - description_x > (right - left) / 2
    page_layout, = extract_pages(BytesIO(pdf))
    assert any(isinstance(obj, LTCurve) and obj.width > obj.height
               and obj.x0 <= short_x + .05 and obj.x1 >= header[-1].x1 - .05
               and body[0][1][0].baseline < obj.y0 <= header[0].y1 + header[0].size
               for obj in page_layout)
    visible = glyphs(pdf)
    by_char = {c.get_text(): c for c in visible}
    assert "Bold" in by_char["굵"].fontname and "Bold" not in by_char["봄"].fontname
    assert by_char["빗"].matrix[2] != by_char["봄"].matrix[2]


def test_shorter_cells_align_on_left_edge_midpoint_and_right_edge(tmp_path):
    source = """| 좌 | 중 | 우 |
| :--- | :---: | ---: |
| 가가가 | 나나나 | 다다다 |
| 가 | 나 | 다 |
| 끝 | | 다 |"""
    lines = [chars for _, chars in baselines(render(tmp_path, source, page_mm=[100, 90]))]
    assert [text(chars) for chars in lines] == ["좌중우", "가가가나나나다다다", "가나다", "끝다"]
    _, wide, narrow, empty = lines
    assert narrow[0].x0 == pytest.approx(wide[0].x0, abs=.05)
    midpoint = (wide[3].x0 + wide[5].x1) / 2
    assert (narrow[1].x0 + narrow[1].x1) / 2 == pytest.approx(midpoint, abs=.05)
    assert narrow[2].x1 == pytest.approx(wide[-1].x1, abs=.05)
    assert empty[-1].x1 == pytest.approx(narrow[-1].x1, abs=.05)
    assert wide[2].x1 < wide[3].x0 and wide[5].x1 < wide[6].x0


def test_ordinary_wrapped_rows_move_together_and_do_not_consume_proof_numbers(tmp_path):
    keys = "가나다라마바사아"
    rows = [f"| {i} | {key * 17} |" for i, key in enumerate(keys, 1)]
    source = "앞말\n\n| 순 | 기록 |\n| --- | --- |\n" + "\n".join(rows) + "\n\n뒷말\n\n- 목록"
    pdf = render(tmp_path, source, page_mm=[72, 65], line_numbers="left")
    lines = baselines(pdf)
    numbered = []
    table = []
    for page, chars in lines:
        label = [c for c in chars if c.x1 < 10 * BP_PER_MM - 1]
        if label:
            numbered.append((text(label), text(chars[len(label):])))
        else:
            table.append((page, chars))
    assert numbered == [("1", "앞말"), ("2", "뒷말"), ("3", "①목록")]
    assert text(lines[0][1]) == "1앞말"
    assert [text(chars) for _, chars in lines[-2:]] == ["2뒷말", "3①목록"]
    body = data_lines(table, "순기록")
    assert 1 < len({page for page, _ in body}) < len(keys)
    actual = "".join(text(chars) for _, chars in body)
    assert actual == "".join(str(i) + key * 17 for i, key in enumerate(keys, 1))
    for i, key in enumerate(keys, 1):
        positions = [(page, c.baseline) for page, chars in body for c in chars if c.text in (str(i), key)]
        assert len({y for _, y in positions}) > 1
        assert len({page for page, _ in positions}) == 1, f"ordinary row {i} split across pages"


def test_oversized_row_continues_both_cells_in_order_inside_the_page(tmp_path):
    digits = "".join(f"{i:03}" for i in range(80))
    syllables = "산강들숲" * 18 + "끝"
    source = f"| 코드 | 지명 |\n| :--- | :--- |\n| {digits} | {syllables} |\n| 마침 | 확인 |"
    lines = baselines(render(tmp_path, source, page_mm=[82, 62]))
    body = data_lines(lines, "코드지명")
    last_page, last_row = body.pop()
    assert last_page > 1 and text(last_row) == "마침확인"
    left_runs, right_runs = [], []
    for page, chars in body:
        left = [c for c in chars if c.text.isdigit()]
        right = [c for c in chars if c.text in syllables]
        assert chars == left + right
        if left:
            left_runs.append((page, left))
        if right:
            right_runs.append((page, right))
        assert 10 * BP_PER_MM - .05 <= chars[0].x0 < chars[-1].x1 <= 72 * BP_PER_MM + .05
        assert 9 * BP_PER_MM <= chars[0].baseline <= 53 * BP_PER_MM
    assert left_runs[0][0] < left_runs[-1][0]
    assert right_runs[0][0] < right_runs[-1][0]
    assert "".join(text(run) for _, run in left_runs) == digits
    assert "".join(text(run) for _, run in right_runs) == syllables
    assert max(run[-1].x1 for _, run in left_runs) < min(run[0].x0 for _, run in right_runs)


def test_fixed_model_changes_following_paragraph_and_preserves_table_glyphs(tmp_path):
    class ShortFirstLine:
        def predict(self, options, *, timeout):
            return min(options, key=lambda key: options[key][0]["end_glyph"])

    rows = "\n".join(f"| {i} | **강** |" for i in range(12))
    source = "| 수 | 땅 |\n| ---: | :---: |\n" + rows + "\n\n풀잎 사이 Dew 은빛 이슬"
    style = apply_style(Style(), {**DECISION_STYLE, "page_mm": [58, 68]})
    chosen, status, reason = render_provider_choice(parse_md(source), style, tmp_path,
                                                    monotonic() + 30, ShortFirstLine())
    assert (status, reason) == ("model", None)
    assert [row["block"] for row in measured_rows(tmp_path / "measure")] == [2]
    rule = baselines((tmp_path / "measure/book.pdf").read_bytes())
    model = baselines(chosen)
    assert [text(chars) for _, chars in rule[-2:]] == ["풀잎사이Dew은빛", "이슬"]
    assert [text(chars) for _, chars in model[-2:]] == ["풀잎사이Dew", "은빛이슬"]
    rule_table, model_table = rule[:-2], model[:-2]
    assert rule_table == model_table
    body = data_lines(model_table, "수땅")
    assert body[0][0] < body[-1][0]
    assert [text(chars) for _, chars in body] == [f"{i}강" for i in range(12)]
