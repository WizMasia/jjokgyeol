"""Read the physical pages emitted by LuaLaTeX, including split footnotes."""
from dataclasses import replace

import pytest

from app.document import InputError, parse_md
from app.style import SectionRule, Style, apply_style
from app.typeset import typeset
from pdf_metrics import read_pages
from test_reference_typeset import color, glyphs


MM = 72 / 25.4


def test_prints_literal_text_and_physical_number_in_selected_face(tmp_path):
    # Exercise the renderer directly with a canonical, already validated rule.
    header = SectionRule((), "header", tuple({
        "visible": True, "left": r"\foo%#{{현재쪽}}", "center": "해", "right": "제{현재쪽}쪽",
        "font": "nanum-gothic", "font_sp": 9 * 65536, "color": "#336699", "offset_sp": 6 * 65536,
    }.items()))
    style = replace(Style(), heading_font="nanum-myeongjo", section_rules=(header,))
    pdf = typeset(parse_md("원고"), style, tmp_path)
    page, = read_pages(pdf)
    top = [c for line in page.lines for c in line.chars if c.baseline > page.height - 17 * MM]
    assert "".join(c.text for c in sorted(top, key=lambda c: c.x0)) == r"\foo%#{현재쪽}해제1쪽"
    assert all(c.font.endswith("NanumGothic") and abs(c.size - 9 * 72 / 72.27) < .015 for c in top)
    assert top[0].x0 == pytest.approx(18 * MM, abs=.02)
    assert max(c.x1 for c in top) == pytest.approx(page.width - 18 * MM, abs=.02)
    center, = [c for c in top if c.text == "해"]
    assert (center.x0 + center.x1) / 2 == pytest.approx(page.width / 2, abs=.02)
    measured = {c.get_text(): color(c) for c in glyphs(pdf)}
    assert measured["해"] == pytest.approx((.2, .4, .6), abs=.001)
    assert measured["원"] == (0, 0, 0)


def rule(role, path=(), **values):
    return {"path": list(path), "role": role, "values": values}


def render(source, path, rules, page=(90, 100)):
    style = apply_style(Style(), {"page_mm": list(page), "margins_mm": [15, 12, 15, 12],
                                  "section_rules": rules})
    return read_pages(typeset(parse_md(source), style, path))


def regions(page):
    header, body, footer = [], [], []
    for line in page.lines:
        for c in line.chars:
            target = header if c.baseline > page.height - 14 * MM else footer if c.baseline < 14 * MM else body
            target.append(c)
    return "".join(c.text for c in header), body, "".join(c.text for c in footer)


def test_first_line_and_last_manuscript_line_on_footnote_only_pages(tmp_path):
    source = "# 표지\n\n달\n\n# 부록\n\n쌀[^주]\n\n[^주]: " + "각주 " * 450
    rules = [rule("header", visible=True, left="윗공통", offset_mm=3),
             rule("header", ["표지"], left="윗표지"), rule("header", ["부록"], left="윗부록"),
             rule("footer", visible=True, center="밑{현재쪽}", offset_mm=3),
             rule("footer", ["표지"], visible=False)]
    pages = render(source, tmp_path, rules)
    assert len(pages) >= 3
    for i, page in enumerate(pages, 1):
        top, chars, bottom = regions(page)
        manuscript = "".join(c.text for c in chars)
        if i == 1:
            assert "표지달부록쌀" in manuscript
            assert (top, bottom) == ("윗표지", "")
        else:
            assert "각주" in manuscript and "쌀" not in manuscript
            assert (top, bottom) == ("윗부록", f"밑{i}")


def test_wrapped_paragraph_and_midpage_heading_follow_first_line(tmp_path):
    source = "# 표지\n\n" + "달" * 170 + "\n\n# 부록\n\n" + "쌀" * 470
    rules = [rule("header", visible=True, center="앞", offset_mm=3), rule("header", ["부록"], center="뒤")]
    pages = [regions(p) for p in render(source, tmp_path, rules, (85, 76))]
    texts = ["".join(c.text for c in body) for _, body, _ in pages]
    assert any(text.startswith("달") and "부록" in text for text in texts)
    assert any(text.startswith("쌀") for text in texts)
    assert [top for top, _, _ in pages] == ["뒤" if text.startswith(("부록", "쌀")) else "앞" for text in texts]


def test_fenced_setext_keeps_body_and_note_geometry_with_running_heads(tmp_path):
    source = "# 소개\n\n~~~\n# 예시\n~~~\n부록\n===\n\n쌀[^주]\n\n[^주]: " + "풀이 " * 390
    baseline, active = tmp_path / "baseline", tmp_path / "active"
    baseline.mkdir()
    active.mkdir()
    before = render(source, baseline, [])
    after = render(source, active, [rule("header", visible=True, left="공통", offset_mm=3),
                                    rule("header", ["부록"], left="부록쪽")])
    assert len(before) == len(after) >= 2
    def body_geometry(pages):
        # Adding a new header glyph changes the embedded PDF font subset ID.
        return [[replace(c, font=c.font.split("+")[-1]) for c in regions(p)[1]] for p in pages]
    assert body_geometry(before) == body_geometry(after)
    assert [regions(p)[0] for p in after] == ["공통"] + ["부록쪽"] * (len(after) - 1)


def test_tenth_page_number_overlaps_while_first_nine_fit(tmp_path):
    rules = [rule("footer", visible=True, left="나" * 10, right="{현재쪽}", font_pt=10, offset_mm=3)]
    with pytest.raises(InputError) as error:
        render("\n\n".join(["문단"] * 60), tmp_path, rules, (60, 65))
    assert "10쪽" in error.value.message and "바닥글" in error.value.message


@pytest.mark.parametrize(("role", "label"), [("header", "머리글"), ("footer", "바닥글")])
def test_actual_box_cannot_enter_body_margin(tmp_path, role, label):
    with pytest.raises(InputError) as error:
        render("원고", tmp_path, [rule(role, visible=True, left="높이", font_pt=14, offset_mm=14)])
    assert "1쪽" in error.value.message and label in error.value.message


def test_empty_manuscript_uses_global_footer_and_first_physical_number(tmp_path):
    page, = render("", tmp_path, [rule("footer", visible=True, right="쪽{현재쪽}", offset_mm=3)])
    assert regions(page)[2] == "쪽1"


def test_preamble_and_a_heading_on_the_first_line_of_a_page(tmp_path):
    source = "새벽\n\n" * 5 + "# 바람\n\n하늘"
    pages = render(source, tmp_path, [rule("header", visible=True, center="공통", offset_mm=3),
                                      rule("header", ["바람"], center="구간")], (60, 65))
    first, second = map(regions, pages)
    assert "".join(c.text for c in first[1]) == "새벽" * 5 and first[0] == "공통"
    assert "".join(c.text for c in second[1]).startswith("바람") and second[0] == "구간"


def test_joined_setext_heading_continues_across_physical_pages(tmp_path):
    heading = "산" * 75
    source = "# 안내\n\n~~~\n# 예시\n~~~\n**" + heading + "**\n===\n\n달"
    pages = render(source, tmp_path, [rule("header", visible=True, left="공통", offset_mm=3),
                                      rule("header", [heading], left="산쪽")], (68, 72))
    rows = [regions(p) for p in pages]
    text = ["".join(c.text for c in chars) for _, chars, _ in rows]
    assert any("~~~" in t and "산" in t for t in text)
    assert any(t.startswith("산") for t in text)
    assert [top for top, _, _ in rows] == ["산쪽" if t.startswith(("산", "달")) else "공통" for t in text]


def test_centered_text_wider_than_the_body_reports_page_and_region(tmp_path):
    with pytest.raises(InputError) as error:
        render("원고", tmp_path, [rule("header", visible=True, center="산" * 100)])
    assert "1쪽" in error.value.message and "머리글" in error.value.message


def test_offsets_move_inward_from_each_paper_edge_by_the_requested_distance(tmp_path):
    positions = []
    for mm in (2, 7):
        work = tmp_path / str(mm)
        work.mkdir()
        page, = render("원고", work, [rule("header", visible=True, left="윗글", offset_mm=mm),
                                      rule("footer", visible=True, center="밑글", offset_mm=mm)])
        positions.append({line.text: line.chars[0].baseline for line in page.lines})
    near, far = positions
    assert near["윗글"] - far["윗글"] == pytest.approx(5 * MM, abs=.002)
    assert far["밑글"] - near["밑글"] == pytest.approx(5 * MM, abs=.002)
    assert near["원고"] == far["원고"]
