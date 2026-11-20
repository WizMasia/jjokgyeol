"""T11. Read final glyph coordinates independently of the Lua measurements."""
import json
import unicodedata
from dataclasses import replace
from itertools import accumulate

import pytest

from app.document import parse_md
from app.style import Style, apply_style
from app.typeset import typeset
from pdf_metrics import read_pages

BP_PER_SP = 72 / (72.27 * 65536)
BP_PER_MM = 72 / 25.4


def style_for(width, **patch):
    return apply_style(Style(), {
        "page_mm": [width + 20, 100], "margins_mm": [10] * 4, "paragraph_layout": "balanced", **patch,
    })


def render(folder, source, style, choices=None):
    folder.mkdir()
    pages = read_pages(typeset(parse_md(source), style, folder,
                               collect_candidates=True, paragraph_choices=choices))
    rows = [json.loads(line) for line in (folder / "paragraph-candidates.jsonl").read_text().splitlines()]
    return pages, rows


def letters(line):
    return [c for c in line.chars if not c.text.isspace()]


def printed(line):
    return "".join(c.text for c in letters(line))


def selected(row):
    return next(c for c in row["candidates"] if c["id"] == row["selected_id"])


def gap(a, b):
    return b.x0 - a.x0 - a.advance


def check_geometry(lines, candidate, left=10):
    end = 0
    for line, measured in zip(lines, candidate["lines"], strict=True):
        chars = letters(line)
        end += len(chars)
        assert measured["end_glyph"] == end
        assert (measured["first_char"], measured["last_char"]) == (ord(chars[0].text), ord(chars[-1].text))
        start = left * BP_PER_MM + measured["start_sp"] * BP_PER_SP
        assert chars[0].x0 == pytest.approx(start, abs=.04)
        right = chars[-1].x0 + chars[-1].advance
        remaining = start + measured["available_sp"] * BP_PER_SP - right
        assert remaining >= -.04
        assert remaining == pytest.approx(measured["remaining_sp"] * BP_PER_SP, abs=.05)


def test_spacing_limits_and_breaks(tmp_path):
    latin = []
    for pct in (0, -3, -7):
        style = style_for(65, ko_tracking_pct=pct)
        (page,), (row,) = render(tmp_path / str(pct), "한글 River 물결", style)
        line, = page.lines
        a, b, *rest = letters(line)
        assert gap(a, b) == pytest.approx(a.size * pct / 100, abs=.03)
        latin.append([gap(x, y) for x, y in zip(rest[:4], rest[1:5])])
        assert gap(b, rest[0]) == pytest.approx(b.size * 307 / 1024, abs=.03)
        assert selected(row)["lines"][0]["tracking_per_10000"] == pct * 100
    for advances in latin[1:]:
        assert advances == pytest.approx(latin[0], abs=.01)

    # Choose the type size for a natural four TeX point space in NanumMyeongjo.
    source = "강가 물결 River"
    fixed = style_for(65, body_pt="13.34202")
    (control,), _ = render(tmp_path / "ruler", source, fixed)
    chars = letters(control.lines[0])
    for i in (2, 4):
        assert gap(chars[i-1], chars[i]) / (72 / 72.27) == pytest.approx(4, abs=.02)
    # One TeX point less than the printed word group, measured from the control.
    width = round((chars[-1].x0 + chars[-1].advance - chars[0].x0) / BP_PER_SP) - 65536
    narrow = replace(fixed, page_sp=(sum(fixed.margins_sp[1::2]) + width, fixed.page_sp[1]))
    (page,), _ = render(tmp_path / "fixed", source, narrow)
    assert list(map(printed, page.lines)) == ["강가물결", "River"]
    (page,), (row,) = render(tmp_path / "variable", source, apply_style(narrow, {"space_pct": [70, 130]}))
    assert list(map(printed, page.lines)) == ["강가물결River"]
    check_geometry(page.lines, selected(row))
    chars = letters(page.lines[0])
    ratio = selected(row)["lines"][0]["space_per_10000"]
    for i in (2, 4):
        actual = gap(chars[i-1], chars[i]) / (72 / 72.27)
        assert 2.8 - .02 <= actual < 4
        assert actual == pytest.approx(4 * ratio / 10000, abs=.02)


def test_rule_avoids_single_glyph_tail_and_replays_other_break(tmp_path):
    source, style = "강가 가나 다른 끝", style_for(25)
    (page,), (row,) = render(tmp_path / "rule", source, style)
    assert 2 <= len(row["candidates"]) <= 4
    assert row["selected_id"] == row["rule_id"]
    assert list(map(printed, page.lines)) == ["강가가나", "다른끝"]
    check_geometry(page.lines, selected(row))
    other = next(c for c in row["candidates"] if c["lines"][0]["end_glyph"] == 6)
    (page,), (again,) = render(tmp_path / "other", source, style, {row["block"]: other["id"]})
    assert again["selected_id"] == other["id"] and again["candidates"] == row["candidates"]
    check_geometry(page.lines, other)
    for key, text, width, wanted in [("unknown", source, 25, "unknown"),
                                     ("width", source, 32, other["id"]),
                                     ("text", source.replace("강가", "나루"), 25, other["id"])]:
        (page,), (fallback,) = render(tmp_path / key, text, style_for(width), {1: wanted})
        assert fallback["rule_id"] == fallback["selected_id"] != wanted
        check_geometry(page.lines, selected(fallback))


def test_line_correction_and_floor_in_pdf(tmp_path):
    source = "가나다라 마바사아 끝말"
    for base in (0, -7):
        style = style_for(29.8, ko_tracking_pct=base, ko_tracking_bounds_pct=[-7, 0])
        (page,), (row,) = render(tmp_path / str(base), source, style)
        candidates = row["candidates"]
        corrected = next(c for c in candidates if c["lines"][0]["end_glyph"] == 8)
        (page,), (result,) = render(tmp_path / ("replay" + str(base)), source, style, {1: corrected["id"]})
        assert result["selected_id"] == corrected["id"]
        values = [line["tracking_per_10000"] for line in corrected["lines"]]
        assert -700 <= values[0] < 0 and values[-1] == base * 100
        for line, ratio in zip(page.lines, values, strict=True):
            a, b = letters(line)[:2]
            assert gap(a, b) == pytest.approx(a.size * ratio / 10000, abs=.03)
        check_geometry(page.lines, corrected)


def test_two_widths_punctuation_clusters_and_notes(tmp_path):
    long_word = unicodedata.normalize("NFD", "가각") * 8
    url = "https://example.test/letters/1234567890"
    source = f"강가 ( 여름 ) “달빛” Markdown PDF {url} {long_word} 표식[^주]\n\n[^주]: 각주확인"
    breaks = []
    for width in (26, 39):
        (page,), (row,) = render(tmp_path / str(width), source, style_for(width, rest_indent_mm=2))
        body = [line for line in page.lines if any(c.size > 10 for c in line.chars)]
        text = list(map(printed, body))
        assert "".join(text).rstrip("1") == unicodedata.normalize("NFC", "".join(source.split("[^주]")[0].split()))
        assert all(not s.startswith((")", "”", ".")) and not s.endswith(("(", "“")) for s in text)
        assert any("Markdown" in s for s in text) and any("PDF" in s for s in text)
        assert all(line.chars[-1].x0 + line.chars[-1].advance <= (10 + width) * BP_PER_MM + .05 for line in body)
        features = selected(row)["lines"]
        assert list(accumulate(len(s.rstrip("1")) if s.endswith("표식1") else len(s) for s in text)) == [
            line["end_glyph"] for line in features
        ]
        assert any(line["split_word"] for line in features)
        assert any("각주확인" in printed(line) for line in page.lines)
        breaks.append(text)
    assert breaks[0] != breaks[1]


@pytest.mark.parametrize("width", [26, 39])
def test_title_stays_with_next_at_page_edge(tmp_path, width):
    style = apply_style(style_for(width), {"page_mm": [width + 20, 49]})
    source = ("도입\n\n" * 4 + "# 표제\n\n첫본문\n\n") * 4
    pages, _ = render(tmp_path / "book", source, style)
    assert len(pages) > 2
    assert any(printed(p.lines[0]) == "표제" for p in pages[1:])
    heads = 0
    for page in pages:
        rows = list(map(printed, page.lines))
        for i, text in enumerate(rows):
            if text == "표제":
                heads += 1
                assert rows[i+1:i+2] == ["첫본문"]
    assert heads == 4



def test_expanded_candidate_reports_actual_space_widths(tmp_path):
    style = style_for(25, space_pct=[100, 130], ko_tracking_bounds_pct=[0, 2])
    source = "강가 가나 다른 끝"
    _, (row,) = render(tmp_path / "collect", source, style)
    wider = next(c for c in row["candidates"] if c["lines"][0]["space_per_10000"] > 10000)
    (page,), _ = render(tmp_path / "replay", source, style, {1: wider["id"]})
    for actual, line in zip(page.lines, wider["lines"], strict=True):
        chars = letters(actual)
        words = [word for word in source.split() if word in printed(actual)]
        pos, gaps = 0, []
        for word in words[:-1]:
            pos += len(word)
            gaps.append(gap(chars[pos-1], chars[pos]))
        assert [sp * BP_PER_SP for sp in line.get("spaces_sp", [])] == pytest.approx(gaps, abs=.03)
        assert 10000 <= line["space_per_10000"] <= 13000
        assert 0 <= line["tracking_per_10000"] <= 200
    assert wider["lines"][-1]["space_per_10000"] == 10000
    assert wider["lines"][-1]["tracking_per_10000"] == 0
    check_geometry(page.lines, wider)


def test_consecutive_spaces_and_wrapped_standalone_quotes(tmp_path):
    source = "한강   물결  여름"
    (page,), (row,) = render(tmp_path / "spaces", source, style_for(55))
    chars = letters(page.lines[0])
    for i, count in [(2, 3), (4, 2)]:
        assert gap(chars[i-1], chars[i]) == pytest.approx(count * chars[0].size * 307 / 1024, abs=.03)
    assert len(selected(row)["lines"][0]["spaces_sp"]) == 5
    assert json.loads((tmp_path / "spaces/input.json").read_text())["blocks"][0]["inlines"][0]["text"] == source
    (page,), _ = render(tmp_path / "quotes", "한강   “  여름  ”   끝", style_for(17))
    assert all(not printed(line).startswith("”") and not printed(line).endswith("“") for line in page.lines)
    assert "".join(map(printed, page.lines)) == "한강“여름”끝"


def test_replay_without_collection_preserves_note_on_reference_page(tmp_path):
    source = ("앞말\n\n" * 3 + "강가 가나 다른 끝[^강]\n\n[^강]: 물길을 확인")
    style = apply_style(Style(), {"page_mm": [45, 58], "margins_mm": [10] * 4})
    _, reports = render(tmp_path / "rule", source, style)
    row = reports[-1]
    assert row["block"] == 4
    assert len({c["lines"][0]["end_glyph"] for c in row["candidates"]}) >= 2
    for index, candidate in enumerate(row["candidates"]):
        folder = tmp_path / str(index)
        folder.mkdir()
        pdf = typeset(parse_md(source), style, folder, paragraph_choices={4: candidate["id"]})
        assert not (folder / "paragraph-candidates.jsonl").exists()
        body, notes, markers = [], [], []
        for page_no, page in enumerate(read_pages(pdf)):
            for line in page.lines:
                text = printed(line)
                if "물길을확인" in text:
                    notes.append(page_no)
                elif text != "앞말":
                    body.append(text.rstrip("1"))
                    if text.endswith("끝1"):
                        markers.append(page_no)
        assert len(markers) == 1 and markers == notes
        ends = list(accumulate(map(len, body)))
        assert ends == [line["end_glyph"] for line in candidate["lines"]]


@pytest.mark.parametrize(("patch", "request_options", "first"), [
    ({"text_styles": {"heading1": {"color": "#123456"}}, "space_pct": [70, 130]}, {}, "강가가나다른"),
    ({"paragraph_layout": "balanced"}, {}, "강가가나"),
    ({"ko_tracking_bounds_pct": [0, 0]}, {}, "강가가나"),
    ({}, {"collect_candidates": True}, "강가가나"),
    ({}, {"paragraph_choices": {}}, "강가가나"),
])
def test_normal_and_explicit_candidate_requests(tmp_path, patch, request_options, first):
    style = apply_style(Style(), {"page_mm": [45, 100], "margins_mm": [10] * 4, **patch})
    page, = read_pages(typeset(parse_md("강가 가나 다른 끝"), style, tmp_path, **request_options))
    assert printed(page.lines[0]) == first
    assert (tmp_path / "paragraph-candidates.jsonl").exists() == bool(request_options.get("collect_candidates"))
