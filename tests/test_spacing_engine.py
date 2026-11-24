"""Compare boundary decisions with the baselines in the shipped PDF."""
import json

import pytest

from app.document import parse_md
from app.style import Style, apply_style
from app.typeset import typeset
from pdf_metrics import read_pages


SP = 65536
BP = 72 / 72.27


def render(folder, doc, style, **options):
    folder.mkdir()
    return read_pages(typeset(doc, style, folder, **options))


def report(folder):
    return json.loads((folder / "spacing.json").read_text())


def baselines(pages):
    return {c.text: (page, c.baseline) for page, p in enumerate(pages, 1)
            for line in p.lines for c in line.chars}


def test_one_gap_per_boundary_and_defaults_reported(tmp_path):
    doc = parse_md("앞\n\n# 산\n\n중\n\n뒤\n\n끝")
    plain = baselines(render(tmp_path / "plain", doc, Style()))
    doc.blocks[0].space_after_sp = 6 * SP
    doc.blocks[2].space_before_sp = 0
    style = apply_style(Style(), {"text_styles": {"heading1": {
        "space_before_pt": 12, "space_after_pt": "auto"}}})
    printed = baselines(render(tmp_path / "spaced", doc, style))
    for char, delta in zip("앞산중뒤끝", [0, 8, 4, 4, 4], strict=True):
        assert plain[char][1] - printed[char][1] == pytest.approx(delta * BP, abs=.02)
    rows = report(tmp_path / "spaced")
    assert [(r["id"], r["before_line"], r["after_line"], r["source"], r["gap_sp"], r["auto"])
            for r in rows] == [
        ("2", 0, 2, "style", 12 * SP, False), ("3", 2, 4, "manual", 0, False),
        ("4", 4, 6, "default", 4 * SP, False), ("5", 6, 8, "default", 4 * SP, False)]
    assert all(r["valid"] and r["page"] == r["lines"] == 1 for r in rows)
    assert rows[0]["left_kind"] == "paragraph" and rows[0]["right_kind"] == "heading"
    assert all(a["remaining_sp"] > b["remaining_sp"] > 0 for a, b in zip(rows, rows[1:]))


def test_explicit_zero_document_gap_removes_the_four_point_default(tmp_path):
    doc = parse_md("첫 문단\n\n둘째 문단")
    default = baselines(render(tmp_path / "default-gap", doc, Style()))
    zero_style = apply_style(Style(), {"paragraph_gap_pt": 0})
    zero = baselines(render(tmp_path / "zero-gap", doc, zero_style))
    assert zero["둘"][1] - default["둘"][1] == pytest.approx(4 * BP, abs=.02)


def test_frame_and_table_receive_one_exterior_gap(tmp_path):
    doc = parse_md("앞\n\n# 제목\n\n| 갑 | 을 |\n| -- | -- |\n| 병 | 정 |\n\n뒤")
    style = apply_style(Style(), {"text_styles": {"heading1": {"frame": "box"}}})
    old = baselines(render(tmp_path / "base", doc, style))
    doc.blocks[1].space_before_sp = 0
    doc.blocks[1].space_after_sp = 12 * SP
    doc.blocks[3].space_before_sp = 6 * SP
    new = baselines(render(tmp_path / "configured", doc, style))
    for char, delta in zip("앞제갑병뒤", [0, -4, 4, 4, 6], strict=True):
        assert old[char][1] - new[char][1] == pytest.approx(delta * BP, abs=.02)
    assert [r["gap_sp"] for r in report(tmp_path / "configured")] == [0, 12 * SP, 6 * SP]


def test_nested_and_resumed_list_paragraphs_do_not_inherit_body_spacing(tmp_path):
    doc = parse_md("앞\n\n- 갑\n  이어짐\n\n  병\n\n  - 정\n\n  복귀\n\n뒤")
    levels = [{"expr": "-", "indent_mm": 1, "gap_mm": 1},
              {"expr": "*", "indent_mm": 2, "gap_mm": 1}]
    plain = baselines(render(tmp_path / "plain", doc, apply_style(Style(), {"list_levels": levels})))
    style = apply_style(Style(), {"list_levels": [
        dict(levels[0], space_before_pt=6, space_after_pt=0), dict(levels[1], space_before_pt=12)],
        "text_styles": {"body": {"space_before_pt": 20}}})
    new = baselines(render(tmp_path / "list", doc, style))
    for char, delta in zip("앞갑병정복뒤", [0, 2, 4, 12, 14, 30], strict=True):
        assert plain[char][1] - new[char][1] == pytest.approx(delta * BP, abs=.02)
    assert [(r["after_line"], r["gap_sp"] // SP) for r in report(tmp_path / "list")] == [
        (2, 6), (5, 6), (7, 12), (9, 6), (11, 20)]


def test_page_break_discards_gap_before_multipage_paragraph(tmp_path):
    doc = parse_md("앞\n\n" + "넓은 들을 지나 " * 55)
    style = apply_style(Style(), {"page_mm": [66, 50], "margins_mm": [10] * 4})
    doc.blocks[1].space_before_sp = 72 * SP
    printed = render(tmp_path / "break", doc, style)
    reference = render(tmp_path / "standalone", parse_md("넓은 들을 지나 " * 55), style)
    assert [line.text for line in printed[0].lines] == ["앞"]
    assert len(printed) == len(reference) + 1 > 2
    for page, control in zip(printed[1:], reference, strict=True):
        assert [[(c.text, c.x0, c.baseline) for c in line.chars] for line in page.lines] == [
            [(c.text, c.x0, c.baseline) for c in line.chars] for line in control.lines]
    row, = report(tmp_path / "break")
    assert (row["gap_sp"], row["page"], row["source"]) == (0, 2, "manual")
    assert row["lines"] > 1


def test_counts_inner_frame_lines_and_repeated_table_headers(tmp_path):
    title = "구름 바람 " * 4
    table = "| 구분 | 값 |\n| -- | -- |\n" + "| 측정 | 값 |\n" * 16
    doc = parse_md(f"앞\n\n# {title}\n\n{table}\n뒤")
    style = apply_style(Style(), {"page_mm": [70, 105], "margins_mm": [10] * 4,
                                  "text_styles": {"heading1": {"frame": "box", "space_before_pt": 6}}})
    pages = render(tmp_path / "table", doc, style)
    frame, table, tail = report(tmp_path / "table")
    frame_lines = sum("구름" in line.text or "바람" in line.text for page in pages for line in page.lines)
    table_pages = sum(any("구분" in line.text for line in page.lines) for page in pages)
    assert frame_lines > 1 and table_pages > 1
    assert frame["lines"] == frame_lines
    assert table["lines"] == 16 + table_pages
    assert tail["left_page"] == len(pages)


def test_hidden_memo_only_heading_does_not_own_a_boundary(tmp_path):
    doc = parse_md("첫\n\n# [^메]\n\n끝[^주]\n\n[^메]{메모}: 편집\n[^주]: 바닥 각주")
    doc.blocks[0].space_after_sp = 12 * SP
    render(tmp_path / "notes", doc, Style())
    row, = report(tmp_path / "notes")
    assert (row["id"], row["before_line"], row["after_line"], row["gap_sp"]) == ("3", 0, 4, 12 * SP)


def test_replay_combines_auto_gap_with_same_measured_line_choice(tmp_path):
    from test_decision import SOURCE, STYLE

    doc = parse_md("봄\n\n" + SOURCE)
    doc.blocks[0].space_after_sp = "auto"
    style = apply_style(Style(), STYLE)
    measure, replay = tmp_path / "measure", tmp_path / "replay"
    render(measure, doc, style, collect_candidates=True)
    row, = report(measure)
    assert (row["auto"], row["source"], row["gap_sp"]) == (True, "rule", 4 * SP)
    candidates = [json.loads(line) for line in (measure / "paragraph-candidates.jsonl").read_text().splitlines()]
    measured = next(r for r in candidates if r["block"] == 2)
    choice = next(c["id"] for c in measured["candidates"] if c["id"] != measured["rule_id"])
    with_break = render(tmp_path / "line", doc, style, collect_candidates=True, paragraph_choices={2: choice})
    with_gap = render(replay, doc, style, collect_candidates=True, paragraph_choices={2: choice},
                      spacing_choices={"2": 6 * SP})
    selected = [json.loads(line) for line in (replay / "paragraph-candidates.jsonl").read_text().splitlines()]
    assert next(r for r in selected if r["block"] == 2)["selected_id"] == choice
    assert baselines(with_break)["풀"][1] - baselines(with_gap)["풀"][1] == pytest.approx(2 * BP, abs=.02)
    row, = report(replay)
    assert (row["auto"], row["source"], row["gap_sp"]) == (True, "ai", 6 * SP)


def test_source_lines_follow_rendered_fence_join_and_exclude_literal_blocks(tmp_path):
    doc = parse_md("~~~\n예시\n~~~\n제목\n===\n\n    코드\n\n본문")
    style = apply_style(Style(), {"text_styles": {"body": {"space_before_pt": 6}}})
    render(tmp_path / "joined", doc, style)
    rows = report(tmp_path / "joined")
    assert [(r["id"], r["before_line"], r["after_line"], r["gap_sp"]) for r in rows] == [
        ("2", 0, 6, 4 * SP), ("3", 6, 8, 6 * SP)]
