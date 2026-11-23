"""Paragraph and heading alignment in canonical styles and the shipped PDF."""
from pathlib import Path
from time import monotonic

import pytest

from app.ai.decision import measured_rows, render_provider_choice
from app.document import InputError, parse_md
from app.paragraph_edit import edit_alignment
from app.style import Style, apply_style, from_stored_style
from app.typeset import typeset
from pdf_metrics import read_pages
from test_decision import SOURCE, STYLE
from test_list_typeset import BP_PER_MM, baselines, text


BP_PER_PT = 72 / 72.27
PROOFS = Path(__file__).resolve().parents[1] / "proofs/decision-model"


class ShortFirstLine:
    def predict(self, options, *, timeout):
        return min(options, key=lambda key: options[key][0]["end_glyph"])


def test_role_styles_keep_alignment_and_exact_sizes_through_both_formats():
    rows = {"body": {"align": "right"}}
    for level in range(1, 7):
        rows[f"heading{level}"] = {"align": ("left", "center", "right")[level % 3],
                                   "font_sp": 65536 * (12 + level) + level}
    stored = Style().to_stored() | {"body_sp": 11 * 65536 + 1, "text_styles": rows}
    style = from_stored_style(stored)
    assert style.to_stored() == stored
    assert apply_style(Style(), style.to_editor()) == style
    assert "text_styles" not in from_stored_style(Style().to_stored()).to_editor()


@pytest.mark.parametrize(("role", "value"), [
    ("heading2", "justify"), ("body", None), ("body", True), ("heading5", []),
    ("note", "left"), ("gloss", "center"),
])
def test_invalid_alignment_reports_exact_role_field(role, value):
    patch = {"text_styles": {role: {"align": value}}}
    with pytest.raises(InputError) as editor:
        apply_style(Style(), patch)
    with pytest.raises(InputError) as stored:
        from_stored_style(Style().to_stored() | patch)
    assert editor.value.field == stored.value.field == f"text_styles.{role}.align"


@pytest.mark.parametrize("fixed_sizes", [False, True])
def test_pdf_effective_sizes_for_body_and_each_heading(tmp_path, fixed_sizes):
    names = "강숲산들섬별"
    body = 12.25
    sizes = ([14 + level / 8 for level in range(1, 7)] if fixed_sizes
             else [body * ratio for ratio in (1.6, 1.5, 1.4, 1.3, 1.2, 1.1)])
    rows = {f"heading{i}": {"align": "center", **({"font_pt": size} if fixed_sizes else {})}
            for i, size in enumerate(sizes, 1)}
    style = apply_style(Style(), {"body_pt": body, "text_styles": rows | {"body": {"align": "left"}}})
    source = "숲\n\n" + "\n\n".join("#" * i + " " + name for i, name in enumerate(names, 1))
    lines = baselines(typeset(parse_md(source), from_stored_style(style.to_stored()), tmp_path))
    assert [text(chars) for _, chars in lines] == ["숲", *names]
    for (_, (char,)), size in zip(lines, [body, *sizes], strict=True):
        assert char.size == pytest.approx(size * BP_PER_PT, abs=.015)


@pytest.mark.parametrize("framed", [False, True])
def test_wrapped_heading_role_and_direct_override_use_inner_edges(tmp_path, framed):
    title = "가을 구름 " * 4
    source = f"# {title}\n\n# {title}\n\n본문"
    doc = parse_md(source)
    doc.blocks[1].align = "left"
    style = apply_style(Style(), {"page_mm": [84, 180], "margins_mm": [10, 9, 10, 14],
                                  "gutter_mm": 3, "text_styles": {"heading1": {
                                      "align": "center", **({"frame": "box"} if framed else {})}}})
    lines = [chars for _, chars in baselines(typeset(doc, style, tmp_path))]
    assert text(lines.pop()) == "본문"
    count = len(lines) // 2
    assert count >= 2 and list(map(text, lines[:count])) == list(map(text, lines[count:]))
    left, right = 17 * BP_PER_MM, 75 * BP_PER_MM
    for row in lines[:count]:
        assert (row[0].x0 + row[-1].x1) / 2 == pytest.approx((left + right) / 2, abs=.04)
    for row in lines[count:]:
        assert row[0].x0 == pytest.approx(left + (3.4 * BP_PER_PT if framed else 0), abs=.04)


def test_body_role_fills_mirrored_width_without_changing_lists_tables_or_headings(tmp_path):
    source = ("바다 섬 " * 8 + "\n\n") * 8 + "# 제목\n\n- 목록\n\n| 표 | 값 |\n| --- | ---: |\n| 칸 | 둘 |"
    patch = {"page_mm": [71, 80], "margins_mm": [10, 9, 10, 15], "gutter_mm": 3,
             "first_indent_mm": 3, "rest_indent_mm": 5, "paragraph_layout": "justified"}
    style = apply_style(Style(), patch | {"text_styles": {"body": {"align": "right"}}})
    pdf = typeset(parse_md(source), style, tmp_path, collect_candidates=True)
    assert [r["block"] for r in measured_rows(tmp_path)] == [10]  # Only the list item.
    pages = set()
    for page, row in baselines(pdf):
        if text(row).startswith(("바", "섬")):
            pages.add(page)
            assert row[-1].x1 == pytest.approx((62 if page % 2 else 53) * BP_PER_MM, abs=.04)
    assert {1, 2} <= pages
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    other_roles = source[source.index("#"):]
    old = typeset(parse_md(other_roles), apply_style(Style(), patch), baseline)
    for (page, row), (_, original) in zip(baselines(pdf)[-4:], baselines(old), strict=True):
        dx = (0 if page % 2 else -9) * BP_PER_MM
        assert text(row) == text(original)
        assert [c.x0 for c in row] == pytest.approx([c.x0 + dx for c in original], abs=.04)


def test_direct_alignment_uses_natural_spaces_and_skips_model_measurement_and_replay(tmp_path):
    doc = parse_md("# 틀\n\n## 소제목\n\n가 나 다 라 마 바 사 아 자 차 카 타 파 하\n\n" + SOURCE)
    doc.blocks[2].align = "right"
    style = apply_style(Style(), {**STYLE, "first_indent_mm": 1, "rest_indent_mm": 2,
                                  "space_pct": [70, 130], "text_styles": {
                                      "heading1": {"align": "center", "frame": "box"},
                                      "heading2": {"align": "center"}}})
    pdf, status, reason = render_provider_choice(doc, style, tmp_path, monotonic() + 30, ShortFirstLine())
    assert (status, reason) == ("model", None)
    aligned = []
    for folder in (tmp_path / "measure", tmp_path / "replay"):
        assert [row["block"] for row in measured_rows(folder)] == [4]
        lines = baselines((folder / "book.pdf").read_bytes())
        headings = [row for _, row in lines if text(row) in ("틀", "소제목")]
        assert [text(row) for row in headings] == ["틀", "소제목"]
        for row in headings:
            assert (row[0].x0 + row[-1].x1) / 2 == pytest.approx(29 * BP_PER_MM, abs=.04)
        runs = [row for _, row in lines
                if set(text(row)) <= set("가나다라마바사아자차카타파하")]
        assert len(runs) >= 2
        for row in runs:
            assert row[-1].x1 == pytest.approx(48 * BP_PER_MM, abs=.04)
            for a, b in zip(row, row[1:]):
                assert b.x0 - a.x1 == pytest.approx(a.size * 307 / 1024, abs=.02)
        aligned.append((headings, runs))
    assert aligned[0] == aligned[1]
    assert any("풀잎" in text(row) and row[0].x0 == pytest.approx(11 * BP_PER_MM, abs=.04)
               for _, row in baselines(pdf))


def test_clearing_marker_and_role_restores_existing_rule_and_model_proofs(tmp_path):
    marked = "<!-- 쪽결 정렬=오른쪽 -->\n" + SOURCE
    reset = edit_alignment({"source": marked, "first_line": 0, "last_line": 0, "align": None})
    assert reset["source"] == SOURCE
    stored = apply_style(Style(), STYLE | {"text_styles": {"body": {"align": "center"}}}).to_stored()
    del stored["text_styles"]["body"]["align"]
    style = from_stored_style(stored)
    pdf, status, reason = render_provider_choice(parse_md(reset["source"]), style, tmp_path,
                                                 monotonic() + 30, ShortFirstLine())
    assert (status, reason) == ("model", None)
    assert read_pages(pdf) == read_pages((PROOFS / "model.pdf").read_bytes())
    assert read_pages((tmp_path / "measure/book.pdf").read_bytes()) == read_pages((PROOFS / "rule.pdf").read_bytes())


def test_numbered_title_and_byline_keep_notes_and_margin_numbers(tmp_path):
    doc = parse_md("# 제8조 점검\n\n<!-- 쪽결 정렬=오른쪽 -->\n2026. 11. 23.  **점검팀**[^주]\n\n끝\n\n[^주]: 참고 기록")
    style = apply_style(Style(), {"article_expr": "제{숫자}조", "line_numbers": "left",
                                  "text_styles": {"body": {"align": "left"},
                                                  "heading1": {"frame": "box", "align": "center"}}})
    rows = baselines(typeset(doc, style, tmp_path))
    left, right = 18 * BP_PER_MM, 130 * BP_PER_MM
    numbered = []
    for _, chars in rows:
        labels = [c for c in chars if c.x1 < left]
        if labels:
            assert labels[-1].x1 == pytest.approx(left - 6 * BP_PER_PT, abs=.03)
            numbered.append((text(labels), [c for c in chars if c.x0 >= left - .03]))
    assert [label for label, _ in numbered] == ["1", "2", "3"]
    title, byline, ending = [chars for _, chars in numbered]
    assert text(title) == "제1조점검"
    assert (title[0].x0 + title[-1].x1) / 2 == pytest.approx((left + right) / 2, abs=.04)
    assert text(byline) == "2026.11.23.점검팀" and byline[-1].font.endswith("Bold")
    marker, = [c for _, chars in rows for c in chars if c.text == "1" and c.x1 > byline[-1].x1]
    # TeX includes half a point of scriptspace after a superscript in its box.
    assert marker.x1 + .5 * BP_PER_PT == pytest.approx(right, abs=.04)
    assert text(ending) == "끝" and ending[0].x0 == pytest.approx(left, abs=.04)
    assert any("참고기록" in text(chars) for _, chars in rows)


def test_body_alignment_stops_at_quoted_and_code_blocks(tmp_path):
    source = "Plain Text\n\n    CODE\n\n> QUOTE\n\n```text\nFENCED\n```\n"
    baseline = tmp_path / "baseline"
    baseline.mkdir()
    original = baselines(typeset(parse_md(source), Style(), baseline))
    style = apply_style(Style(), {"text_styles": {"body": {"align": "right"}}})
    aligned_dir = tmp_path / "aligned"
    aligned_dir.mkdir()
    aligned = baselines(typeset(parse_md(source), style, aligned_dir))
    assert [text(row) for _, row in original] == ["PlainText", "CODE", ">QUOTE", "```textFENCED```"]
    assert original[0][1][0].x0 == pytest.approx(18 * BP_PER_MM, abs=.04)
    assert aligned[0][1][-1].x1 == pytest.approx(130 * BP_PER_MM, abs=.04)
    assert aligned[1:] == original[1:]


def test_alignment_only_role_preserves_footnote_mark_appearance(tmp_path):
    source = "**굵은[^n]**\n\n[^n]: 참고"
    original = tmp_path / "original"
    aligned = tmp_path / "aligned"
    original.mkdir()
    aligned.mkdir()
    doc = parse_md(source)
    baseline = typeset(doc, Style(), original)
    unused_role = apply_style(Style(), {"text_styles": {"heading6": {"align": "center"}}})
    assert read_pages(typeset(doc, unused_role, aligned)) == read_pages(baseline)


def test_body_and_quote_share_legacy_line_until_body_alignment_is_set(tmp_path):
    doc = parse_md("본문\n> 인용")
    original, unrelated, aligned = tmp_path / "original", tmp_path / "unrelated", tmp_path / "aligned"
    original.mkdir()
    unrelated.mkdir()
    aligned.mkdir()
    legacy = typeset(doc, Style(), original)
    heading_only = apply_style(Style(), {"text_styles": {"heading1": {"align": "right"}}})
    assert read_pages(typeset(doc, heading_only, unrelated)) == read_pages(legacy)
    (_, joined), = baselines(legacy)
    assert text(joined) == "본문>인용" and joined[0].x0 == pytest.approx(18 * BP_PER_MM, abs=.04)
    style = apply_style(Style(), {"text_styles": {"body": {"align": "right"}}})
    (_, body), (_, quote) = baselines(typeset(doc, style, aligned))
    assert text(body) == "본문" and body[-1].x1 == pytest.approx(130 * BP_PER_MM, abs=.04)
    assert text(quote) == ">인용" and quote[0].x0 == pytest.approx(18 * BP_PER_MM, abs=.04)
