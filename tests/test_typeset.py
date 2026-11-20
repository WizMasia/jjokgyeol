"""T3 runs stock LuaLaTeX. The candidate entry point is for T6 only."""
import json
from pathlib import Path

import pytest
from pdf_metrics import read_pages

FIXTURES = Path(__file__).with_name("fixtures")
CASES = json.loads((FIXTURES / "layout-cases.json").read_text(encoding="utf-8"))
SOURCE = (FIXTURES / "page-edge.md").read_text(encoding="utf-8")


def render_candidate_for_test(source, cases, label, workdir):
    from app.typeset import _render
    from app.document import parse_md
    from app.style import Style, apply_style

    candidate = cases["candidates"][label]
    lines = candidate["lines"]
    breaks = []
    words = 0
    for line in lines[:-1]:
        words += len(line.split())
        breaks.append(words)
    style = apply_style(Style(), cases["style"])
    fixed = apply_style(style, {"ko_tracking_pct": candidate["tracking_pct"],
                                "space_pct": [candidate["space_pct"]] * 2})
    return _render(parse_md(source), style, workdir, {
        "block": cases["paragraph_index"] + 1,
        "breaks": breaks,
        "space_per_10000": fixed.space_per_10000[0],
        "ko_tracking_per_10000": fixed.ko_tracking_per_10000,
    })


def rows(pdf):
    return [(i, line) for i, page in enumerate(read_pages(pdf), 1) for line in page.lines]


def compact(text):
    return "".join(text.split())


def test_literal_pdf(tmp_path):
    from app.typeset import typeset
    from app.document import parse_md
    from app.style import Style

    source = r"""# 표제

예약 % & # $ ^ ~ {문자} ^^41 -- \relax 와 **굵게** *강조* 끝[^주]

[^주]: 원문 \relax {기호} 17%"""
    pdf = typeset(parse_md(source), Style(), tmp_path)
    text = compact("".join(line.text for _, line in rows(pdf)))
    for phrase in ("표제", "예약%&#$^~{문자}^^41--", r"\relax", "굵게", "강조", r"원문\relax{기호}17%"):
        assert phrase in text
    assert {p.name for p in tmp_path.iterdir()} <= {
        "input.json", "book.pdf", "book.log", "book.aux", "fonts", "texcache"
    }


@pytest.fixture(scope="module")
def layouts(tmp_path_factory):
    return {label: render_candidate_for_test(SOURCE, CASES, label, tmp_path_factory.mktemp(label))
            for label in CASES["candidates"]}


def test_two_layouts(layouts):
    actual = {}
    for label, pdf in layouts.items():
        expected = CASES["candidates"][label]["lines"]
        starts = tuple(line.split()[0] for line in expected)
        actual[label] = [compact(line.text).rstrip("0123456789")
                         for _, line in rows(pdf) if line.text.startswith(starts)]
        assert actual[label] == [compact(line) for line in expected]
    assert actual["A"] != actual["B"]


def test_note_follows_reference(layouts):
    for label, pdf in layouts.items():
        placed = rows(pdf)

        def locate(phrase):
            return [(number, line.chars[-1].y0) for number, line in placed
                    if compact(phrase) in compact(line.text)]

        (ref_page, ref_y), = locate(CASES["reference_text"])
        (note_page, note_y), = locate(CASES["footnote_text"])
        assert ref_page == note_page == CASES["candidates"][label]["reference_page"]
        assert 15 * 72 / 25.4 - 2 < note_y < min(ref_y, 70 * 72 / 25.4 / 2)


def test_pdf_spacing(layouts):
    latin = {}
    for label, pdf in layouts.items():
        spec = CASES["candidates"][label]
        pages = read_pages(pdf)
        for page in pages:
            assert (page.width, page.height) == pytest.approx(
                [mm * 72 / 25.4 for mm in CASES["style"]["page_mm"]], abs=0.05
            )
        latin[label] = []
        for text in spec["lines"]:
            line, = [line for page in pages for line in page.lines
                     if compact(line.text).rstrip("0123456789") == compact(text)]
            chars = iter(c for c in line.chars if not c.text.isspace())
            prev = None
            for word in text.split():
                for index, letter in enumerate(word):
                    c = next(chars)
                    assert c.text == letter
                    assert c.size == pytest.approx(CASES["style"]["body_pt"] * 72 / 72.27, abs=0.05)
                    assert "NanumMyeongjo" in c.font
                    assert c.x0 >= 18 * 72 / 25.4 - 0.05
                    assert c.x0 + c.advance <= (148 - 18) * 72 / 25.4 + 0.05
                    if prev:
                        gap = c.x0 - prev.x0 - prev.advance
                        if not index:
                            assert gap == pytest.approx(c.size * 307 / 1024 * spec["space_pct"] / 100, abs=0.05)
                        elif all("가" <= ch <= "힣" for ch in (prev.text, letter)):
                            assert gap == pytest.approx(c.size * spec["tracking_pct"] / 100, abs=0.05)
                        elif word.isascii():
                            latin[label].append(gap)
                    prev = c
    assert len(latin["A"]) == len("RiverWalk") - 2
    assert latin["A"] == pytest.approx(latin["B"], abs=0.05)


def test_tracking_preserves_latin_spacing(tmp_path):
    from app.document import parse_md
    from app.style import Style, apply_style
    from app.typeset import typeset

    latin = []
    for tracking in (0, -7):
        work = tmp_path / str(tracking)
        work.mkdir()
        pdf = typeset(parse_md("강가 Walk"), apply_style(Style(), {"ko_tracking_pct": tracking}), work)
        (_, line), = rows(pdf)
        ko1, ko2, *word = [c for c in line.chars if not c.text.isspace()]
        assert ko2.x0 - ko1.x0 - ko1.advance == pytest.approx(ko1.size * tracking / 100, abs=0.05)
        latin.append([b.x0 - a.x0 - a.advance for a, b in zip(word, word[1:])])
    assert latin[0] == pytest.approx(latin[1], abs=0.05)


def test_rule_layout_can_shrink_space_to_fit(tmp_path):
    from app.document import parse_md
    from app.style import Style, apply_style
    from app.typeset import typeset

    # Four NanumMyeongjo syllables (0.95em each) and one 0.30em space.
    width_mm = 11 * 4.05 * 25.4 / 72.27
    for low, expected in ((100, 2), (70, 1)):
        work = tmp_path / str(low)
        work.mkdir()
        style = apply_style(Style(), {"page_mm": [width_mm + 36, 100], "space_pct": [low, 100]})
        pdf = typeset(parse_md("강가 지도"), style, work)
        lines = rows(pdf)
        assert len(lines) == expected
        if expected == 1:
            chars = [c for c in lines[0][1].chars if not c.text.isspace()]
            gap = chars[2].x0 - chars[1].x0 - chars[1].advance
            natural = chars[1].size * 307 / 1024
            assert 0.7 * natural - 0.05 <= gap < natural - 0.05


def test_ideographic_indent_moves_first_hangul(tmp_path):
    from app.document import parse_md
    from app.style import Style
    from app.typeset import typeset

    source = "강가\n\n\u3000강가\n\n\u3000\u3000강가"
    pdf = typeset(parse_md(source), Style(), tmp_path)
    starts = [c.x0 for _, line in rows(pdf) for c in line.chars if c.text == "강"]

    # NanumMyeongjo gives U+3000 an advance of 973 units per 1024 em.
    full_space = 11 * 72 / 72.27 * 973 / 1024
    margin = 18 * 72 / 25.4
    assert starts == pytest.approx([margin, margin + full_space, margin + 2 * full_space], abs=0.05)


def test_line_local_integer_nodes(tmp_path, monkeypatch):
    """Measure the actual packed line, before PDF coordinates lose sp precision."""
    import importlib
    import shutil
    from app.document import parse_md
    from app.style import Style, apply_style

    engine = importlib.import_module("app.typeset")
    templates = tmp_path / "tex"
    shutil.copytree(engine.TEMPLATES, templates)
    monkeypatch.setattr(engine, "TEMPLATES", templates)
    probe = r"""
local file = assert(io.open("lines.sp", "w"))
luatexbase.add_to_callback("post_linebreak_filter", function(head)
  for line in node.traverse_id(node.id("hlist"), head) do
    local chars, adjustments, spaces = 0, {}, {}
    for n in node.traverse(line.head) do
      if n.id == node.id("glyph") then chars = chars + 1
      elseif n.id == node.id("glue") and n.subtype == 0 and n.width ~= 0 then
        adjustments[#adjustments+1] = tostring(n.width)
      elseif n.id == node.id("glue") and n.subtype == 13 then
        spaces[#spaces+1] = tostring(n.width)
      elseif n.id == node.id("kern") and n.kern ~= 0 then
        adjustments[#adjustments+1] = tostring(n.kern)
      end
    end
    file:write(chars, " ", table.concat(adjustments, ","), " ", table.concat(spaces, ","), "\n")
  end
  file:flush()
  return head
end, "test.read_packed_lines")
"""
    (templates / "probe.lua").write_text(probe, encoding="utf-8")
    book = templates / "book.tex"
    # luatexbase invokes post_linebreak_filter in reverse registration order.
    book.write_text(book.read_text().replace(r"\directlua{book =",
                    r'\directlua{require("probe")}\directlua{book ='))
    work = tmp_path / "job"
    work.mkdir()
    short, long = "가가가 다 다", "가" * 101 + " 다 다 다 다"
    style = apply_style(Style(), {"page_mm": [450, 210], "ko_tracking_pct": -7})
    pdf = engine._render(parse_md(short + " " + long), style, work, {
        "block": 1, "breaks": [3], "space_per_10000": 9001,
        "ko_tracking_per_10000": -700,
    })
    output = [line.split() for line in (work / "lines.sp").read_text().splitlines()]
    assert [int(line[0]) for line in output] == [5, 105]
    cumulative = [-((n * 11 * 65536 * 700 + 5000) // 10000) for n in range(101)]
    expected = [b - a for a, b in zip(cumulative, cumulative[1:])]
    first, second = [[int(v) for v in line[1].split(",")] for line in output]
    assert first == expected[:2]
    assert second == expected
    assert sum(second) == -5046272
    natural = 307 * style.body_sp // 1024
    cumulative_space = [(n * natural * 9001 + 5000) // 10000 for n in range(5)]
    spaces = [b - a for a, b in zip(cumulative_space, cumulative_space[1:])]
    assert [[int(v) for v in line[2].split(",")] for line in output] == [spaces[:2], spaces]
    assert [compact(line.text) for _, line in rows(pdf)] == [compact(short), compact(long)]


def test_candidate_tracking_is_paragraph_local(tmp_path):
    pdf = render_candidate_for_test(SOURCE, CASES, "B", tmp_path)
    before, = [line for _, line in rows(pdf) if line.text.startswith("강둑")]
    target, = [line for _, line in rows(pdf) if line.text.startswith("강가")]
    for line, pct in ((before, 0), (target, -7)):
        a, b = line.chars[:2]
        assert b.x0 - a.x0 - a.advance == pytest.approx(a.size * pct / 100, abs=0.05)


@pytest.mark.parametrize(("prefix", "tail", "room_sp", "expected"), [
    ("가가", "나나 다", 0, ["가가", "나나다"]),
    ("가가가", "나나나 다", -1, ["가가가", "나나나", "다"]),
])
def test_rule_line_boundary_to_one_sp(tmp_path, prefix, tail, room_sp, expected):
    from dataclasses import replace
    from app.document import parse_md
    from app.style import Style, apply_style
    from app.typeset import typeset

    style = apply_style(Style(), {"ko_tracking_pct": -7})
    glyph = 973 * style.body_sp // 1024
    space = 307 * style.body_sp // 1024
    gaps = len(prefix) - 1
    correction = -((gaps * style.body_sp * 700 + 5000) // 10000)
    first_width = glyph * len(prefix) + correction
    rest_width = first_width + space + glyph + room_sp
    style = replace(style, page_sp=(sum(style.margins_sp[1::2]) + rest_width, style.page_sp[1]),
                    first_indent_sp=rest_width - first_width)
    pdf = typeset(parse_md(prefix + " " + tail), style, tmp_path)
    assert [compact(line.text) for _, line in rows(pdf)] == expected


def test_composed_clusters_keep_one_tracking_gap(tmp_path):
    import unicodedata
    from app.document import parse_md
    from app.style import Style, apply_style
    from app.typeset import typeset

    source = unicodedata.normalize("NFD", "가각")
    pdf = typeset(parse_md(source), apply_style(Style(), {"ko_tracking_pct": -7}), tmp_path)
    (_, line), = rows(pdf)
    a, b = line.chars
    assert a.text + b.text == "가각"
    assert b.x0 - a.x0 - a.advance == pytest.approx(-a.size * .07, abs=.05)
    data = json.loads((tmp_path / "input.json").read_text())
    assert data["blocks"][0]["inlines"][0]["text"] == source


def test_heading_and_note_use_their_own_font_size_and_origin(tmp_path):
    from app.document import parse_md
    from app.style import Style, apply_style
    from app.typeset import typeset

    style = apply_style(Style(), {"body_pt": "11.5", "ko_tracking_pct": "-7.25",
                                  "first_indent_mm": 5, "rest_indent_mm": 7})
    pdf = typeset(parse_md("# 표제\n\n본문[^주]\n\n[^주]: 각주"), style, tmp_path)
    placed = rows(pdf)
    for pair, pt, left in (("표제", 18.4, 18), ("본문", 11.5, 23), ("각주", 8, None)):
        line, = [line for _, line in placed if pair in line.text]
        first, second = [c for c in line.chars if c.text in pair]
        assert first.size == pytest.approx(pt * 72 / 72.27, abs=0.01)
        assert second.x0 - first.x0 - first.advance == pytest.approx(-first.size * .0725, abs=.05)
        if left is not None:
            assert first.x0 == pytest.approx(left * 72 / 25.4, abs=.05)
        else:
            # The stock footnote mark has its own label indent.
            assert first.x0 == pytest.approx(18 * 72 / 25.4 + 1.8 * first.size, abs=.05)


def test_rounded_space_floor_is_respected_to_one_sp(tmp_path):
    from dataclasses import replace
    from app.document import parse_md
    from app.style import Style, apply_style
    from app.typeset import typeset

    style = apply_style(Style(), {"space_pct": [70, 90]})
    # 70% of the font space is 151289.6sp. The minimum rounds to 151290sp.
    width = 2 * (973 * style.body_sp // 1024) + 151289
    style = replace(style, page_sp=(sum(style.margins_sp[1::2]) + width, style.page_sp[1]))
    assert [line.text for _, line in rows(typeset(parse_md("가 나"), style, tmp_path))] == ["가", "나"]


def test_publication_policy_sets_heading_metrics_and_font_faces(tmp_path, monkeypatch):
    import app.typeset as engine
    from app.document import parse_md
    from app.style import Style, apply_style

    # Distinct regular and bold mappings catch deriving one filename from the other.
    policy = {
        "fonts": {
            "nanum-myeongjo": {"regular": "NanumGothic.ttf", "bold": "NanumMyeongjoBold.ttf"},
            "nanum-gothic": {"regular": "NanumGothic.ttf", "bold": "NanumMyeongjo.ttf"},
        },
        "heading_per_10000": [20000, 12345, 15000, 14000, 13000, 12000],
        "heading_leading_per_10000": 16500,
    }
    monkeypatch.setattr(engine, "engine_policy", lambda: policy)
    style = apply_style(Style(), {"body_pt": 11, "page_mm": [51, 120]})
    pdf = engine.typeset(parse_md("## 달빛 샛별\n\n봄 **눈**"), style, tmp_path)
    chars = {c.text: c for _, line in rows(pdf) for c in line.chars}
    heading_sp = (11 * 65536 * 12345 + 5000) // 10000
    leading_sp = (heading_sp * 16500 + 5000) // 10000
    bp_per_sp = 72 / (72.27 * 65536)
    assert chars["달"].size == pytest.approx(heading_sp * bp_per_sp, abs=.001)
    assert chars["달"].baseline - chars["샛"].baseline == pytest.approx(leading_sp * bp_per_sp, abs=.002)
    assert {c: chars[c].font.split("+")[-1] for c in ("달", "봄", "눈")} == {
        "달": "NanumMyeongjo", "봄": "NanumGothic", "눈": "NanumMyeongjoBold",
    }


def test_engine_font_policy_requires_literal_basenames(tmp_path, monkeypatch):
    import subprocess
    import app.typeset as engine
    from app.document import parse_md
    from app.style import Style

    policy = engine.engine_policy()
    # Even a path to the same installed file must pass the basename boundary.
    policy["fonts"]["nanum-myeongjo"]["regular"] = "../fonts/NanumMyeongjo.ttf"
    monkeypatch.setattr(engine, "engine_policy", lambda: policy)
    with pytest.raises(subprocess.CalledProcessError):
        engine.typeset(parse_md("글"), Style(), tmp_path)
    assert "invalid font basename" in (tmp_path / "book.log").read_text()
