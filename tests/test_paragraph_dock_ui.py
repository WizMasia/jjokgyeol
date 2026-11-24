import json

import pytest
from playwright.sync_api import expect

from app.paragraph_edit import edit_alignment

from settings_ui import close_settings, open_settings
from test_editor import browser, desk
from test_jjok_editor import entries, save_document
from test_spacing_editor import proof_reply


def test_dock_toolbar_and_menus_preserve_editor_selection_and_proof(desk):
    page = desk.page
    desk.ready()
    source = desk.source
    text = "앞 문장\n\n강조할 문장 뒤"
    source.fill(text)
    source.evaluate("el => { el.focus(); el.setSelectionRange(6, 9, 'backward'); }")
    before = [page.locator(".sheet").bounding_box(), page.locator(".proof-stage").bounding_box()]
    assert abs(before[0]["y"] - before[1]["y"]) <= 1
    proof = desk.frame.get_attribute("src")
    expect(page.locator("#paragraph-dock")).to_be_hidden()
    page.locator("#toggle-paragraph-dock").click()
    expect(page.locator("#paragraph-dock")).to_be_visible()
    after = [page.locator(".sheet").bounding_box(), page.locator(".proof-stage").bounding_box()]
    assert abs(after[0]["y"] - after[1]["y"]) <= 1
    for old, new in zip(before, after):
        assert abs(old["x"] - new["x"]) <= 1
        assert abs(old["y"] - new["y"]) <= 1
        assert abs(old["width"] - new["width"]) <= 1
    assert source.evaluate("el => [el.selectionStart, el.selectionEnd, el.selectionDirection]") == [6, 9, "backward"]
    assert desk.frame.get_attribute("src") == proof
    page.locator("#format-bold").click()
    expect(source).to_have_value("앞 문장\n\n**강조할** 문장 뒤")
    source.press("ControlOrMeta+z")
    expect(source).to_have_value(text)
    source.press("ControlOrMeta+Shift+z")
    expect(source).to_have_value("앞 문장\n\n**강조할** 문장 뒤")
    file_menu = page.locator(".app-menu").first
    expect(file_menu.locator("summary")).to_be_visible()
    file_menu.locator("summary").click()
    expect(file_menu.locator("summary")).to_have_attribute("aria-expanded", "true")
    expect(page.get_by_role("menuitem", name="원고 저장")).to_be_visible()
    page.keyboard.press("Escape")
    expect(file_menu.locator("summary")).to_have_attribute("aria-expanded", "false")
    expect(page.get_by_role("menuitem", name="원고 저장")).to_be_hidden()
    expect(file_menu.locator("summary")).to_be_focused()
    file_menu.locator("summary").press("ArrowDown")
    expect(page.get_by_role("menuitem", name="원고 열기")).to_be_focused()
    page.keyboard.press("ArrowDown")
    expect(page.get_by_role("menuitem", name="원고 가져오기")).to_be_focused()
    page.keyboard.press("Escape")
    expect(file_menu.locator("summary")).to_be_focused()
    file_menu.locator("summary").click()
    with page.expect_download() as saved:
        page.get_by_role("menuitem", name="원고 저장").click()
    assert saved.value.path().read_text() == "앞 문장\n\n**강조할** 문장 뒤"


def test_format_toggle_does_not_insert_empty_emphasis(desk):
    desk.ready()
    page = desk.page
    source = desk.source
    source.fill("원고")
    source.evaluate("el => { el.focus(); el.setSelectionRange(1, 1); }")
    page.locator("#format-italic").click()
    expect(source).to_have_value("원고")
    expect(source).to_be_focused()


def test_document_spacing_controls_and_ai_action_exist(desk):
    desk.ready()
    page = desk.page
    expect(page.locator("#paragraph-leading-mode")).to_have_count(1)
    expect(page.locator("#paragraph-gap-pt")).to_have_count(1)
    expect(page.locator("#ai-typeset")).to_have_count(1)


@pytest.mark.parametrize(("marker", "action"), [("**", "italic"), ("*", "bold")])
def test_nested_emphasis_toggles_only_requested_marker(desk, marker, action):
    desk.ready()
    source = desk.source
    inner = "강조 문장"
    source.fill(marker + inner + marker)
    source.focus()
    start = len(marker)
    source.evaluate("(el, start) => el.setSelectionRange(start, start + 5, 'backward')", start)
    button = desk.page.locator(f"#format-{action}")
    button.click()
    expect(source).to_have_value("***" + inner + "***")
    assert source.evaluate("el => [el.selectionStart, el.selectionEnd, el.selectionDirection]") == [3, 8, "backward"]
    button.click()
    expect(source).to_have_value(marker + inner + marker)
    expect(source).to_be_focused()
    source.press("ControlOrMeta+z")
    expect(source).to_have_value("***" + inner + "***")
    source.press("ControlOrMeta+Shift+z")
    expect(source).to_have_value(marker + inner + marker)


def test_leading_query_and_manual_value_use_paragraph_edit(desk):
    desk.ready()
    page = desk.page
    source = desk.source
    requests = []

    def paragraph(route):
        payload = route.request.post_data_json
        requests.append(payload)
        route.fulfill(json={"source": payload["source"], "supported": True,
                            "start_line": 0, "align": None, "leading_pt": payload.get("leading_pt"),
                            "space_before_pt": None, "space_after_pt": None})

    page.route("**/api/paragraph/align", paragraph)
    source.fill("한 문단")
    source.evaluate("el => { el.focus(); el.setSelectionRange(2, 2); }")
    page.locator("#toggle-paragraph-dock").click()
    with page.expect_request(lambda request: request.url.endswith("/api/paragraph/align") and
                             request.post_data_json.get("leading") is True):
        page.wait_for_timeout(350)
    expect(page.locator("#paragraph-leading-mode")).to_have_value("")
    assert requests[-1]["leading"] is True
    expect(page.locator("#paragraph-leading-measure")).to_be_hidden()
    page.locator("#paragraph-leading-mode").select_option("manual")
    expect(page.locator("#paragraph-leading-measure")).to_be_visible()
    page.locator("#paragraph-leading-value").fill("18")
    with page.expect_request(lambda request: request.url.endswith("/api/paragraph/align") and
                             request.post_data_json.get("leading_pt") == "18"):
        page.locator("#paragraph-leading-value").press("Enter")


@pytest.mark.parametrize(("decision", "reason", "completion"), [
    ("model", None, "AI 조판 완료"),
    ("mixed", "rate_limit", "일부 AI 적용 · 모델 서버의 요청 한도"),
    ("rule", "timeout", "규칙 조판 완료 · 응답 시간 초과"),
])
def test_ai_status_reports_actual_outcome(desk, decision, reason, completion):
    desk.ready()
    page = desk.page
    choice = page.locator("#proof-choice")
    status = page.locator("#status")
    choice.select_option("rule")
    previous = desk.frame.get_attribute("src")
    page.evaluate("applyConnection({provider: 'builtin'})")
    desk.submit(page.locator("#ai-typeset"))
    expect(status).to_contain_text("AI 조판 중")
    assert desk.frame.get_attribute("src") == previous
    proof_reply(desk, 0, {}, decision, reason)
    expect(status).to_contain_text(completion)
    expect(choice).to_have_value("model" if decision != "rule" else "rule")
    assert desk.frame.get_attribute("src") != previous


def test_ai_promotes_pending_rule_only_job_without_promising_model_completion(desk):
    desk.ready()
    page = desk.page
    status = page.locator("#status")
    previous = desk.frame.get_attribute("src")
    desk.submit(desk.refresh)
    expect(status).to_have_text("조판 중")
    page.locator("#ai-typeset").click()
    expect(status).to_contain_text("AI 조판 중")
    assert len(desk.requests) == 2
    assert desk.requests[-1]["connection"] == {"provider": "rule"}
    assert desk.frame.get_attribute("src") == previous
    desk.reply(0, desk.pdf)
    expect(status).to_have_text("규칙 조판 완료")
    assert desk.frame.get_attribute("src") != previous


def test_model_refresh_ignores_stale_ai_response(desk):
    desk.ready()
    page = desk.page
    status = page.locator("#status")
    previous = desk.frame.get_attribute("src")
    page.evaluate("applyConnection({provider: 'builtin'})")
    desk.submit(desk.refresh)
    expect(status).to_contain_text("AI 조판 중")
    desk.source.fill("새 문단")
    desk.submit(desk.refresh)
    assert desk.frame.get_attribute("src") == previous
    proof_reply(desk, 1, {}, "mixed", "rate_limit")
    expect(status).to_contain_text("일부 AI 적용")
    current = desk.frame.get_attribute("src")
    with page.expect_request_finished(lambda r: r == desk.pending[0].request):
        proof_reply(desk, 0, {}, "model")
    page.evaluate("() => new Promise(resolve => requestAnimationFrame(resolve))")
    expect(status).to_contain_text("일부 AI 적용")
    assert desk.frame.get_attribute("src") == current


def test_wide_dock_keeps_target_and_receipt_in_view(desk):
    desk.ready()
    page = desk.page
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.route("**/api/paragraph/align", lambda route: route.fulfill(json=edit_alignment(route.request.post_data_json)))
    desk.source.fill("첫 문단\n\n다음 문단")
    desk.source.evaluate("el => el.setSelectionRange(8, 10)")
    page.locator("#toggle-paragraph-dock").click()
    expect(page.locator("#paragraph-target-help")).to_contain_text("원고 3행")
    desk.submit(desk.refresh)
    proof_reply(desk, 0, {"rule": [{"before_line": 0, "after_line": 2, "gap_sp": 65536,
                                    "source": "default"}]}, "rule")
    expect(page.locator("#paragraph-spacing-applied")).to_contain_text("위 1 pt")
    for selector in ("#paragraph-target-help", "#paragraph-spacing-applied", "#app-status-bar"):
        expect(page.locator(selector)).to_be_in_viewport(ratio=1)
    sheet, stage = (page.locator(selector).bounding_box() for selector in (".sheet", ".proof-stage"))
    assert abs(sheet["y"] - stage["y"]) <= 1
    assert abs(sheet["height"] - stage["height"]) <= 1

    page.set_viewport_size({"width": 360, "height": 800})
    mode = page.locator("#paragraph-leading-mode")
    mode.select_option("manual")
    manual = page.locator("#paragraph-leading-value")
    manual.scroll_into_view_if_needed()
    expect(manual).to_be_in_viewport(ratio=1)
    mode.select_option("")
    expect(manual).to_be_hidden()


def test_document_gap_blank_inherits_and_zero_stays_explicit(desk):
    desk.ready()
    page = desk.page
    open_settings(page)
    page.locator("#style details > summary").click()
    payloads = []

    def export_style(route):
        payload = route.request.post_data_json
        payloads.append(payload)
        route.fulfill(json={"paragraph_gap_sp": round(float(payload["paragraph_gap_pt"]) * 65536)}
                           if "paragraph_gap_pt" in payload else {})

    page.route("**/api/style/export", export_style)
    gap = page.locator("#paragraph-gap-pt")
    gap.fill("0")
    with page.expect_download() as explicit:
        page.locator("#save-style").click()
    assert json.loads(explicit.value.path().read_text())["paragraph_gap_sp"] == 0
    assert payloads[-1]["paragraph_gap_pt"] == "0"
    gap.fill("")
    with page.expect_download() as inherited:
        page.locator("#save-style").click()
    assert "paragraph_gap_sp" not in json.loads(inherited.value.path().read_text())
    assert "paragraph_gap_pt" not in payloads[-1]
    gap.fill("6.25")
    close_settings(page)
    saved = entries(save_document(page))
    assert json.loads(saved["style.json"])["paragraph_gap_sp"] == 409600


def test_emphasis_wrap_unwrap_undo_and_save_original_mixed_endings(desk):
    desk.ready()
    page = desk.page
    source = desk.source
    bom = b"\xef\xbb\xbf"
    unformatted = "앞\r\n가\r나\r\n끝"
    formatted = "앞\r\n**가\r나**\r\n끝"
    page.locator("#file").set_input_files({"name": "줄끝.md", "mimeType": "text/markdown",
                                          "buffer": bom + unformatted.encode()})
    expect(source).to_have_value("앞\n가\n나\n끝")
    source.evaluate("el => { el.focus(); el.setSelectionRange(2, 5); }")

    def check(raw):
        expect(source).to_have_value(raw.replace("\r\n", "\n").replace("\r", "\n"))
        with page.expect_download() as saved:
            page.locator("#save").click()
        assert saved.value.path().read_bytes() == bom + raw.encode()

    page.locator("#format-bold").click()
    check(formatted)
    page.locator("#format-bold").click()
    check(unformatted)
    source.press("ControlOrMeta+z")
    check(formatted)
    source.press("ControlOrMeta+z")
    check(unformatted)
    source.press("ControlOrMeta+Shift+z")
    check(formatted)


@pytest.mark.parametrize("outcome", ["success", "error", "switch"])
def test_pending_gap_pin_preserves_leading_and_blocks_other_pins(desk, outcome):
    desk.ready()
    page = desk.page
    edits = []

    def paragraph(route):
        payload = route.request.post_data_json
        if "space_before_pt" in payload or "leading_pt" in payload:
            edits.append(route)
            return
        route.fulfill(json=edit_alignment(payload))

    page.route("**/api/paragraph/align", paragraph)
    original = "처음\n\n<!-- 쪽결 leading_sp=auto -->\n중간\n\n마지막"
    desk.source.fill(original)
    desk.source.evaluate("(el, offset) => el.setSelectionRange(offset, offset)", original.index("중간"))
    page.locator("#toggle-paragraph-dock").click()
    mode = page.locator("#paragraph-leading-mode")
    expect(mode).to_have_value("auto")
    desk.submit(desk.refresh)
    proof_reply(desk, 0, {"rule": [], "model": [
        {"before_line": 0, "after_line": 3, "gap_sp": 65536, "source": "ai"},
        {"before_line": 3, "after_line": 5, "gap_sp": 131072, "source": "ai"}]})
    pins = page.locator("#paragraph-spacing-applied").get_by_role("button", name="고정")
    expect(pins).to_have_count(2)
    with page.expect_request(lambda r: r.url.endswith("/paragraph/align") and
                             "space_before_pt" in r.post_data_json):
        pins.first.click()
    expect(pins.last).to_be_disabled()
    pins.last.evaluate("button => button.click()")
    assert len(edits) == 1
    route = edits[0]
    payload = route.request.post_data_json
    assert "space_after_pt" not in payload and "leading_pt" not in payload
    if outcome == "error":
        route.fulfill(status=422, json={"message": "문단 편집 실패"})
        expect(page.locator("#paragraph-align-message")).to_have_text("문단 편집 실패")
        expect(desk.source).to_have_value(original)
    else:
        if outcome == "switch":
            page.locator("#proof-choice").select_option("rule")
        result = edit_alignment(payload)
        route.fulfill(json=result)
        if outcome == "switch":
            expect(desk.source).to_have_value(original)
        else:
            expect(desk.source).to_have_value(result["source"])
            assert "before_sp=65536" in result["source"]
    expect(mode).to_be_enabled()
    expect(mode).to_have_value("auto")
    if outcome == "switch":
        expect(desk.source).to_have_value(original)
