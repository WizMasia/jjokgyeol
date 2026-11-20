import pytest

from app.document import InputError, parse_md
from app.list_marks import format_mark
from app.style import Style, apply_style, editor_bootstrap, from_stored_style
from app.typeset import typeset
from test_list_acceptance import match_item, rows


def test_line_position_round_trip_and_off():
    legacy = Style().to_stored()
    assert "line_numbers" not in legacy
    for side in ("left", "right"):
        s = apply_style(Style(), {"line_numbers": side})
        assert from_stored_style(s.to_stored()).to_editor()["line_numbers"] == side
        assert apply_style(s, {"line_numbers": "off"}).to_stored() == legacy
    for bad in (["left"], "", 1, None):
        with pytest.raises(InputError) as err:
            apply_style(Style(), {"line_numbers": bad})
        assert err.value.field == "line_numbers"


def test_outline_token_is_contextual():
    assert format_mark("{{계층번호}} {계층번호}.", 4, parents=(2, 3)) == "{계층번호} 2.3.4."
    with pytest.raises(InputError):
        apply_style(Style(), {"article_expr": "{계층번호}."})


def test_number_path_prints_ancestors_and_resets(tmp_path):
    source = ("9. 서문\n   - 첫째\n     - 깊이\n   - 둘째\n10. 본론\n    - 아래\n\n"
              "별도 문단\n\n- 부록\n  - 참고")
    levels = editor_bootstrap()["list_presets"]["outline"]
    style = apply_style(Style(), {"list_levels": levels})
    printed = rows(typeset(parse_md(source), style, tmp_path))
    for name, mark in (("서문", "9."), ("첫째", "9.1."), ("깊이", "9.1.1."),
                       ("둘째", "9.2."), ("본론", "10."), ("아래", "10.1."),
                       ("부록", "1."), ("참고", "1.1.")):
        _, chars, _ = match_item(printed, name)
        assert "".join(c.text for c in chars) == mark


@pytest.mark.parametrize(("expr", "expected"), [
    ("{계층번호}.", ("7.", "24.", "24.1.")),
    ("{숫자}.", ("7.", "8.", "1.")),
    ("{{계층번호}}{숫자}.", ("{계층번호}7.", "{계층번호}8.", "{계층번호}1.")),
])
def test_fenced_ordered_example_before_real_list(tmp_path, expr, expected):
    source = "~~~\n\n7. 예제\n~~~\n\n24. 부모\n    - 자식"
    style = apply_style(Style(), {"list_levels": [{"expr": expr, "indent_mm": 0, "gap_mm": 2}]})
    printed = rows(typeset(parse_md(source), style, tmp_path))
    for name, mark in zip(("예제", "부모", "자식"), expected, strict=True):
        assert "".join(c.text for c in match_item(printed, name)[1]) == mark


@pytest.mark.parametrize(("article", "root"), [(False, 9), (True, 1)])
def test_nested_fence_keeps_real_siblings_and_article_ordinals(tmp_path, article, root):
    source = ("# 제8조 조사\n\n9. 서문\n   - 현장\n\n     ````md\n     - 예제\n     ````\n\n"
              "     - 기록\n       - 상세\n   - 검토\n10. 결론")
    patch = {"list_levels": editor_bootstrap()["list_presets"]["outline"]}
    if article:
        patch["article_expr"] = "제{숫자}조"
    printed = rows(typeset(parse_md(source), apply_style(Style(), patch), tmp_path))
    for name, mark in (("서문", f"{root}."), ("현장", f"{root}.1."),
                       ("기록", f"{root}.1.1."), ("상세", f"{root}.1.1.1."),
                       ("검토", f"{root}.2."), ("결론", f"{root + 1}.")):
        assert "".join(c.text for c in match_item(printed, name)[1]) == mark, name


def test_fence_on_article_item_and_adjacent_ordered_list(tmp_path):
    source = ("# 제8조 조사\n\n9. ~~~\n   - 예제\n   ~~~\n   - 현장\n10. 검토\n\n"
              "~~~\n예시\n~~~\n24. 결론\n    - 기록")
    style = apply_style(Style(), {
        "article_expr": "제{숫자}조",
        "section_rules": [{"path": ["제8조 조사"], "role": "list",
                           "values": {"levels.1.expr": "{계층번호}.", "levels.2.expr": "{계층번호}."}}],
    })
    printed = rows(typeset(parse_md(source), style, tmp_path))
    for name, mark in (("현장", "1.1."), ("검토", "2."), ("결론", "24."), ("기록", "24.1.")):
        assert "".join(c.text for c in match_item(printed, name)[1]) == mark, name


def test_fences_only_change_lists_using_hierarchy_in_their_section(tmp_path):
    source = ("# 서문\n\n~~~\n\n7. 도표\n~~~\n\n24. 원본\n\n"
              "# 본문\n\n~~~\n\n7. 예제\n~~~\n\n24. 결과")
    style = apply_style(Style(), {
        "list_levels": [{"expr": "{숫자}.", "indent_mm": 0, "gap_mm": 2}],
        "section_rules": [{"path": ["본문"], "role": "list", "values": {"levels.1.expr": "{계층번호}."}}],
    })
    printed = rows(typeset(parse_md(source), style, tmp_path))
    for name, mark in (("원본", "8."), ("결과", "24.")):
        assert "".join(c.text for c in match_item(printed, name)[1]) == mark, name


def test_outline_nine_levels_fit_a5_and_wrap_at_body_column(tmp_path):
    names = ["계획", "조사", "지역", "현장", "기록", "면담", "증언", "대조", "검증"]
    source = "\n".join("  " * depth + "- " + name for depth, name in enumerate(names))
    source += " " + "확인 " * 45 + "\n" + "  " * 8 + "- 보완\n- 결론"
    levels = editor_bootstrap()["list_presets"]["outline"]
    style = apply_style(Style(), {"list_levels": levels, "paragraph_layout": "justified",
                                "ko_tracking_bounds_pct": [-15, 15], "space_pct": [70, 130]})
    printed = rows(typeset(parse_md(source), style, tmp_path))
    previous = 0
    for depth, name in enumerate(names, 1):
        index, marker, first = match_item(printed, name)
        assert "".join(c.text for c in marker) == "1." * depth
        assert marker[0].x0 > previous
        previous = marker[0].x0
    assert printed[index + 1][1][0].x0 == pytest.approx(first.x0, abs=.03)
    for name, expected in (("보완", "1." * 8 + "2."), ("결론", "2.")):
        _, marker, _ = match_item(printed, name)
        assert "".join(c.text for c in marker) == expected
    assert len(levels) == 9


def test_line_number_faces_round_trip_and_validate_both_sides():
    for odd in ("left", "right", "off"):
        for even in ("left", "right", "off"):
            pair = {"odd": odd, "even": even}
            style = apply_style(Style(), {"line_numbers": pair})
            assert style.to_editor()["line_numbers"] == pair
            assert from_stored_style(style.to_stored()) == style
            assert apply_style(style, {"line_numbers": "right"}).to_stored()["line_numbers"] == "right"
            assert "line_numbers" not in apply_style(style, {"line_numbers": "off"}).to_stored()
    for bad in ({}, {"odd": "left"}, {"odd": "up", "even": "right"},
                {"odd": [], "even": "left"}, {"odd": "left", "even": "left", "first": "off"}):
        with pytest.raises(InputError) as err:
            apply_style(Style(), {"line_numbers": bad})
        assert err.value.field == "line_numbers"
