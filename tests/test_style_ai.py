"""A suggestion is an atomic proposal in editor units."""
from functools import partial

import pytest

from app.ai.style import suggest_patch
from app.document import InputError
from app.style import Style, apply_style

BOTH = "본문은 16pt로 하고 바닥글 가운데에 쪽 번호를 넣어 줘"
BODY = "본문만 16pt로"
FOOT = {"path": [], "role": "footer", "values": {"visible": True, "center": "{현재쪽}"}}


def reply(*pairs):
    return {"edits": [{"field": field, "value": value} for field, value in pairs]}


def model(answer):
    class Model:
        def generate_json(self, prompt, payload, *, timeout, schema):
            return answer

    return partial(suggest_patch, connection=Model())


def test_partial_patch():
    current = apply_style(Style(), {"text_styles": {"heading1": {"font_pt": 18}},
                                    "margins_mm": [15, 19, 15, 19]})
    suggest = model(reply(("body_pt", "16"), ("footer.center", "{현재쪽}")))
    patch = suggest(BOTH, current)
    assert patch == {"body_pt": "16", "section_rules": [FOOT]}
    confirmed = apply_style(current, patch)
    manual = apply_style(confirmed, {"body_pt": 14})
    assert manual.to_editor()["section_rules"] == [FOOT]
    assert manual.to_editor()["body_pt"] == "14"
    suggest = model(reply(("body_pt", "16")))
    assert suggest(BODY, manual) == {"body_pt": "16"}
    assert apply_style(manual, suggest(BODY, manual)) == confirmed
    assert current.to_editor()["body_pt"] == "11"


def test_preserves_scopes_duplicate_assignments_and_list_levels():
    rules = [
        {"path": [], "role": "header", "values": {"left": "기관"}},
        {"path": [], "role": "footer", "values": {"left": "책", "center": "옛말"}},
        {"path": [], "role": "footer", "values": {"visible": False, "center": "옛말"}},
        {"path": [], "role": "footer", "face": "even", "values": {"center": "짝"}},
        {"path": ["표지"], "role": "footer", "values": {"visible": False}},
    ]
    current = apply_style(Style(), {"section_rules": rules})
    suggest = model(reply(("footer.center", "{현재쪽}"), ("list.2.expr", "[{숫자}]")))
    patch = suggest("목록 둘째 단계 표지와 가운데 쪽 번호", current)
    rows = patch["section_rules"]
    assert [rows[i] for i in (0, 3, 4)] == [rules[i] for i in (0, 3, 4)]
    assert rows[1]["values"] == {"left": "책", "center": "{현재쪽}"}
    assert rows[2]["values"] == {"visible": True, "center": "{현재쪽}"}
    levels = [v.to_editor() for v in current.resolved_list_levels()]
    levels[1]["expr"] = "[{숫자}]"
    assert patch["list_levels"] == levels
    assert current.to_editor()["section_rules"] == rules


@pytest.mark.parametrize(("pairs", "field"), [
    ([("list.1.expr", "{없는토큰}")], "list_levels[0].expr"),
    ([("page.width", "140"), ("margin.left", "75"), ("margin.right", "75")], "margins_mm"),
    ([("footer.center", "{쪽}")], "section_rules[0].values.center"),
    ([("source", "원고 수정")], "patch"),
    ([("body_pt", "16"), ("body_pt", "14")], "patch"),
    ([("body_pt", True)], "patch"),
])
def test_invalid_patch_is_atomic(pairs, field):
    current = Style()
    before = current.to_stored()
    suggest = model(reply(*pairs))
    with pytest.raises(InputError) as error:
        suggest("양식 수정", current)
    assert error.value.field == field
    assert current.to_stored() == before


def test_noop_and_unrelated_decimals():
    suggest = model(reply(("body_pt", "11.0"), ("margin.left", "25.5")))
    assert suggest("왼쪽만 변경", Style()) == {"margins_mm": ["20", "18", "20", "25.5"]}


@pytest.mark.parametrize("other, expected", [([], {}), ([("body_pt", "16")], {"body_pt": "16"})])
def test_matching_inherited_list_preset_stays_inherited(other, expected):
    suggest = model(reply(("list.1.expr", "{원숫자}"), ("list.2.gap_mm", "2.00"), *other))
    assert suggest("목록 양식 유지", Style()) == expected
