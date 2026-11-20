from dataclasses import asdict
from decimal import Decimal
import json

import pytest

from app.document import InputError, parse_md
from app.style import Style, apply_style, from_stored_style


def rule(path, role, **values):
    return {"path": path, "role": role, "values": values}


def resolved(source, style):
    from app.reference_rules import block_values

    doc = parse_md(source)
    return block_values([asdict(b) for b in doc.blocks], style, fenced_blocks=doc.fenced_blocks)


def test_targets_use_visible_markdown_titles_in_outline_order():
    from app.reference_rules import targets

    source = ("본문\n\n첫 **제목**\n===\n\n### 항목\n\n##### 메모\n\n- # 목록 안\n\n"
              "~~~\n# 예시\n~~~\n\n## 항목\n\n# 부록")
    assert targets(source) == [
        {"path": ["첫 제목"], "count": 1, "level": 1},
        {"path": ["첫 제목", "항목"], "count": 2, "level": 3, "levels": [3, 2]},
        {"path": ["첫 제목", "항목", "메모"], "count": 1, "level": 5},
        {"path": ["부록"], "count": 1, "level": 1},
    ]


def test_more_specific_properties_win_independently_with_roles_isolated():
    patch = {"section_rules": [
        rule(["본문", "하위"], "body", font_pt=14),
        rule(["본문"], "heading", font_pt=24),
        rule(["본문"], "body", font_pt=12, font="nanum-gothic"),
        rule([], "body", leading_pt=17),
        rule([], "heading", color="#123456"),
        rule(["본문"], "list", first_indent_mm=0, **{"levels.1.expr": "{숫자}호"}),
    ]}
    style = apply_style(Style(), patch)
    assert resolved("서문\n\n# 본문\n\n문단\n\n## 하위\n\n문단\n\n- 항목\n\n# 결론\n\n끝", style) == [
        {"leading_sp": 1114112}, {"font_sp": 1572864, "color": "#123456"},
        {"leading_sp": 1114112, "font_sp": 786432, "font": "nanum-gothic"},
        {"color": "#123456"},
        {"leading_sp": 1114112, "font_sp": 917504, "font": "nanum-gothic"},
        {"first_indent_sp": 0, "levels.1.expr": "{숫자}호"},
        {"color": "#123456"}, {"leading_sp": 1114112},
    ]


def test_saved_rules_reconnect_only_when_the_target_is_unique():
    style = apply_style(Style(), {"section_rules": [rule(["첫 장"], "body", color="#334455")]})
    style = from_stored_style(json.loads(json.dumps(style.to_stored())))
    for source in ["# 다른 원고\n내용", "# 첫 장\n하나\n\n# 첫 장\n둘"]:
        assert all(not values for values in resolved(source, style))
    assert resolved("프롤로그\n\n# 첫 장\n다른 내용", style) == [{}, {}, {"color": "#334455"}]


def test_toggling_article_pin_preserves_heading_and_descendant_rule_targets():
    from app.reference_rules import targets

    style = apply_style(Style(), {"section_rules": [
        rule(["제8조 목적"], "heading", font_pt=24), rule(["제8조 목적", "부록"], "body", font_pt=14),
    ]})
    saved = from_stored_style(json.loads(json.dumps(style.to_stored())))
    for suffix in ("", " {번호고정}"):
        source = f"# **제8조** 목적[^주]{suffix}\n\n### 부록\n\n달\n\n[^주]: 설명"
        assert targets(source) == [{"path": ["제8조 목적"], "count": 1, "level": 1},
                                   {"path": ["제8조 목적", "부록"], "count": 1, "level": 3}]
        assert resolved(source, saved) == [{"font_sp": 1572864}, {}, {"font_sp": 917504}]


def test_pin_is_literal_outside_an_article_and_pinned_duplicates_stay_ambiguous():
    from app.reference_rules import targets

    source = ("# 일반 {번호고정}\n\n# 제8[^주]조 목적 {번호고정}\n\n"
              "# 제9조 목적\n\n본문\n\n# 제9조 목적 {번호고정}\n\n본문\n\n[^주]: 설명")
    assert targets(source) == [
        {"path": ["일반 {번호고정}"], "count": 1, "level": 1},
        {"path": ["제8조 목적 {번호고정}"], "count": 1, "level": 1},
        {"path": ["제9조 목적"], "count": 2, "level": 1},
    ]
    style = apply_style(Style(), {"section_rules": [rule(["제9조 목적"], "body", font_pt=14)]})
    assert all(not values for values in resolved(source, style))


def test_section_units_are_exact_and_round_trip_through_style_files():
    from app.style_files import editor_values

    style = apply_style(Style(), {"section_rules": [
        rule(["장"], "list", font_pt="11.00000762939453125", leading_pt="17.5",
             rest_indent_mm=Decimal("0.019378662109375"), color="#abcDEF",
             **{"levels.2.indent_mm": "0.0193786621093749999999999999999", "levels.2.gap_mm": 0,
                "levels.2.expr": " {{별첨}}{한글} "}),
    ]})
    stored = style.to_stored()
    assert stored["section_rules"] == [
        rule(["장"], "list", font_sp=720897, leading_sp=1146880, rest_indent_sp=3614,
             color="#abcDEF", **{"levels.2.indent_sp": 3613, "levels.2.gap_sp": 0,
                                 "levels.2.expr": " {{별첨}}{한글} "})
    ]
    opened = from_stored_style(json.loads(json.dumps(stored), parse_int=Decimal))
    assert apply_style(Style(), editor_values(opened)).to_stored() == stored
    assert apply_style(opened, {"leading_pt": "19"}).to_stored()["section_rules"] == stored["section_rules"]
    assert apply_style(opened, {"section_rules": []}).to_stored() == Style().to_stored()
    assert "section_rules" not in Style().to_editor()


@pytest.mark.parametrize(("row", "field"), [
    (rule("장", "heading", font_pt=12), "path"),
    (rule([3], "body", font_pt=12), "path"),
    (rule([], "footnote", color="#000000"), "role"),
    (rule(["장"], "document", page_mm=[100, 150]), "role"),
    (rule([], "heading", first_indent_mm=1), "values.first_indent_mm"),
    (rule([], "body", page_mm=[100, 150]), "values.page_mm"),
    (rule([], "heading", font="unknown"), "values.font"),
    (rule([], "heading", font_pt=True), "values.font_pt"),
    (rule([], "body", font_pt="0.000001"), "values.font_pt"),
    (rule([], "body", font_pt=2048), "values.font_pt"),
    (rule([], "heading", color="#12345z"), "values.color"),
    (rule([], "body", leading_pt="NaN"), "values.leading_pt"),
    (rule([], "body", first_indent_mm="-1e-30"), "values.first_indent_mm"),
    (rule([], "list", **{"levels.0.expr": "-"}), "values.levels.0.expr"),
    (rule([], "body", **{"levels.1.gap_mm": 0}), "values.levels.1.gap_mm"),
    (rule([], "list", **{"levels.1.expr": "{잘못}"}), "values.levels.1.expr"),
    (rule([], "list", **{"levels.2.indent_mm": 99999}), "values.levels.2.indent_mm"),
    (rule([], "body"), "values"),
])
def test_invalid_editor_rules_name_field(row, field):
    with pytest.raises(InputError) as error:
        apply_style(Style(), {"section_rules": [row]})
    assert error.value.field == f"section_rules[0].{field}"


@pytest.mark.parametrize(("key", "value"), [
    ("font_sp", "720896"), ("leading_sp", Decimal("2.5")), ("first_indent_sp", -1),
    ("levels.2.gap_sp", 2**53), ("rest_indent_mm", 1), ("font_pt", 11),
])
def test_stored_rule_units_and_integer_types_are_strict(key, value):
    with pytest.raises(InputError) as error:
        from_stored_style({**Style().to_stored(), "section_rules": [rule([], "list", **{key: value})]})
    assert error.value.field == f"section_rules[0].values.{key}"


def test_disjoint_properties_merge_and_same_target_conflict_requires_selection():
    a = rule([], "body", font_pt=12)
    b = rule([], "body", leading_pt=18)
    style = apply_style(Style(), {"section_rules": [a, b]})
    assert resolved("본문", style) == [{"font_sp": 786432, "leading_sp": 1179648}]
    with pytest.raises(InputError) as error:
        apply_style(style, {"section_rules": [a, b, rule([], "body", font_pt=14)]})
    assert error.value.field == "section_rules[2].values.font_pt"


def test_scoped_dimensions_must_fit_the_final_global_style():
    patch = {"section_rules": [rule([], "body", rest_indent_mm=60, font_pt=100)]}
    style = apply_style(Style(), patch)
    for change, key in [
        ({"page_mm": [96, 180], "margins_mm": [10, 18, 10, 18]}, "rest_indent_mm"),
        ({"ko_tracking_pct": 20000}, "font_pt"),
    ]:
        for base, update in [(style, change), (Style(), {**change, **patch})]:
            with pytest.raises(InputError) as error:
                apply_style(base, update)
            assert error.value.field == f"section_rules[0].values.{key}"
    stored = style.to_stored()
    stored["section_rules"][0]["values"]["rest_indent_sp"] = stored["page_sp"][0] - sum(stored["margins_sp"][1::2])
    with pytest.raises(InputError) as error:
        from_stored_style(stored)
    assert error.value.field == "section_rules[0].values.rest_indent_sp"


def test_section_indent_checks_metric_and_rounded_clearance():
    for width, indent in [("20", "20"), ("20.0000001", "20")]:
        with pytest.raises(InputError) as error:
            apply_style(Style(), {"page_mm": [width, 100], "margins_mm": [0, 0, 0, 0],
                                  "section_rules": [rule([], "body", first_indent_mm=indent)]})
        assert error.value.field == "section_rules[0].values.first_indent_mm"


def test_one_sp_clearance_with_a_section_indent_survives_editor_round_trip():
    stored = {**Style().to_stored(), "page_sp": [17, 1000000], "margins_sp": [0, 7, 0, 7],
              "section_rules": [rule([], "body", first_indent_sp=2)]}
    opened = from_stored_style(stored)
    assert apply_style(Style(), opened.to_editor()).to_stored() == stored


def test_absolute_heading_size_respects_installed_leading_unless_overridden(monkeypatch):
    from app import publication

    monkeypatch.setattr(publication, "HEADING_LEADING", 1000000)
    heading = rule(["제목"], "heading", font_pt=200)
    with pytest.raises(InputError) as error:
        apply_style(Style(), {"section_rules": [heading]})
    assert error.value.field == "section_rules[0].values.font_pt"
    style = apply_style(Style(), {"section_rules": [heading, rule([], "heading", leading_pt=220)]})
    assert resolved("# 제목", style) == [{"font_sp": 13107200, "leading_sp": 14417920}]
