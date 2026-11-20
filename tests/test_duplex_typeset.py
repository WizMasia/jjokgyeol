"""Read face-dependent text and positions from the physical pages LuaLaTeX shipped."""
import json

import pytest

from app.document import InputError
from test_running_heads_typeset import MM, regions, render, rule


def face(role, name, path=(), **values):
    return {**rule(role, path, **values), "face": name}


def test_first_page_for_nested_and_identical_sibling_rules(tmp_path):
    source = ("서문\n\n# 잠깐\n\n짧은글\n\n# 부록\n\n## 동쪽\n\n" + "동" * 440 +
              "\n\n## 서쪽\n\n" + "서" * 440 + "\n\n## 나머지\n\n" + "덤" * 350)
    rules = [rule("header", visible=True, center="공", offset_mm=3),
             face("header", "odd", left="홀"), face("header", "even", left="짝"),
             face("header", "first", left="표"), face("header", "first", ["잠깐"], center="잠"),
             rule("header", ["부록"], right="부"), face("header", "first", ["부록"], left="첫", center="큰")]
    for name in ("동쪽", "서쪽"):
        rules.append(face("header", "first", ["부록", name], center="새"))
    rows = [regions(p) for p in render(source, tmp_path, rules, (80, 90))]
    # Both parent and child first own page 2. The second child first owns page 5.
    # The last child has no rules, so the parent's ordinary style resumes there.
    assert [body[0].text for _, body, _ in rows] == list("서동동동서서서나덤덤")
    assert [top for top, _, _ in rows] == [
        "표공", "첫새부", "홀공부", "짝공부", "홀새부", "짝공부", "홀공부", "짝공부", "홀공부", "짝공부",
    ]


def test_heading_on_first_line_hides_only_header_and_footer_moves_outside(tmp_path):
    source = "새벽\n\n" * 5 + "# 바람\n\n" + "하늘\n\n" * 11
    rules = [rule("header", visible=True, left="공통", offset_mm=3),
             face("header", "first", ["바람"], visible=False),
             rule("footer", visible=True, center="{현재쪽}", offset_mm=3),
             face("footer", "even", left="{현재쪽}", center=""),
             face("footer", "odd", right="{현재쪽}", center="")]
    pages = render(source, tmp_path, rules, (60, 65))
    assert len(pages) >= 3 and "".join(c.text for c in regions(pages[1])[1]).startswith("바람")
    for i, p in enumerate(pages, 1):
        top, _, bottom = regions(p)
        assert top == ("" if i == 2 else "공통")
        assert bottom == str(i)
        digits = [c for line in p.lines for c in line.chars if c.baseline < 14 * MM]
        if i % 2:
            assert digits[-1].x1 == pytest.approx(p.width - 12 * MM, abs=.02)
        else:
            assert digits[0].x0 == pytest.approx(12 * MM, abs=.02)


def test_footnote_only_pages_inherit_last_scope_and_leave_first_for_the_next_body_line(tmp_path):
    source = "# 시작\n\n달\n\n# 부록\n\n쌀[^주]\n\n나중\n\n[^주]: " + "각주 " * 350
    rules = [rule("header", visible=True, center="앞", offset_mm=3),
             face("header", "first", ["부록"], center="첫"),
             face("header", "even", ["부록"], center="짝"),
             face("header", "odd", ["부록"], center="홀")]
    rows = [regions(p) for p in render(source, tmp_path, rules, (75, 73))]
    assert len(rows) >= 4
    assert "부록쌀" in "".join(c.text for c in rows[0][1])
    for _, body, _ in rows[1:-1]:
        assert "각주" in "".join(c.text for c in body) and all(c.size <= 8.1 for c in body)
    assert "".join(c.text for c in rows[-1][1]).startswith("나중")
    note_faces = ["짝" if i % 2 == 0 else "홀" for i in range(2, len(rows))]
    assert [top for top, _, _ in rows] == ["앞", *note_faces, "첫"]


def test_empty_physical_page_uses_document_first_over_odd(tmp_path):
    rules = [face("footer", "odd", visible=True, left="홀", right="{현재쪽}"),
             face("footer", "first", left="표", font_pt=13, font="nanum-gothic"),
             face("header", "even", visible=True, center="짝")]
    page, = render("", tmp_path, rules)
    assert regions(page) == ("", [], "표1")
    assert all(c.font.endswith("NanumGothic") and c.size == pytest.approx(13 * 72 / 72.27, abs=.01)
               for line in page.lines for c in line.chars)


def test_child_default_overrides_document_first_without_enabling_an_unmatched_role(tmp_path):
    rules = [face("header", "first", visible=True, center="책", right="첫"),
             rule("header", ["장"], center="장"), face("footer", "even", visible=True, center="짝")]
    page, = render("# 장\n\n문장", tmp_path, rules)
    assert (regions(page)[0], regions(page)[2]) == ("장첫", "")


def test_only_matching_face_can_cause_a_two_digit_collision(tmp_path):
    source = "\n\n".join(["문단"] * 50)
    for name in ("odd", "even"):
        work = tmp_path / name
        work.mkdir()
        rules = [rule("footer", visible=True, right="{현재쪽}", font_pt=10, offset_mm=3),
                 face("footer", name, left="나" * 10)]
        if name == "odd":
            pages = render(source, work, rules, (60, 65))
            assert len(pages) == 10 and regions(pages[-1])[2] == "10"
            assert regions(pages[-2])[2].replace(" ", "") == "나" * 10 + "9"
        else:
            with pytest.raises(InputError) as error:
                render(source, work, rules, (60, 65))
            assert error.value.field == "footer" and "10쪽" in error.value.message


def test_job_transmits_a_scoped_face_only_once_for_all_its_blocks(tmp_path):
    phrase = "유일문구"
    rules = [face("header", "first", ["장"], visible=True, left=phrase, offset_mm=3)]
    pages = render("# 장\n\n" + "문단\n\n" * 24, tmp_path, rules, (60, 65))
    assert [regions(p)[0] for p in pages] == [phrase] + [""] * (len(pages) - 1)
    payload = json.loads((tmp_path / "input.json").read_bytes())
    assert json.dumps(payload, ensure_ascii=False).count(phrase) == 1
