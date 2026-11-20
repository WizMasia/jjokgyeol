import json
from decimal import Decimal

import pytest

from app.document import InputError, parse_md
from app.style import Style, apply_style, editor_bootstrap, from_stored_style
from test_publication import CONFIG, install, rewrite, start


def rule(path, role, **values):
    return {"path": path, "role": role, "values": values}


def resolved(source, rules):
    from app.reference_rules import running_values

    style = apply_style(Style(), {"section_rules": rules})
    doc = parse_md(source)
    return running_values(doc.blocks, style, fenced_blocks=doc.fenced_blocks)


def test_slots_preserve_literal_spaces_and_braces_and_distinguish_page_fields():
    from app.running_heads import escape_running_literal, parse_running_text

    assert parse_running_text("") == []
    assert parse_running_text(" {{현재쪽}}·{{{현재쪽}}} 2026 {현재쪽}") == [
        ("text", " {현재쪽}·{"), ("page", ""), ("text", "} 2026 "), ("page", ""),
    ]
    literal = r"{{첫째}}{현재쪽}%\문구"
    assert parse_running_text(escape_running_literal(literal)) == [("text", literal)]


def test_section_visibility_empty_slot_and_inheritance_restore_independently():
    source = "앞말\n\n# 표지\n\n## 설명\n\n첫 문단\n\n### 세부\n\n- 목록\n\n# 부록"
    common = rule([], "footer", visible=True, center="{현재쪽}", left="기관")
    overrides = [
        rule(["표지", "설명", "세부"], "footer", visible=True),
        rule(["표지", "설명"], "footer", left="설명", center=""),
        rule(["표지"], "footer", visible=False),
        rule(["부록"], "header", visible=True, right="부록"),
        common,
    ]
    result = resolved(source, overrides)
    assert [(r["footer"]["visible"], r["footer"]["center"], r["footer"]["left"]) for r in result] == [
        (True, "{현재쪽}", "기관"), (False, "{현재쪽}", "기관"), (False, "", "설명"),
        (False, "", "설명"), (True, "", "설명"), (True, "", "설명"), (True, "{현재쪽}", "기관"),
    ]
    assert all(not r["header"]["visible"] for r in result[:-1])
    assert result[-1]["header"]["visible"] is True and result[-1]["header"]["right"] == "부록"
    overrides[1] = rule(["표지", "설명"], "footer", left="설명")
    assert resolved(source, overrides)[5]["footer"]["center"] == "{현재쪽}"


def test_running_sections_use_only_unique_real_heading_paths_without_affecting_body():
    from app.reference_rules import block_values

    rules = [rule(["빠진 장"], "header", visible=True), rule(["반복"], "footer", visible=True),
             rule(["가짜"], "header", visible=True), rule(["제8조 목적"], "header", left="실제", visible=True)]
    source = ("# 반복\n\n본문\n\n# 반복\n\n~~~\n# 가짜\n~~~\n"
              "**제8조** 목적 {번호고정}\n===\n\n끝")
    doc = parse_md(source)
    style = apply_style(Style(), {"section_rules": rules})
    body = block_values(doc.blocks, style, fenced_blocks=doc.fenced_blocks)
    assert all(not values for values in body)
    result = resolved(source, rules)
    assert all(not r["footer"]["visible"] for r in result)
    assert [r["header"]["left"] for r in result] == [""] * (len(result) - 2) + ["실제"] * 2
    assert resolved("", rules) == []


def test_legacy_omission_and_exact_stored_section_round_trip():
    from app.reference_rules import running_values

    old = Style().to_stored()
    assert "section_rules" not in old and from_stored_style(old).to_stored() == old
    defaults = editor_bootstrap()["running_defaults"]
    assert defaults == {"visible": False, "left": "", "center": "", "right": "", "font": "nanum-myeongjo",
                        "color": "#000000", "font_pt": "9", "offset_mm": "8"}
    before = resolved("앞말", [])[0]
    for area in ("header", "footer"):
        assert before[area] == {"visible": False, "left": "", "center": "", "right": "",
                                "font": "nanum-myeongjo", "color": "#000000", "font_sp": 589824,
                                "offset_sp": 1491744}
    rules = [rule([], "footer", font_pt="9.00000762939453125", offset_mm=Decimal("0.019378662109375"),
                  visible=False, center=""), rule(["부록"], "header", left="{{현재쪽}} 2026")]
    style = apply_style(Style(), {"section_rules": rules})
    stored = style.to_stored()
    assert stored["section_rules"] == [
        rule([], "footer", font_sp=589825, offset_sp=3614, visible=False, center=""), rules[1]]
    opened = from_stored_style(json.loads(json.dumps(stored), parse_int=Decimal))
    assert apply_style(Style(), opened.to_editor()).to_stored() == stored
    assert running_values(parse_md("# 부록").blocks, opened)[0]["footer"]["font_sp"] == 589825
    assert "section_rules" not in editor_bootstrap()["defaults"]


@pytest.mark.parametrize(("key", "bad"), [
    ("visible", 1), ("visible", "false"), ("left", None), ("center", "두\n줄"), ("right", "문구 {총쪽}"),
    ("left", "{"), ("center", "문구}"), ("font", "missing"), ("color", "#gg1122"),
    ("offset_mm", "-1e-88"), ("offset_mm", True), ("offset_mm", 6000),
    ("font_pt", False), ("font_pt", 0), ("leading_pt", 12), ("first_indent_mm", 1),
    ("levels.1.expr", "{숫자}"), ("evidence", "출처"),
])
def test_running_field_errors_are_atomic_and_locate_the_slot(key, bad):
    base = apply_style(Style(), {"section_rules": [rule([], "header", visible=True)]})
    with pytest.raises(InputError) as error:
        apply_style(base, {"section_rules": [rule([], "header", visible=False), rule([], "footer", **{key: bad})]})
    assert error.value.field == "section_rules[1].values." + key
    if key == "right":
        assert "4" in error.value.message  # Opening brace is the fourth character.
    assert base.to_editor()["section_rules"] == [rule([], "header", visible=True)]


@pytest.mark.parametrize(("key", "bad"), [
    ("offset_sp", "2"), ("offset_sp", -1), ("offset_sp", 2**53), ("font_sp", Decimal("1.01")),
    ("font_sp", True), ("offset_mm", 3), ("visible", Decimal(0)),
])
def test_invalid_stored_running_values(key, bad):
    with pytest.raises(InputError) as error:
        from_stored_style({**Style().to_stored(), "section_rules": [rule([], "footer", **{key: bad})]})
    assert error.value.field == "section_rules[0].values." + key


def test_running_defaults_install_change_reaches_both_areas_and_the_editor(install):
    rewrite(install, CONFIG, lambda c: c["running_defaults"].update(font_pt=12.5))
    proc = start(install, """
from decimal import localcontext
from app.document import parse_md
from app.style import Style, apply_style, editor_bootstrap
from app.reference_rules import running_values
with localcontext() as context:
    context.prec = 2
    assert editor_bootstrap()["running_defaults"]["font_pt"] == "12.5"
style = apply_style(Style(), {"section_rules": [{"path": [], "role": "header", "values": {"visible": True}}]})
row, = running_values(parse_md("본문").blocks, style)
assert row["header"]["font_sp"] == row["footer"]["font_sp"] == 819200
assert style.to_stored()["section_rules"][0]["values"] == {"visible": True}
""")
    assert proc.returncode == 0, proc.stderr
    rewrite(install, CONFIG, lambda c: c["running_defaults"].update(offset_mm=-1))
    proc = start(install, "from app.style import Style")
    assert proc.returncode and "running_defaults.offset_mm" in proc.stderr.splitlines()[-1]


def test_installed_default_visibility_only_activates_for_a_matching_area(install):
    rewrite(install, CONFIG, lambda c: c["running_defaults"].update(visible=True))
    proc = start(install, """
from app.document import parse_md
from app.reference_rules import running_values
from app.style import Style, apply_style, editor_bootstrap
assert editor_bootstrap()["running_defaults"]["visible"] is True
doc = parse_md("# 서론\\n\\n첫말\\n\\n# 부록\\n\\n## 자료\\n\\n끝")
def visible(style):
    return [(r["header"]["visible"], r["footer"]["visible"]) for r in running_values(doc.blocks, style)]
assert visible(Style()) == [(False, False)] * 5
style = apply_style(Style(), {"section_rules": [
    {"path": ["부록"], "role": "header", "values": {"left": "부록"}},
    {"path": ["누락"], "role": "footer", "values": {"right": "끝"}},
]})
assert visible(style) == [(False, False)] * 2 + [(True, False)] * 3
""")
    assert proc.returncode == 0, proc.stderr
