"""Running-area drafts share the reference proof, commit and file paths."""
import json

import pytest
from playwright.sync_api import expect

from app import style
from test_editor import browser
from test_reference_editor import Review, candidate, unit
from test_slice import server_url


@pytest.fixture
def desk(browser, server_url, monkeypatch, request):
    bootstrap = style.editor_bootstrap()
    defaults = dict(visible=getattr(request, "param", False), left="", center="", right="", font="nanum-myeongjo",
                    color="#000000", font_pt="12.5", offset_mm="7.25")
    monkeypatch.setattr(style, "editor_bootstrap", lambda: {**bootstrap, "running_defaults": defaults})
    with browser.new_context() as context:
        page = context.new_page()
        page.set_default_timeout(3000)
        d = Review(page, server_url)
        d.ready()
        d.targets = [{"path": path, "count": 1} for path in (["표지"], ["표지", "부록"])]
        yield d


def open_editor(desk):
    desk.page.get_by_role("button", name="머리글·바닥글 편집", exact=True).click(timeout=1000)
    expect(desk.page.locator("#running-editor")).to_be_visible()
    expect(desk.page.locator("#running-target option")).to_have_count(3)


def target(page, path, role):
    page.locator("#running-target").select_option(json.dumps(path, ensure_ascii=False, separators=(",", ":")))
    page.locator("#running-role").select_option(role)


def own(page, key, value):
    page.locator("#running-own-" + key).check()
    page.locator("#running-" + key).fill(value)


def trial(d):
    d.submit(d.page.locator("#reference-trial"))
    return d.requests[-1]["style"]["section_rules"]


def apply(d):
    d.reply(len(d.pending) - 1, d.pdf)
    d.page.locator("#reference-apply").click()


def test_direct_inheritance_empty_and_reopen(desk):
    d, p = desk, desk.page
    open_editor(d)
    expect(p.locator("#running-font_pt")).to_have_value("12.5")
    expect(p.locator("#running-offset_mm")).to_have_value("7.25")
    p.locator("#running-visible").select_option("show")
    own(p, "left", "2026 {{기관}}")
    target(p, [], "footer")
    p.locator("#running-visible").select_option("show")
    own(p, "center", "－ {현재쪽} －")
    target(p, ["표지"], "footer")
    p.locator("#running-visible").select_option("hide")
    target(p, ["표지", "부록"], "footer")
    expect(p.locator("#running-visible option:checked")).to_contain_text("숨김")
    expect(p.locator("#running-center")).to_have_value("－ {현재쪽} －")
    own(p, "left", "추가 자료")
    own(p, "center", "")
    rules = trial(d)
    assert rules == [
        {"path": [], "role": "header", "values": {"visible": True, "left": "2026 {{기관}}"}},
        {"path": [], "role": "footer", "values": {"visible": True, "center": "－ {현재쪽} －"}},
        {"path": ["표지"], "role": "footer", "values": {"visible": False}},
        {"path": ["표지", "부록"], "role": "footer", "values": {"left": "추가 자료", "center": ""}}]
    apply(d)
    p.locator("summary").click()
    with p.expect_download() as downloaded:
        p.locator("#save-style").click()
    raw = downloaded.value.path().read_bytes()
    assert json.loads(raw)["section_rules"] == rules
    p.locator("#style-file").set_input_files({"name": "서식.json", "mimeType": "application/json", "buffer": raw})
    expect(p.locator("#save-style")).to_be_enabled()
    open_editor(d)
    target(p, ["표지", "부록"], "footer")
    p.locator("#running-own-center").uncheck()
    expect(p.locator("#running-center")).to_have_value("－ {현재쪽} －")
    assert trial(d)[-1]["values"] == {"left": "추가 자료"}
    apply(d)
    d.targets = [{"path": ["새 표지"], "count": 1}, {"path": ["새 부록"], "count": 1}]
    d.source.fill("# 새 표지\n\n# 새 부록")
    open_button = p.locator("#open-running")
    open_button.click()
    bindings = p.locator("#reference-bindings").get_by_label("다시 연결할 대상")
    bindings.nth(2).select_option(label="새 표지")
    bindings.nth(3).select_option(label="새 부록")
    assert [r["path"] for r in trial(d)] == [[], [], ["새 표지"], ["새 부록"]]
    apply(d)


def test_page_ten_error_and_recovery(desk):
    d, p = desk, desk.page
    original_source = d.source.input_value()
    original_pdf = d.frame.get_attribute("src")
    open_editor(d)
    p.locator("#running-visible").select_option("show")
    own(p, "right", "{다른필드}")
    trial(d)
    d.reply(0, {"field": "section_rules[0].values.right", "message": "1번째 문자에 알 수 없는 필드가 있습니다."}, 422)
    expect(p.locator("#running-right")).to_have_attribute("aria-invalid", "true")
    expect(p.locator("#running-right")).to_have_accessible_description("1번째 문자에 알 수 없는 필드가 있습니다.")
    p.locator("#running-right").fill("긴 문구 {현재쪽}")
    trial(d)
    error = {"field": "source", "message": "10쪽 머리글의 가운데 칸과 오른쪽 칸이 겹칩니다."}
    d.reply(1, error, 422)
    expect(p.locator("#reference-message")).to_contain_text("10쪽")
    expect(p.locator("#reference-apply")).to_be_disabled()
    assert p.locator("#preview").get_attribute("src") == original_pdf
    p.locator("#reference-cancel").click()
    assert p.evaluate("styleForm.get().section_rules") is None
    expect(d.source).to_have_value(original_source)
    open_editor(d)
    own(p, "right", "{현재쪽}")
    p.locator("#running-visible").select_option("show")
    trial(d)
    apply(d)
    good = d.frame.get_attribute("src")
    d.source.fill("# 늘어난 원고")
    open_editor(d)
    trial(d)
    d.reply(3, error, 422)
    expect(p.locator("#reference-message")).to_contain_text("10쪽")
    p.locator("#reference-cancel").click()
    d.submit(d.export)
    d.reply(4, error, 422)
    expect(p.get_by_role("alert")).to_contain_text("10쪽")
    assert d.frame.get_attribute("src") == good
    d.submit(d.export)
    with p.expect_download() as saved:
        d.reply(5, d.pdf)
    assert saved.value.url == d.frame.get_attribute("src")


def test_reference_variant_and_retry_preserve_authors_choice(desk):
    d, p = desk, desk.page
    def read(role, key, value, group, variant, basis="read", **extra):
        return candidate(role, key, value, basis, source_group=group, variant=variant, **extra)
    common = read("header", "left", "기관", "h1", "default")
    first = read("header", "left", "기관", "h2", "first")
    automatic = read("footer", "center", "－ {현재쪽} －", "footer-center", "unknown", "estimated")
    single = read("footer", "center", "－ 17 －", "footer-center", "unknown", "confirm",
                  options=[{"kind": "literal", "value": "－ 17 －"}, {"kind": "page", "value": "－ {현재쪽} －"}])
    def analyze(route):
        retry = "page=1" in route.request.url
        route.fulfill(json={"units": [unit(1, "본문", common, first, single if retry else automatic)], "warnings": []})
    p.route("**/api/reference/analyze?*", analyze)
    d.open()
    p.locator("#reference-file").set_input_files({"name": "증거.pdf", "mimeType": "application/pdf", "buffer": b"data"})
    p.locator("#reference-analyze").click()
    header = p.locator('[data-source-group="h2"][data-key="left"]')
    expect(header).to_contain_text("첫 쪽")
    header.get_by_role("checkbox").check()
    expect(p.locator("#running-role")).to_have_value("header")
    expect(p.locator("#running-visible")).to_have_value("show")
    p.locator("#running-left").fill("편집한 기관")
    footer = d.row("footer", "center")
    footer.get_by_role("checkbox").check()
    p.locator("#running-center").fill("제{현재쪽}쪽")
    p.get_by_role("button", name="1쪽 다시 분석").click()
    expect(footer).to_contain_text("재분석")
    expect(p.locator("#running-center")).to_have_value("제{현재쪽}쪽")
    p.locator("#reference-trial").click()
    expect(p.locator("#reference-message")).to_contain_text("재분석")
    footer.get_by_role("button", name="기존 선택 유지").click()
    rules = trial(d)
    assert rules == [{"role": "header", "path": [], "values": {"left": "편집한 기관", "visible": True}},
                     {"role": "footer", "path": [], "values": {"center": "제{현재쪽}쪽", "visible": True}}]
    assert "문단 3" not in json.dumps(rules, ensure_ascii=False)
    apply(d)


def test_scan_requires_number_choice_and_preserves_existing_footer(desk):
    d, p = desk, desk.page
    open_editor(d)
    target(p, [], "footer")
    p.locator("#running-visible").select_option("show")
    own(p, "center", "{현재쪽}")
    trial(d)
    apply(d)
    scan = candidate("header", "right", "2026 · 17", "confirm", source_group="edge",
                     variant="unknown", suggested_face=None, options=[{"kind": "literal", "value": "2026 · 17"},
                                                  {"kind": "page", "value": "2026 · {현재쪽}"}])
    p.route("**/api/reference/analyze?*", lambda r: r.fulfill(json={"units": [unit(1, "본문", scan)], "warnings": []}))
    d.open()
    p.locator("#reference-file").set_input_files({"name": "번호.png", "mimeType": "image/png", "buffer": b"fixture"})
    p.locator("#reference-analyze").click()
    row = d.row("header", "right")
    row.get_by_label("적용할 면", exact=True).select_option("default")
    row.get_by_role("checkbox").check()
    expect(p.locator("#running-state")).to_contain_text("표시도 함께 제안")
    p.locator("#reference-trial").click()
    expect(p.locator("#reference-message")).to_contain_text("번호 해석")
    expect(p.locator("#reference-apply")).to_be_disabled()
    row.get_by_label("번호 해석").select_option("literal")
    expect(p.locator("#running-right")).to_have_value("2026 · 17")
    row.get_by_label("번호 해석").select_option("page")
    expect(p.locator("#running-right")).to_have_value("2026 · {현재쪽}")
    p.locator("#running-visible").select_option("hide")
    rules = trial(d)
    assert rules == [{"role": "footer", "path": [], "values": {"center": "{현재쪽}", "visible": True}},
                     {"role": "header", "path": [], "values": {"right": "2026 · {현재쪽}", "visible": False}}]
    apply(d)


def test_imported_choice_can_replace_direct_draft_and_restore_it(desk):
    d, p = desk, desk.page
    open_editor(d)
    own(p, "left", "저자의 문구")
    initial = candidate("header", "left", "새 문구", source_group="h", variant="default")
    p.route("**/api/reference/analyze?*", lambda r: r.fulfill(json={"units": [unit(1, "본문", initial)], "warnings": []}))
    p.locator("#reference-file").set_input_files({"name": "문서.docx", "mimeType": "application/octet-stream", "buffer": b"x"})
    p.locator("#reference-analyze").click()
    row = d.row("header", "left")
    row.get_by_role("checkbox").check()
    expect(p.locator("#running-left")).to_have_value("새 문구")
    p.locator("#running-left").fill("참고 문구 수정")
    expect(row.get_by_label("제안 값", exact=True)).to_have_value("참고 문구 수정")
    row.get_by_role("checkbox").uncheck()
    expect(p.locator("#running-left")).to_have_value("저자의 문구")
    expect(p.locator("#running-visible")).to_have_value("inherit")
    assert trial(d) == [{"role": "header", "path": [], "values": {"left": "저자의 문구"}}]
    apply(d)


def test_retry_keeps_multipage_number_evidence_when_page_has_no_candidate(desk):
    d, p = desk, desk.page
    original = candidate("footer", "center", "－ {현재쪽} －", "estimated", source_group="page:1:f", variant="unknown")
    original["evidence"] = "1쪽 － 17 － · 2쪽 － 18 －"
    retries = iter([[{**original, "evidence": "1쪽에서 재확인"}], []])
    def analyze(route):
        found = next(retries) if "page=1" in route.request.url else [original]
        route.fulfill(json={"units": [unit(1, "본문", *found)], "warnings": []})
    p.route("**/api/reference/analyze?*", analyze)
    d.open()
    p.locator("#reference-file").set_input_files({"name": "표본.pdf", "mimeType": "application/pdf", "buffer": b"x"})
    p.locator("#reference-analyze").click()
    row = d.row("footer", "center")
    expect(row).to_contain_text(original["evidence"])
    p.get_by_role("button", name="1쪽 다시 분석").click()
    expect(row).to_contain_text("1쪽에서 재확인")
    expect(row).to_contain_text(original["evidence"])
    p.get_by_role("button", name="1쪽 다시 분석").click()
    expect(row).to_contain_text("다시 찾지 못했습니다.")
    expect(row).to_contain_text(original["evidence"])
    row.get_by_role("button", name="기존 선택 유지").click()
    row.get_by_role("checkbox").check()
    assert trial(d) == [{"role": "footer", "path": [], "values": {"visible": True, "center": "－ {현재쪽} －"}}]
    apply(d)


@pytest.mark.parametrize("desk", [True], indirect=True)
def test_visible_default_only_applies_in_a_bound_area(desk):
    d, p = desk, desk.page
    open_editor(d)
    visible = p.locator("#running-visible option:checked")
    state = p.locator("#running-state")
    expect(visible).to_contain_text("상속 (숨김)")
    target(p, ["표지", "부록"], "header")
    own(p, "left", "부록 문구")
    expect(visible).to_contain_text("상속 (표시)")
    expect(state).to_contain_text("머리글 표시")
    target(p, ["표지"], "header")
    expect(state).to_contain_text("머리글 숨김")
    target(p, ["표지", "부록"], "footer")
    expect(state).to_contain_text("바닥글 숨김")
    d.analyze()
    row = d.row("header", "left")
    row.get_by_label("적용 대상").select_option(label="표지 › 부록")
    row.get_by_role("checkbox").check()
    expect(visible).to_contain_text("상속 (표시)")
    expect(state).not_to_contain_text("함께 제안")
    row.get_by_label("적용 대상").select_option(label="문서 전체")
    expect(state).to_contain_text("표시도 함께 제안")
    assert trial(d) == [
        {"path": ["표지", "부록"], "role": "header", "values": {"left": "부록 문구"}},
        {"path": [], "role": "header", "values": {"left": "기관명", "visible": True}},
    ]


def test_rebind_direct_edits_when_heading_changes_before_first_trial(desk):
    d, p = desk, desk.page
    open_editor(d)
    target(p, ["표지", "부록"], "header")
    own(p, "right", "보충 {현재쪽}")
    d.targets = [{"path": ["보충 자료"], "count": 1}]
    d.source.fill("# 보충 자료\n\n기록")
    bind = p.locator("#reference-bindings").get_by_label("다시 연결할 대상")
    expect(bind).to_have_count(1)
    bind.select_option(label="보충 자료")
    assert trial(d) == [{"path": ["보충 자료"], "role": "header", "values": {"right": "보충 {현재쪽}"}}]
    apply(d)
