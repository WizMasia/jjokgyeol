import json

from playwright.sync_api import expect

from settings_ui import close_settings, open_settings
from test_editor import desk
from test_jjok_editor import entries, save_document


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


def test_italic_within_bold_does_not_remove_bold(desk):
    desk.ready()
    source = desk.source
    source.fill("**강조 문장**")
    source.evaluate("el => { el.focus(); el.setSelectionRange(2, 7); }")
    desk.page.locator("#format-italic").click()
    expect(source).to_have_value("***강조 문장***")
    expect(source).to_be_focused()


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
    page.locator("#paragraph-leading-mode").select_option("manual")
    page.locator("#paragraph-leading-value").fill("18")
    with page.expect_request(lambda request: request.url.endswith("/api/paragraph/align") and
                             request.post_data_json.get("leading_pt") == "18"):
        page.locator("#paragraph-leading-value").press("Enter")


def test_ai_typeset_keeps_last_pdf_visible_until_job_finishes(desk):
    desk.ready()
    page = desk.page
    page.locator("#proof-choice").select_option("rule")
    previous = desk.frame.get_attribute("src")
    with page.expect_request("http://127.0.0.1/api/render"):
        page.locator("#ai-typeset").click()
    expect(page.locator("#status")).to_contain_text("AI 조판 중")
    assert desk.frame.get_attribute("src") == previous
    route = desk.pending[0]
    body = (b'--p\r\nContent-Disposition: form-data; name="rule"; filename="rule.pdf"\r\nContent-Type: application/pdf\r\n\r\n'
            + desk.pdf + b'\r\n--p\r\nContent-Disposition: form-data; name="model"; filename="model.pdf"\r\nContent-Type: application/pdf\r\n\r\n'
            + desk.pdf + b'\r\n--p\r\nContent-Disposition: form-data; name="spacing"\r\n\r\n{}\r\n--p--\r\n')
    route.fulfill(headers={"Content-Type": "multipart/form-data; boundary=p", "X-Jjokgyeol-Decision": "model"}, body=body)
    expect(page.locator("#status")).to_have_text("최신 버전")
    expect(page.locator("#proof-choice")).to_have_value("model")
    assert desk.frame.get_attribute("src") != previous


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
    raw = b"\xef\xbb\xbf" + "앞\r\n중간\r끝".encode()
    page.locator("#file").set_input_files({"name": "줄끝.md", "mimeType": "text/markdown", "buffer": raw})
    expect(source).to_have_value("앞\n중간\n끝")
    source.evaluate("el => { el.focus(); el.setSelectionRange(2, 4); }")
    page.locator("#format-bold").click()
    expect(source).to_have_value("앞\n**중간**\n끝")
    source.evaluate("el => el.setSelectionRange(4, 6)")
    page.locator("#format-bold").click()
    expect(source).to_have_value("앞\n중간\n끝")
    source.press("ControlOrMeta+z")
    expect(source).to_have_value("앞\n**중간**\n끝")
    source.press("ControlOrMeta+z")
    expect(source).to_have_value("앞\n중간\n끝")
    source.press("ControlOrMeta+Shift+z")
    expect(source).to_have_value("앞\n**중간**\n끝")
    with page.expect_download() as saved:
        page.locator("#save").click()
    assert saved.value.path().read_bytes() == b"\xef\xbb\xbf" + "앞\r\n**중간**\r끝".encode()
