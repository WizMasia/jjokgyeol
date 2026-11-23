"""Paragraph spacing through the shared style, manuscript and proof controls."""
import json

from playwright.sync_api import expect

from settings_ui import close_settings, open_settings
from test_editor import browser
from test_jjok_editor import editor, entries, save_document, server_url, upload
from test_paragraph_alignment_editor import connect_paragraph_service, select_text


def mode(scope, side):
    return scope.locator(f'[data-spacing-mode="space_{side}_pt"]')


def amount(scope, side):
    return scope.locator(f'[data-spacing-value="space_{side}_pt"]')


def test_role_and_list_settings_roundtrip_and_independent_reset(editor):
    p = editor.page
    open_settings(p)
    p.locator("#style summary").click()
    roles = p.locator("#text-roles")
    target = roles.locator("#text-role")
    mode(roles, "before").select_option("auto")
    for level in range(1, 7):
        target.select_option(f"heading{level}")
        mode(roles, "after").select_option("manual")
        amount(roles, "after").fill("0" if level == 1 else "4.0000152587890625")
    for excluded in ("gloss", "note"):
        target.select_option(excluded)
        expect(mode(roles, "before")).to_be_hidden()
    target.select_option("heading2")
    mode(roles, "before").select_option("auto")
    mode(roles, "after").select_option("")
    row = p.locator("#list-levels fieldset").first
    mode(row, "before").select_option("manual")
    amount(row, "before").fill("6.25")
    mode(row, "after").select_option("auto")
    with p.expect_download() as saved:
        p.locator("#save-style").click()
    style_bytes = saved.value.path().read_bytes()
    stored = json.loads(style_bytes)
    assert stored["text_styles"]["body"]["space_before_sp"] == "auto"
    assert stored["text_styles"]["heading1"]["space_after_sp"] == 0
    assert stored["text_styles"]["heading2"]["space_before_sp"] == "auto"
    assert "space_after_sp" not in stored["text_styles"]["heading2"]
    assert stored["text_styles"]["heading6"]["space_after_sp"] == 4 * 65536 + 1
    assert stored["list_levels"][0]["space_before_sp"] == 409600
    assert stored["list_levels"][0]["space_after_sp"] == "auto"
    mode(row, "after").select_option("")
    upload(p, "#style-file", "간격.json", style_bytes)
    expect(mode(row, "after")).to_have_value("auto")
    target.select_option("heading6")
    expect(amount(roles, "after")).to_have_value("4.0000152587890625")


def test_individual_edits_only_send_one_side_and_preserve_undo(editor):
    p = editor.page
    connect_paragraph_service(editor)
    editor.source.fill("첫 문단\n\n마지막 문단")
    select_text(editor.source, 1, 1)
    box = p.locator("#paragraph-spacing")
    before, after = mode(box, "before"), mode(box, "after")
    with p.expect_request(lambda r: r.url.endswith("/paragraph/align") and r.post_data_json.get("spacing") is True):
        expect(before).to_be_enabled()
        # Wait for the debounced selection query before editing.
        p.wait_for_function("document.querySelector('#paragraph-align').selectedIndex >= 0")
    before.select_option("auto")
    expect(before).to_have_value("auto")
    p.wait_for_function("document.querySelector('#source').value.includes('<!--')")
    after.select_option("manual")
    amount(box, "after").fill("0")
    with p.expect_request(lambda r: r.url.endswith("/paragraph/align") and "space_after_pt" in r.post_data_json) as write:
        amount(box, "after").press("Enter")
    assert write.value.post_data_json["space_after_pt"] == 0
    assert "space_before_pt" not in write.value.post_data_json
    expect(editor.source).to_be_focused()
    before_clear = editor.source.input_value()
    with p.expect_request(lambda r: r.url.endswith("/paragraph/align") and "space_before_pt" in r.post_data_json) as clear:
        before.select_option("")
    assert clear.value.post_data_json["space_before_pt"] is None
    assert "space_after_pt" not in clear.value.post_data_json
    expect(editor.source).not_to_have_value(before_clear)
    expect(after).to_have_value("manual")
    expect(amount(box, "after")).to_have_value("0")
    editor.source.press("ControlOrMeta+z")
    expect(editor.source).to_have_value(before_clear)


def proof_reply(editor, index, receipt, decision="model"):
    parts = []
    for key in (("rule",) if decision == "rule" else ("rule", "model")):
        parts.append(f'--p\r\nContent-Disposition: form-data; name="{key}"; filename="{key}.pdf"\r\n'
                     'Content-Type: application/pdf\r\n\r\n'.encode() + editor.pdf + b"\r\n")
    parts.append(b'--p\r\nContent-Disposition: form-data; name="spacing"\r\n\r\n'
                 + json.dumps(receipt).encode() + b"\r\n--p--\r\n")
    editor.pending[index].fulfill(headers={"Content-Type": "multipart/form-data; boundary=p",
                                           "X-Jjokgyeol-Decision": decision}, body=b"".join(parts))


def test_display_tracks_proof_selection_revision_and_archive(editor):
    p = editor.page
    connect_paragraph_service(editor)
    source = "처음\n\n중간\n계속\n\n마지막"
    editor.source.fill(source)
    select_text(editor.source, source.index("계속"), source.index("계속"))
    editor.submit(editor.refresh)
    assert editor.pending[0].request.headers.get("x-jjokgyeol-spacing") == "1"
    def rows(gap, origin):
        return [dict(before_line=0, after_line=2, gap_sp=gap * 65536, source=origin),
                dict(before_line=2, after_line=5, gap_sp=0, source="manual")]
    receipt = {"model": rows(12, "ai"), "rule": rows(4, "rule")}
    proof_reply(editor, 0, receipt)
    details = p.locator("#paragraph-spacing-applied")
    expect(details).to_contain_text("위 12 pt · AI")
    expect(details).to_contain_text("아래 0 pt · 직접")
    p.locator("#proof-choice").select_option("rule")
    expect(details).to_contain_text("위 4 pt · 규칙 대체")
    select_text(editor.source, 0, 0)
    expect(details).to_contain_text("아래 4 pt")
    open_settings(p)
    p.locator("#body_pt").fill("12")
    close_settings(p)
    expect(details).to_contain_text("이전")
    expect(details).to_contain_text("아래 4 pt")
    editor.submit(editor.refresh)
    proof_reply(editor, 1, {"rule": rows(8, "style")}, decision="rule")
    expect(details).to_contain_text("아래 8 pt · 양식")
    saved = save_document(p)
    assert "spacing.json" not in entries(saved)
    editor.source.fill("줄이 다른 원고")
    expect(details).not_to_contain_text("8 pt")
    p.once("dialog", lambda dialog: dialog.accept())
    upload(p, "#document-file", "다시.jjok", saved)
    expect(editor.source).to_have_value(source)
    expect(details).to_contain_text("미리보기 갱신 후 확인")
    expect(details).not_to_contain_text("8 pt")


def test_unfinished_numeric_paragraph_edit_cannot_be_saved(editor):
    p = editor.page
    connect_paragraph_service(editor)
    editor.source.fill("한 문단")
    select_text(editor.source, 0, 0)
    box = p.locator("#paragraph-spacing")
    mode(box, "before").select_option("manual")
    numeric = amount(box, "before")
    downloads = []
    p.on("download", lambda file: downloads.append(file))
    for raw in ("", "1e", "72.00000000000000001"):
        numeric.fill(raw)
        p.locator("#save").click()
        expect(numeric).to_have_attribute("aria-invalid", "true")
        expect(editor.source).to_have_value("한 문단")
        expect(numeric).to_have_value(raw)
        assert not downloads
    numeric.fill("6.25")
    with p.expect_download() as saved:
        p.locator("#save").click()
    assert "space_before_sp=409600" in saved.value.path().read_text()


def test_manual_value_keeps_the_exact_side_of_a_half_sp(editor):
    connect_paragraph_service(editor)
    editor.source.fill("원고")
    select_text(editor.source, 0, 0)
    box = editor.page.locator("#paragraph-spacing")
    mode(box, "before").select_option("manual")
    value = amount(box, "before")
    for raw, sp in (("18.00000762939453125", 1179649), ("18.0000076293945312499", 1179648)):
        value.fill(raw)
        value.press("Enter")
        expect(editor.source).to_have_value(f"<!-- 쪽결 space_before_sp={sp} -->\n원고")
        expect(value).to_have_value(str(sp / 65536).removesuffix(".0"))


def test_second_side_waits_for_first_edit_and_late_selection_reply_is_ignored(editor):
    p = editor.page
    connect_paragraph_service(editor)
    source = "처음\n\n다음"
    editor.source.fill(source)
    select_text(editor.source, 1, 1)
    box = p.locator("#paragraph-spacing")
    before, after = mode(box, "before"), mode(box, "after")
    expect(before).to_be_enabled()
    held = []
    def delay(route):
        if "space_before_pt" in route.request.post_data_json:
            held.append(route)
        else:
            route.fallback()
    p.route("**/api/paragraph/align", delay)
    with p.expect_request(lambda r: r.url.endswith("/paragraph/align") and "space_before_pt" in r.post_data_json):
        before.select_option("auto")
    expect(after).to_be_disabled()
    select_text(editor.source, len(source), len(source))
    held[0].fulfill(json={"source": "오래된 응답", "start_line": 0, "align": None,
                          "space_before_pt": "auto", "space_after_pt": 72, "supported": True, "message": ""})
    expect(before).to_be_enabled()
    expect(before).to_have_value("")
    expect(after).to_have_value("")
    expect(editor.source).to_have_value(source)


def test_leaving_amount_for_pdf_checkbox_commits_and_keeps_focus(editor):
    page = editor.page
    connect_paragraph_service(editor)
    editor.source.fill("가을\n\n겨울")
    select_text(editor.source, 0, 0)
    panel = page.locator("#paragraph-spacing")
    mode(panel, "after").select_option("manual")
    amount(panel, "after").fill("6")
    page.locator("#include-proof").click()
    expect(editor.source).to_have_value("<!-- 쪽결 space_after_sp=393216 -->\n가을\n\n겨울")
    expect(page.locator("#include-proof")).to_be_focused()


def test_role_spacing_error_marks_the_invalid_amount(editor):
    page = editor.page
    open_settings(page)
    page.locator("#style summary").click()
    panel = page.locator("#text-roles")
    panel.locator("#text-role").select_option("heading1")
    mode(panel, "before").select_option("manual")
    amount(panel, "before").fill("73")
    page.locator("#save-style").click()
    expect(amount(panel, "before")).to_have_attribute("aria-invalid", "true")
    expect(page.locator("#typography-error")).to_contain_text("72")


def test_settings_waits_for_blurred_paragraph_amount(editor):
    page = editor.page
    connect_paragraph_service(editor)
    editor.source.fill("날짜\n\n본문")
    select_text(editor.source, 0, 0)
    panel = page.locator("#paragraph-spacing")
    mode(panel, "after").select_option("manual")
    amount(panel, "after").fill("6")
    held = []
    def hold(route):
        if "space_after_pt" in route.request.post_data_json:
            held.append(route)
        else:
            route.fallback()
    page.route("**/api/paragraph/align", hold)
    with page.expect_request(lambda r: r.url.endswith("/paragraph/align") and "space_after_pt" in r.post_data_json):
        page.locator("#open-settings").click()
    held[0].fallback()
    expect(editor.source).to_have_value("<!-- 쪽결 space_after_sp=393216 -->\n날짜\n\n본문")
    expect(page.locator("#settings-dialog")).to_be_visible()
