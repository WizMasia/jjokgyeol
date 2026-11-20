"""Duplex editing through the browser and the real style-file endpoint."""
import json

import pytest

from playwright.sync_api import expect

from test_running_heads_editor import apply, browser, desk, open_editor, own, server_url, target, trial
from test_reference_editor import candidate, unit


def test_face_edits_inherit_by_path_and_survive_style_file(desk):
    d, p = desk, desk.page
    open_editor(d)
    face = p.get_by_label("편집할 면", exact=True)
    expect(face).to_have_value("default")
    own(p, "center", "공통")
    for side, text in [("even", "짝수면"), ("odd", "홀수면")]:
        face.select_option(side)
        own(p, "center", text)
    target(p, ["표지"], "header")
    face.select_option("first")
    box = p.get_by_role("group", name="가운데 칸")
    expect(box).to_contain_text("홀수 쪽")
    expect(box).to_contain_text("홀수면")
    expect(box).to_contain_text("짝수 쪽")
    expect(box).to_contain_text("짝수면")
    own(p, "center", "")
    target(p, ["표지"], "footer")
    p.locator("#running-visible").select_option("hide")
    rules = trial(d)
    assert rules == [
        {"path": [], "role": "header", "values": {"center": "공통"}},
        {"path": [], "role": "header", "face": "even", "values": {"center": "짝수면"}},
        {"path": [], "role": "header", "face": "odd", "values": {"center": "홀수면"}},
        {"path": ["표지"], "role": "header", "face": "first", "values": {"center": ""}},
        {"path": ["표지"], "role": "footer", "face": "first", "values": {"visible": False}},
    ]
    apply(d)
    p.locator("summary").click()
    with p.expect_download() as download:
        p.locator("#save-style").click()
    stored = download.value.path().read_bytes()
    assert json.loads(stored)["section_rules"] == rules
    p.locator("#style-file").set_input_files({"name": "양면.json", "mimeType": "application/json", "buffer": stored})
    expect(p.locator("#save-style")).to_be_enabled()
    open_editor(d)
    target(p, ["표지"], "header")
    face.select_option("first")
    expect(p.locator("#running-own-center")).to_be_checked()
    expect(p.locator("#running-center")).to_have_value("")
    face.select_option("default")
    own(p, "center", "표지 공통")
    face.select_option("odd")
    expect(p.locator("#running-center")).to_have_value("표지 공통")
    p.locator("#reference-cancel").click()
    assert p.evaluate("styleForm.get().section_rules") == rules


def test_candidate_faces_retry_conflict_and_explicit_hide(desk):
    d, p = desk, desk.page
    def running(key, value, group, face):
        return candidate("footer", key, value, source_group=group, variant="unknown", suggested_face=face)
    odd = running("center", "가", "A", "odd")
    even = running("center", "나", "B", "even")
    off = running("visible", False, "C", "first")
    first = running("right", "문구", "D", "first")
    unreviewed = running("left", "근거", "E", "odd")
    def analyze(route):
        retry = "page=1" in route.request.url
        result = [{**odd, "suggested_face": None} if retry else odd, even, off, first,
                  {**unreviewed, "suggested_face": None} if retry else unreviewed]
        route.fulfill(json={"units": [unit(1, "본문", *result)], "warnings": []})
    p.route("**/api/reference/analyze?*", analyze)
    d.open()
    p.locator("#reference-file").set_input_files({"name": "책.pdf", "mimeType": "application/pdf", "buffer": b"x"})
    p.locator("#reference-analyze").click()
    a, b, c, e = [p.locator(f'[data-source-group="{group}"]') for group in "ABCD"]
    for row in [c, e, a, b]:
        row.get_by_role("checkbox").check()
    face = a.get_by_label("적용할 면", exact=True)
    expect(face).to_have_value("odd")
    face.select_option("even")
    p.locator("#reference-trial").click()
    expect(p.locator("#reference-message")).to_contain_text("충돌")
    face.select_option("default")
    p.locator("#running-center").fill("저자가 수정")
    p.get_by_role("button", name="1쪽 다시 분석").click()
    expect(p.locator("#reference-analyze")).to_be_enabled()
    expect(face).to_have_value("default")
    expect(p.locator('[data-source-group="E"]').get_by_label("적용할 면")).to_have_value("")
    expect(a.get_by_label("제안 값")).to_have_value("저자가 수정")
    rules = {r.get("face", "default"): r["values"] for r in trial(d)}
    assert rules == {"default": {"center": "저자가 수정", "visible": True},
                     "even": {"center": "나"}, "first": {"visible": False, "right": "문구"}}
    apply(d)


def test_unresolved_scan_face_and_number_require_separate_choices(desk):
    d, p = desk, desk.page
    scan = candidate("footer", "right", "제17면", "confirm", source_group="scan", variant="unknown",
                     suggested_face=None, options=[{"kind": "literal", "value": "제17면"},
                                                   {"kind": "page", "value": "제{현재쪽}면"}])
    p.route("**/api/reference/analyze?*", lambda route: route.fulfill(json={"units": [unit(1, "본문", scan)], "warnings": []}))
    d.open()
    p.locator("#reference-file").set_input_files({"name": "스캔.png", "mimeType": "image/png", "buffer": b"x"})
    p.locator("#reference-analyze").click()
    row = d.row("footer", "right")
    row.get_by_role("checkbox").check()
    face = row.get_by_label("적용할 면", exact=True)
    expect(face).to_have_value("")
    p.locator("#reference-trial").click()
    expect(p.locator("#reference-message")).to_contain_text("적용할 면")
    face.select_option("odd")
    p.locator("#reference-trial").click()
    expect(p.locator("#reference-message")).to_contain_text("번호 해석")
    row.get_by_label("번호 해석").select_option("page")
    assert trial(d) == [{"path": [], "role": "footer", "face": "odd", "values": {"right": "제{현재쪽}면", "visible": True}}]
    apply(d)


def test_rebind_and_validation_focus_keep_face(desk):
    d, p = desk, desk.page
    open_editor(d)
    target(p, ["표지"], "header")
    for side in ("even", "odd"):
        p.locator("#running-face").select_option(side)
        own(p, "left", side)
    d.targets = [{"path": ["새 제목"], "count": 1}]
    d.source.fill("# 새 제목")
    bindings = p.locator("#reference-bindings")
    for label in ("홀수 쪽", "짝수 쪽"):
        bindings.locator(".reference-row").filter(has_text=label).get_by_label("다시 연결할 대상").select_option(label="새 제목")
    rules = trial(d)
    assert [(r["path"], r["face"], r["values"]["left"]) for r in rules] == [
        (["새 제목"], "even", "even"), (["새 제목"], "odd", "odd")]
    d.reply(0, {"field": "section_rules[0].values.left", "message": "잘못된 식"}, 422)
    expect(p.locator("#running-face")).to_have_value("even")
    expect(p.locator("#running-left")).to_be_focused()
    expect(p.locator("#running-left")).to_have_value("even")


@pytest.mark.parametrize("desk", [True], indirect=True)
def test_installed_visible_default_only_starts_on_matching_face(desk):
    d, p = desk, desk.page
    open_editor(d)
    face = p.locator("#running-face")
    face.select_option("odd")
    own(p, "left", "홀수만")
    expect(p.locator("#running-state")).to_contain_text("머리글 표시")
    for side in ("even", "default"):
        face.select_option(side)
        expect(p.locator("#running-state")).to_contain_text("머리글 숨김")
    target(p, ["표지"], "header")
    face.select_option("first")
    state = p.locator("#running-visible option:checked")
    expect(state).to_contain_text("홀수 쪽 표시")
    expect(state).to_contain_text("짝수 쪽 숨김")


def test_parent_and_child_first_can_coincide_without_forcing_import_visible(desk):
    d, p = desk, desk.page
    open_editor(d)
    p.locator("#running-visible").select_option("show")
    target(p, ["표지"], "header")
    p.locator("#running-face").select_option("first")
    p.locator("#running-visible").select_option("hide")
    own(p, "left", "표지 첫 문구")
    suggested = candidate("header", "right", "자료", variant="first", source_group="f", suggested_face="first")
    p.route("**/api/reference/analyze?*", lambda r: r.fulfill(json={"units": [unit(1, "본문", suggested)], "warnings": []}))
    p.locator("#reference-file").set_input_files({"name": "첫면.docx", "mimeType": "application/octet-stream", "buffer": b"x"})
    p.locator("#reference-analyze").click()
    row = d.row("header", "right")
    row.get_by_label("적용 대상").select_option(label="표지 › 부록")
    row.get_by_role("checkbox").check()
    note = p.locator("#running-state")
    expect(note).to_contain_text("표지 첫 쪽")
    expect(note).to_contain_text("숨김")
    expect(note).to_contain_text("시험 PDF")
    expect(p.get_by_role("group", name="왼쪽 칸")).to_contain_text("표지 첫 문구")
    expect(p.locator("#running-visible")).to_have_value("inherit")
    assert trial(d)[-1] == {"path": ["표지", "부록"], "role": "header", "face": "first", "values": {"right": "자료"}}
    apply(d)


def test_imported_hide_remains_inherited_on_a_more_specific_face(desk):
    d, p = desk, desk.page
    off = candidate("footer", "visible", False, source_group="off", variant="default", suggested_face="default")
    text = candidate("footer", "right", "문구", source_group="right", variant="odd", suggested_face="odd")
    p.route("**/api/reference/analyze?*", lambda r: r.fulfill(json={"units": [unit(1, "본문", text, off)], "warnings": []}))
    d.open()
    p.locator("#reference-file").set_input_files({"name": "홀수.docx", "mimeType": "application/octet-stream", "buffer": b"x"})
    p.locator("#reference-analyze").click()
    d.row("footer", "visible").get_by_role("checkbox").check()
    d.row("footer", "right").get_by_role("checkbox").check()
    expect(p.locator("#running-state")).to_contain_text("바닥글 숨김")
    assert trial(d) == [{"path": [], "role": "footer", "face": "odd", "values": {"right": "문구"}},
                        {"path": [], "role": "footer", "values": {"visible": False}}]


@pytest.mark.parametrize(("hidden_face", "child_face", "auto_show"), [
    ("first", "default", False), ("first", "odd", False), ("first", "even", True),
    ("even", "default", False), ("even", "odd", True),
])
def test_child_import_respects_parent_hide_on_overlapping_pages(desk, hidden_face, child_face, auto_show):
    d, p = desk, desk.page
    d.source.fill("# 표지\n\n제목부터 시작한다.")
    hide = candidate("footer", "visible", False, source_group="hide", variant=hidden_face, suggested_face=hidden_face)
    slot = candidate("footer", "right", "부서", source_group="slot", variant=child_face, suggested_face=child_face)
    p.route("**/api/reference/analyze?*", lambda r: r.fulfill(json={"units": [unit(1, "표지", hide, slot)], "warnings": []}))
    d.open()
    p.locator("#reference-file").set_input_files({"name": "부서.docx", "mimeType": "application/octet-stream", "buffer": b"x"})
    p.locator("#reference-analyze").click()
    d.row("footer", "visible").get_by_role("checkbox").check()
    row = d.row("footer", "right")
    row.get_by_label("적용 대상").select_option(label="표지")
    row.get_by_role("checkbox").check()
    parent, child = trial(d)
    assert parent == {"path": [], "role": "footer", "face": hidden_face, "values": {"visible": False}}
    assert child.get("face", "default") == child_face and child["path"] == ["표지"]
    assert child["values"] == ({"right": "부서", "visible": True} if auto_show else {"right": "부서"})


def test_rebinding_saved_odd_rule_moves_its_edit_and_cleared_slot(desk):
    d, p = desk, desk.page
    open_editor(d)
    target(p, ["표지"], "footer")
    p.locator("#running-face").select_option("odd")
    own(p, "left", "이전")
    own(p, "right", "제거할 값")
    trial(d)
    apply(d)
    open_editor(d)
    target(p, ["표지"], "footer")
    p.locator("#running-face").select_option("odd")
    p.locator("#running-left").fill("수정")
    p.locator("#running-own-right").uncheck()
    p.locator("#reference-bindings").get_by_label("다시 연결할 대상").select_option(label="표지 › 부록")
    assert trial(d) == [{"path": ["표지", "부록"], "role": "footer", "face": "odd", "values": {"left": "수정"}}]
    apply(d)


@pytest.mark.parametrize(("key", "first", "second", "equal"), [
    ("font_pt", 9, "9.0", True),
    ("offset_mm", "+0009.", ".090e2", True),
    ("font_pt", "9.0000076293945312499999", "9.0000076293945312500001", False),
    ("left", "09", "9", False),
])
def test_conflict_compares_lengths_exactly_and_slots_literally(desk, key, first, second, equal):
    d, p = desk, desk.page
    values = [candidate("footer", key, value, source_group=group, variant="odd", suggested_face="odd")
              for group, value in [("A", first), ("B", first)]]
    p.route("**/api/reference/analyze?*", lambda r: r.fulfill(json={"units": [unit(1, "본문", *values)], "warnings": []}))
    d.open()
    p.locator("#reference-file").set_input_files({"name": "홀수.docx", "mimeType": "application/octet-stream", "buffer": b"x"})
    p.locator("#reference-analyze").click()
    for group in ("A", "B"):
        p.locator(f'[data-source-group="{group}"]').get_by_role("checkbox").check()
    p.locator('[data-source-group="B"]').get_by_label("제안 값").fill(second)
    if equal:
        rule, = trial(d)
        assert rule["face"] == "odd" and rule["values"][key] == second
    else:
        p.locator("#reference-trial").click()
        expect(p.locator("#reference-message")).to_contain_text("충돌")
        assert len(d.requests) == 1
