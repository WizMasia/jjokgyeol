"""Paragraph and role controls in the shared manuscript and settings views."""
import json
import re

import pytest
from playwright.sync_api import expect

from settings_ui import close_settings, open_settings
from test_editor import browser, desk
from test_jjok_editor import entries, save_document, upload
from test_reference_editor import review as editor
from test_slice import server_url


def select_text(source, start, end):
    source.evaluate("(el, [a,b]) => { el.focus(); el.setSelectionRange(a, b, 'backward'); }", [start, end])


def connect_paragraph_service(editor):
    def transport(route):
        headers = {key: value for key, value in route.request.headers.items() if key != "host"}
        headers.update(origin=editor.service, referer=editor.service + "/")
        route.fulfill(response=route.fetch(url=editor.service + "/api/paragraph/align", headers=headers))

    editor.page.route("**/api/paragraph/align", transport)


def test_role_controls_share_body_values_and_restore_defaults(desk):
    desk.ready()
    p = desk.page
    open_settings(p)
    p.locator("#style summary").click()
    role = p.locator("#text-role")
    size = p.get_by_label("대상 글자 크기", exact=True)
    font = p.get_by_label("대상 글꼴", exact=True)
    align = p.locator("[data-text=align]")
    expect(role.locator("option").first).to_have_text("0단계 본문")
    expect(size).to_have_value("11")
    expect(font).to_have_value(p.locator("#body_font").input_value())
    p.evaluate("""() => {
      window.bodyEdits = [];
      document.querySelector('#style').addEventListener('input', e => bodyEdits.push(e.target.id));
    }""")
    size.fill("12.0000152587890625")
    font.select_option("nanum-gothic")
    assert p.evaluate("bodyEdits") == ["body_pt", "body_font"]
    align.select_option("right")
    expect(p.locator("#body_pt")).to_have_value("12.0000152587890625")
    expect(p.locator("#body_font")).to_have_value("nanum-gothic")
    p.locator("#body_pt").fill("13.0000152587890625")
    expect(size).to_have_value("13.0000152587890625")
    for i in range(1, 7):
        role.select_option(f"heading{i}")
        align.select_option("center")
        size.fill(f"{14+i}.0000152587890625")
    role.select_option("heading2")
    size.fill("")
    align.select_option("")
    for name in ("note", "gloss"):
        role.select_option(name)
        expect(align).to_be_hidden()
        expect(size).to_be_visible()
        expect(font).to_be_visible()
    styles = p.evaluate("styleForm.get().text_styles")
    assert styles == {
        "body": {"align": "right"},
        **{f"heading{i}": {"align": "center", "font_pt": f"{14+i}.0000152587890625"}
           for i in (1, 3, 4, 5, 6)},
    }


@pytest.mark.parametrize("with_pdf", [False, True])
def test_alignment_edit_undo_and_document_roundtrip(editor, with_pdf):
    p = editor.page
    src = editor.source
    text = "# 제목\r\n\r날짜 👏\n작성자\r\n\r\n끝"
    marker = "<!-- 쪽결 정렬=오른쪽 -->"
    upload(p, "#file", "원문.md", ("\ufeff" + text).encode())
    view = "# 제목\n\n날짜 👏\n작성자\n\n끝"
    expect(src).to_have_value(view)
    connect_paragraph_service(editor)
    start = view.index("날짜")
    end = view.index("작성자") + 1  # JS counts the emoji as two UTF-16 code units.
    select_text(src, start, end)
    align = p.locator("#paragraph-align")
    with p.expect_request(lambda r: r.url.endswith("/api/paragraph/align") and "align" in r.post_data_json) as req:
        align.select_option("right")
    assert req.value.post_data_json == {"source": text, "first_line": 2, "last_line": 2, "align": "right"}
    adjusted = view.replace("날짜", marker + "\n날짜")
    expect(src).to_have_value(adjusted)
    expect(src).to_be_focused()
    assert src.evaluate("e => [e.selectionStart,e.selectionEnd,e.selectionDirection]") == [
        start + len(marker) + 1, end + len(marker) + 1, "backward"]
    src.press("ControlOrMeta+z")
    expect(src).to_have_value(view)
    with p.expect_download() as original:
        p.locator("#save").click()
    assert original.value.path().read_bytes() == ("\ufeff" + text).encode()
    select_text(src, start, end)
    align.select_option("right")
    expect(src).to_have_value(adjusted)
    align.select_option("center")
    expect(src).to_have_value(adjusted.replace("오른쪽", "가운데"))
    align.select_option("")
    expect(src).to_have_value(view)
    align.select_option("right")
    expect(src).to_have_value(adjusted)

    open_settings(p)
    p.locator("#style summary").click()
    p.locator("[data-text=font_pt]").fill("12.0000152587890625")
    p.locator("[data-text=align]").select_option("left")
    p.locator("#text-role").select_option("heading1")
    p.locator("[data-text=align]").select_option("center")
    p.get_by_label("제목 틀", exact=True).select_option("box")
    p.locator("[data-text=font_pt]").fill("19.0000152587890625")
    close_settings(p)
    if with_pdf:
        editor.submit(editor.refresh)
        editor.reply(0, editor.pdf)
    p.locator("#include-proof").set_checked(with_pdf)
    saved = save_document(p)
    contents = entries(saved)
    assert ("proof.pdf" in contents) == with_pdf
    raw = contents["manuscript.md"].decode()
    unmarked, count = re.subn(re.escape(marker) + r"(?:\r\n|\r|\n)", "", raw, count=1)
    assert count == 1 and unmarked == "\ufeff" + text
    style = json.loads(contents["style.json"])
    assert style["body_sp"] == 12 * 65536 + 1
    assert style["text_styles"]["heading1"] == {"align": "center", "frame": "box", "font_sp": 19 * 65536 + 1}
    assert style["text_styles"]["body"] == {"align": "left"}
    src.fill("다른 원고")
    p.once("dialog", lambda dialog: dialog.accept())
    upload(p, "#document-file", "교정.jjok", saved)
    expect(src).to_have_value(adjusted)
    select_text(src, start, start)
    expect(align).to_have_value("right")
    assert entries(save_document(p))["style.json"] == contents["style.json"]
    open_settings(p)
    frame = p.get_by_label("제목 틀", exact=True)
    expect(frame).to_have_value("box")
    frame.select_option("")
    assert "frame" not in p.evaluate("styleForm.get().text_styles.heading1")
    for role in ("body", "heading2", "heading6", "note", "gloss"):
        p.locator("#text-role").select_option(role)
        expect(frame).to_be_hidden()


def hold_alignment_edits(page):
    pending = []

    def hold(route):
        payload = route.request.post_data_json
        if "align" in payload:
            pending.append(route)
        else:
            route.fulfill(json={"source": payload["source"], "align": None, "supported": True, "message": ""})

    page.route("**/api/paragraph/align", hold)
    return pending


@pytest.mark.parametrize("status", [200, 422])
def test_alignment_reply_ignores_moved_selection(desk, status):
    desk.ready()
    p = desk.page
    text = "첫 문단\n\n다음 문단"
    desk.source.fill(text)
    select_text(desk.source, 0, 0)
    pending = hold_alignment_edits(p)
    align = p.locator("#paragraph-align")
    with p.expect_request(lambda r: r.url.endswith("/api/paragraph/align") and "align" in r.post_data_json):
        align.select_option("right")
    select_text(desk.source, len(text), len(text))
    with p.expect_request_finished(lambda r: r == pending[0].request):
        pending[0].fulfill(status=status, json={"supported": True, "source": "OLD", "message": "OLD", "align": "right"})
    expect(desk.source).to_have_value(text)
    expect(align).to_have_value("")
    assert "OLD" not in p.locator("main").inner_text()


def test_undo_reset_restores_mixed_marker_endings(editor):
    connect_paragraph_service(editor)
    p = editor.page
    marker = "<!-- 쪽결 정렬=가운데 -->"
    text = f"제목\r\n\r{marker}\n날짜\r\n작성자"
    upload(p, "#file", "원문.md", text.encode())
    view = text.replace("\r\n", "\n").replace("\r", "\n")
    expect(editor.source).to_have_value(view)
    select_text(editor.source, view.index("날짜"), view.index("날짜"))
    align = p.locator("#paragraph-align")
    expect(align).to_have_value("center")
    align.select_option("")
    expect(editor.source).to_have_value(view.replace(marker + "\n", ""))
    editor.source.press("ControlOrMeta+z")
    expect(editor.source).to_have_value(view)
    editor.source.press("ControlOrMeta+Shift+z")
    expect(editor.source).to_have_value(view.replace(marker + "\n", ""))
    editor.source.press("ControlOrMeta+z")
    expect(editor.source).to_have_value(view)
    with p.expect_download() as saved:
        p.locator("#save").click()
    assert saved.value.path().read_bytes() == text.encode()


def test_pending_alignment_leaves_new_settings_focus_and_text_intact(desk):
    desk.ready()
    p = desk.page
    select_text(desk.source, 0, 0)
    pending = hold_alignment_edits(p)
    chooser = p.locator("#paragraph-align")
    chooser.focus()
    with p.expect_request(lambda r: r.url.endswith("/api/paragraph/align") and "align" in r.post_data_json):
        chooser.select_option("right")
    open_settings(p)
    size = p.locator("#body_pt")
    size.focus()
    with p.expect_request_finished(lambda r: r == pending[0].request):
        pending[0].fulfill(json={"source": "<!-- 쪽결 정렬=오른쪽 -->\n" + desk.source.input_value(),
                                 "align": "right", "supported": True, "message": ""})
    expect(size).to_be_focused()
    expect(size).to_have_value("11")


def test_code_selection_shows_reason_and_same_alignment_keeps_document_clean(editor):
    connect_paragraph_service(editor)
    p = editor.page
    marker = "<!-- 쪽결 정렬=오른쪽 -->"
    text = f"{marker}\n작성자\n\n~~~\n{marker}\n~~~"
    upload(p, "#file", "예시.md", text.encode())
    expect(editor.source).to_have_value(text)
    align = p.locator("#paragraph-align")
    select_text(editor.source, 0, 0)
    expect(align).to_have_value("right")
    with p.expect_response("**/api/paragraph/align"):
        align.select_option("right")
    expect(align).to_have_value("right")
    assert not p.evaluate("manuscriptDirty")
    select_text(editor.source, text.rindex(marker), text.rindex(marker))
    expect(p.locator("#paragraph-align-message")).not_to_be_empty()
    align.select_option("left")
    expect(align).to_have_value("")
    expect(editor.source).to_have_value(text)
    assert not p.evaluate("manuscriptDirty")


def test_each_alignment_command_is_one_native_undo_step(editor):
    connect_paragraph_service(editor)
    src = editor.source
    src.fill("작성자")
    select_text(src, 0, 0)
    src.press("A")
    before = "A작성자"
    expect(src).to_have_value(before)
    chooser = editor.page.locator("#paragraph-align")
    states = [before]
    for align, label in (("right", "오른쪽"), ("center", "가운데"), ("", None)):
        chooser.select_option(align)
        after = f"<!-- 쪽결 정렬={label} -->\n{before}" if label else before
        expect(src).to_have_value(after)
        states.append(after)
    for previous in reversed(states[:-1]):
        src.press("ControlOrMeta+z")
        expect(src).to_have_value(previous)
