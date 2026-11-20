import json
from pathlib import Path

import pytest

from app.document import InputError, parse_md
from app.reference_rules import block_values, running_values
from app.style import SectionRule, Style, apply_style, from_stored_style


def rule(path, role, values, **options):
    return {"path": path, "role": role, "values": values, **options}


def contexts(source, rules):
    from app.reference_rules import running_contexts

    doc = parse_md(source)
    style = apply_style(Style(), {"section_rules": rules})
    return running_contexts(doc.blocks, style, fenced_blocks=doc.fenced_blocks)


def test_same_slot_on_different_faces_round_trips_without_filling_sparse_values():
    rules = [
        rule([], "footer", {"visible": True, "center": "{현재쪽}"}, face="default"),
        rule([], "footer", {"center": "", "right": "{현재쪽}", "font_pt": "9.25"}, face="odd"),
        rule([], "footer", {"center": "", "left": "{현재쪽}"}, face="even"),
        rule(["부록"], "footer", {"visible": False}, face="first"),
    ]
    stored = apply_style(Style(), {"section_rules": rules}).to_stored()
    assert stored["section_rules"] == [
        rule([], "footer", {"visible": True, "center": "{현재쪽}"}),
        rule([], "footer", {"center": "", "right": "{현재쪽}", "font_sp": 606208}, face="odd"),
        rules[2], rules[3],
    ]
    opened = from_stored_style(json.loads(json.dumps(stored)))
    assert opened.to_editor()["section_rules"] == [stored["section_rules"][0], *rules[1:]]
    assert apply_style(Style(), opened.to_editor()).to_stored() == stored
    assert SectionRule((), "header", (("visible", True),)).face == "default"


def test_existing_single_face_proof_opens_and_exports_identically():
    path = Path(__file__).resolve().parents[1] / "proofs/running-heads/style.json"
    saved = json.loads(path.read_bytes())
    opened = from_stored_style(saved)
    assert opened.to_stored() == apply_style(Style(), opened.to_editor()).to_stored() == saved


@pytest.mark.parametrize(("role", "face"), [
    ("header", "unknown"), ("footer", None), ("header", True), ("footer", []),
    ("body", "odd"), ("heading", "default"), ("list", "first"),
])
def test_only_running_areas_accept_a_known_face(role, face):
    bad = [rule([], role, {"color": "#112233"}, face=face)]
    for load in (lambda: apply_style(Style(), {"section_rules": bad}),
                 lambda: from_stored_style({**Style().to_stored(), "section_rules": bad})):
        with pytest.raises(InputError) as error:
            load()
        assert error.value.field == "section_rules[0].face"


@pytest.mark.parametrize("first,second", [({"face": "even"}, {"face": "even"}), ({}, {"face": "default"})])
def test_conflicting_same_face_properties_fail_both_loaders(first, second):
    rules = [rule([], "footer", {"left": "앞"}, **first), rule([], "footer", {"left": "뒤"}, **second)]
    for load in (lambda: apply_style(Style(), {"section_rules": rules}),
                 lambda: from_stored_style({**Style().to_stored(), "section_rules": rules})):
        with pytest.raises(InputError) as error:
            load()
        assert error.value.field == "section_rules[1].values.left"


def test_contexts_identify_paths_in_manuscript_order_and_keep_faces_sparse():
    source = "앞말\n\n# 부록\n\n### 자료\n\n- 가\n\n### 참고\n\n나\n\n# 끝"
    rules = [
        rule(["부록", "참고"], "header", {"left": "첫 면"}, face="first"),
        rule(["부록", "자료"], "header", {"left": "첫 면"}, face="first"),
        rule(["부록"], "footer", {"visible": False}, face="first"),
        rule([], "footer", {"right": "{현재쪽}"}, face="odd"),
        rule([], "footer", {"center": ""}, face="odd"),
        rule(["부록"], "header", {"font_pt": 10}),
        rule(["끝"], "heading", {"color": "#112233"}),
    ]
    scopes, chains = contexts(source, rules)
    assert scopes == [
        {"path": [], "header": {}, "footer": {"odd": {"right": "{현재쪽}", "center": ""}}},
        {"path": ["부록"], "header": {"default": {"font_sp": 655360}}, "footer": {"first": {"visible": False}}},
        {"path": ["부록", "자료"], "header": {"first": {"left": "첫 면"}}, "footer": {}},
        {"path": ["부록", "참고"], "header": {"first": {"left": "첫 면"}}, "footer": {}},
    ]
    assert chains == [[1], [1, 2], [1, 2, 3], [1, 2, 3], [1, 2, 4], [1, 2, 4], [1]]


def test_missing_duplicate_and_fenced_paths_are_inactive():
    source = "# **제8조** 목적 {번호고정}\n\n~~~\n# 예시\n~~~\n\n## 자료\n\n## 자료"
    rules = [rule(path, "header", {"left": "첫 면"}, face="first")
             for path in (["제8조 목적"], ["제8조 목적", "자료"], ["예시"], ["누락"])]
    scopes, chains = contexts(source, rules)
    assert [s["path"] for s in scopes] == [[], ["제8조 목적"]]
    assert chains == [[1, 2] for _ in parse_md(source).blocks]


def test_global_scope_exists_even_without_blocks_or_applicable_rules():
    assert contexts("", []) == ([{"path": [], "header": {}, "footer": {}}], [])
    assert contexts("", [rule([], "footer", {"visible": False}, face="first")]) == (
        [{"path": [], "header": {}, "footer": {"first": {"visible": False}}}], [])
    assert contexts("# 시작\n\n본문", []) == ([{"path": [], "header": {}, "footer": {}}], [[1], [1]])


def test_common_value_query_ignores_parity_and_first_face_rules():
    rules = [rule([], "footer", {"visible": True, "center": "{현재쪽}"}),
             rule([], "footer", {"center": ""}, face="even"),
             rule(["장"], "footer", {"visible": False}, face="first"),
             rule(["장"], "header", {"visible": True, "left": "홀수"}, face="odd")]
    style = apply_style(Style(), {"section_rules": rules})
    blocks = parse_md("# 장\n\n본문").blocks
    for areas in running_values(blocks, style):
        assert areas["footer"]["visible"] is True and areas["footer"]["center"] == "{현재쪽}"
        assert areas["header"]["visible"] is False and areas["header"]["left"] == ""
    assert block_values(blocks, style) == [{}, {}]
