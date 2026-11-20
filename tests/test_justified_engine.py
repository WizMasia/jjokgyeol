"""Read the advances in the shipped PDF independently of the candidate log."""
import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.document import parse_md
from app.style import Style, apply_style, from_stored_style
from app.typeset import typeset
from pdf_metrics import read_pages
from test_list_typeset import BP_PER_MM, baselines, text


BP_PER_PT = 72 / 72.27
SP = 65536


def publication(width_pt, **patch):
    style = apply_style(Style(), {"margins_mm": [10] * 4, "ko_tracking_bounds_pct": [-7, 15],
                                  "space_pct": [70, 130], **patch})
    return replace(style, page_sp=(2 * style.margins_sp[1] + round(width_pt * SP), style.page_sp[1]),
                   paragraph_layout="justified")


def render(folder, source, style, choices=None):
    folder.mkdir()
    pdf = typeset(parse_md(source), style, folder, collect_candidates=True, paragraph_choices=choices)
    return ([chars for _, chars in baselines(pdf)],
            [json.loads(line) for line in (folder / "paragraph-candidates.jsonl").read_text().splitlines()])


def edges(lines):
    return [chars[-1].x0 + chars[-1].advance for chars in lines]


def test_global_breaks_and_both_measured_allocations(tmp_path):
    lengths = [5, 4, 5, 2, 6, 2, 2, 2, 5, 5, 4, 5, 4, 2, 4, 3]
    words = ["가" * n for n in lengths]
    source, style = " ".join(words), publication(165)
    lines, (row,) = render(tmp_path / "rule", source, style)
    right = 10 * BP_PER_MM + 165 * BP_PER_PT
    # The fourth word fits at minimum spacing, but taking it strands a later line.
    assert len(lines[0]) == sum(lengths[:3])
    assert len(row["candidates"]) >= 2
    prints = set()
    for i, option in enumerate(row["candidates"]):
        actual, (replay,) = render(tmp_path / str(i), source, style, {1: option["id"]})
        assert replay["selected_id"] == option["id"]
        assert edges(actual)[:-1] == pytest.approx([right] * (len(actual)-1), abs=.05)
        word = 0
        for chars, measured in zip(actual, option["lines"], strict=True):
            advances = []
            while len(advances) < len(chars):
                size = lengths[word]
                advances.extend(["space" if j == 0 and advances else "track" for j in range(size)])
                word += 1
            assert len(advances) == len(chars) and not measured["split_word"]
            for a, b, kind in zip(chars, chars[1:], advances[1:]):
                gap = b.x0 - a.x0 - a.advance
                nominal = a.size if kind == "track" else a.size * 307 / 1024
                low, high = (-.07, .15) if kind == "track" else (.7, 1.3)
                assert low * nominal - .025 <= gap <= high * nominal + .025
                if chars is actual[-1]:
                    assert gap == pytest.approx(0 if kind == "track" else nominal, abs=.025)
        assert word == len(words)
        prints.add(tuple(round(c.x0, 3) for line in actual for c in line))
    assert len(prints) == len(row["candidates"])


def test_justified_report_list_with_gloss_and_hanging_indent(tmp_path):
    path = Path(__file__).resolve().parents[1] / "app/static/general-report.json"
    style = from_stored_style(json.loads(path.read_bytes()))
    style = replace(style, paragraph_layout="justified", space_per_10000=(7000, 13000),
                    ko_tracking_bounds_per_10000=(-700, 1500))
    phrase = "오래된 **지도** 위에 River 물줄기를 [기호]{풀이=작은 표식} 하나로 그린다. "
    source = "- 지도\n  - " + phrase * 6
    lines, rows = render(tmp_path / "report", source, style)
    assert text(lines[0]) == "□지도" and text(lines[1]).startswith("○")
    assert len(lines) > 4
    group = "기호(작은표식)"
    assert sum(text(line).count(group) for line in lines) == 6
    assert sum(text(line).count("River") for line in lines) == 6
    right = (210 - 18) * BP_PER_MM
    assert edges(lines[1:-1]) == pytest.approx([right] * (len(lines)-2), abs=.05)
    assert all(line[0].x0 == pytest.approx(lines[1][1].x0, abs=.03) for line in lines[2:])
    assert rows[-1]["block"] == 2


def test_infeasible_line_uses_largest_legal_gap_and_keeps_word(tmp_path):
    style = publication(40, ko_tracking_bounds_pct=[0, 0], space_pct=[95, 105])
    lines, (row,) = render(tmp_path / "tight", "가 나 River 끝", style)
    assert list(map(text, lines)) == ["가나", "River끝"]
    a, b = lines[0]
    assert b.x0 - a.x0 - a.advance == pytest.approx(a.size * 307/1024 * 1.05, abs=.02)
    assert all(not line["split_word"] for c in row["candidates"] for line in c["lines"])


def test_fractional_ratio_step_on_wide_line(tmp_path):
    style = publication(2902.37)
    word = "가" * 265
    lines, _ = render(tmp_path / "wide", word + " " + word, style)
    assert list(map(text, lines)) == [word, word]
    assert edges(lines)[0] == pytest.approx(10 * BP_PER_MM + 2902.37 * BP_PER_PT, abs=.05)
    assert edges(lines)[1] < edges(lines)[0] - 10


def test_equally_aligned_word_breaks_with_fixed_word_spaces(tmp_path):
    style = publication(250, ko_tracking_bounds_pct=[-15, 15], space_pct=[100, 100])
    _, (row,) = render(tmp_path / "options", " ".join(["강물"] * 18), style)
    ends = {c["lines"][0]["end_glyph"] for c in row["candidates"]}
    assert ends == {20, 22}
    assert all(c["lines"][0]["remaining_sp"] == 0 for c in row["candidates"])


def test_gloss_wider_than_continuation_keeps_its_base(tmp_path):
    style = publication(120, rest_indent_mm=26)
    source = "여기에 먼저 여러 어절을 적고 [판형]{풀이=책의 크기와 모양} 끝"
    lines, _ = render(tmp_path / "gloss", source, style)
    assert "".join(map(text, lines)) == "여기에먼저여러어절을적고판형(책의크기와모양)끝"
    assert any("판형" in text(line) for line in lines)
    assert max(edges(lines)) <= 10 * BP_PER_MM + 120 * BP_PER_PT + .05


def test_gothic_body_top_margin_on_first_and_following_pages(tmp_path):
    style = replace(apply_style(Style(), {"body_font": "nanum-gothic", "body_pt": 16, "leading_pt": 24,
                                         "ko_tracking_bounds_pct": [-15, 15], "space_pct": [70, 130]}),
                    paragraph_layout="justified")
    para = "물안개 속에서 배를 기다리며 멀리 보이는 다리를 바라보았다. " * 4
    source = (para + "\n\n") * 5 + "# 기록\n\n갑문[^주]\n\n" + para + "\n\n[^주]: 물길을 기록한다."
    pages = read_pages(typeset(parse_md(source), style, tmp_path))
    assert len(pages) >= 2
    for page in pages:
        assert max(c.y1 for line in page.lines for c in line.chars) <= page.height - 20 * BP_PER_MM + .02
    on_page = ["".join(line.text.replace(" ", "") for line in p.lines) for p in pages]
    assert any("기록갑문1" in s and "물길을기록한다." in s for s in on_page)

    work = tmp_path / "heading"
    work.mkdir()
    heading, = read_pages(typeset(parse_md("# 기록\n\n갑문[^주]\n\n[^주]: 물길"), style, work))
    assert heading.lines[0].text == "기록"
    assert max(c.y1 for c in heading.lines[0].chars) <= heading.height - 20 * BP_PER_MM + .02
