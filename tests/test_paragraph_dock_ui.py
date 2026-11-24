from playwright.sync_api import expect

from test_editor import desk


def test_dock_toolbar_and_menus_preserve_editor_selection_and_proof(desk):
    page = desk.page
    desk.ready()
    source = desk.source
    text = "앞 문장\n\n강조할 문장 뒤"
    source.fill(text)
    source.evaluate("el => { el.focus(); el.setSelectionRange(6, 9, 'backward'); }")
    before = [page.locator(".sheet").bounding_box(), page.locator(".proof-stage").bounding_box()]
    proof = desk.frame.get_attribute("src")
    expect(page.locator("#paragraph-dock")).to_be_hidden()
    page.locator("#toggle-paragraph-dock").click()
    expect(page.locator("#paragraph-dock")).to_be_visible()
    after = [page.locator(".sheet").bounding_box(), page.locator(".proof-stage").bounding_box()]
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
