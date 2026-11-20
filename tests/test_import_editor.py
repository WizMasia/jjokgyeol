"""Open real manuscripts through the editor and exercise superseded source reads."""
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect

from settings_ui import open_settings, close_settings
from pypdf import PdfReader

from test_editor import browser, desk
from test_slice import server_url


SAMPLES = Path(__file__).parent / "fixtures/imports"


def select_import(page, file):
    with page.expect_file_chooser() as picker:
        page.get_by_role("button", name="원고 가져오기", exact=True).click()
    accepted = picker.value.element.get_attribute("accept").split(",")
    assert {".txt", ".html", ".docx", ".hwpx", ".hwp"}.issubset(accepted)
    picker.value.set_files(file)


def saved_manuscript(page):
    with page.expect_download() as saved:
        page.locator("#save").click()
    return saved.value.suggested_filename, saved.value.path().read_bytes()


@pytest.mark.parametrize("name,phrase", [
    ("field.txt", "측정을 마쳤다"), ("field.html", "자료 보기"),
    ("lists.docx", "Some nested lists"), ("ParaHead.hwpx", "개요2-1-1"), ("lists.hwp", "3-2-1"),
])
def test_import_picker_save_and_render(browser, server_url, name, phrase):
    sample = SAMPLES / name
    original = sample.read_bytes()
    with browser.new_context() as context:
        page = context.new_page()
        page.goto(os.environ.get("JJOKGYEOL_IMPORT_URL", server_url))
        page.wait_for_function("document.querySelector('#status').textContent === '최신 버전'", timeout=45000)
        open_settings(page)
        page.locator("#body_pt").fill("12")
        close_settings(page)
        with page.expect_response(lambda r: "/api/import?" in r.url) as imported:
            select_import(page, sample)
        response = imported.value
        assert response.ok, response.text()
        assert parse_qs(urlsplit(response.url).query) == {"name": [name]}
        assert response.request.method == "POST"
        assert response.request.header_value("content-type") == "application/octet-stream"
        assert response.request.header_value("content-length") == str(len(original))
        # Chromium evicts no-store responses from its debugging cache.
        expected = page.request.post(response.url, data=original)
        assert expected.ok, expected.text()
        data = expected.json()
        expect(page.locator("#source")).to_have_value(data["markdown"])
        assert phrase in data["markdown"]
        assert page.locator("#import-warnings li").all_text_contents() == data["warnings"]
        open_settings(page)
        expect(page.locator("#body_pt")).to_have_value("12")
        close_settings(page)
        assert saved_manuscript(page) == (sample.stem + ".md", data["markdown"].encode())
        with page.expect_download(timeout=45000) as pdf:
            page.locator("#export").click()
        assert pdf.value.suggested_filename == sample.stem + ".pdf"
        text = "".join(p.extract_text() for p in PdfReader(pdf.value.path()).pages)
        assert "".join(phrase.split()) in "".join(text.split())
        assert sample.read_bytes() == original


@pytest.mark.parametrize("reply", [
    {"status": 422, "json": {"message": "손상된 원고입니다."}},
    {"json": {"markdown": "부분 결과", "warnings": [None]}},
])
def test_import_failure_preserves_edited_file_and_preview(desk, reply):
    desk.ready()
    page = desk.page
    old_pdf = desk.frame.get_attribute("src")
    original = b"\xef\xbb\xbfold\r\nline"
    page.locator("#file").set_input_files({"name": "교정.md", "mimeType": "text/plain", "buffer": original})
    expect(desk.source).to_have_value("old\nline")
    desk.source.fill("edited\nline")
    page.route("**/api/import?*", lambda route: route.fulfill(**reply))
    select_import(page, {"name": "broken.hwp", "mimeType": "", "buffer": b"broken"})
    expect(page.locator("#error")).to_contain_text(reply["json"].get("message", "가져오기"))
    expect(desk.source).to_have_value("edited\nline")
    assert saved_manuscript(page) == ("교정.md", b"\xef\xbb\xbfedited\r\nline")
    assert desk.frame.get_attribute("src") == old_pdf


@pytest.mark.parametrize("replacement", ["edit", "open", "import"])
def test_stale_import_cannot_change_source_or_warnings(desk, replacement):
    desk.ready()
    page = desk.page
    held = []

    def respond(route):
        if held:
            route.fulfill(json={"markdown": "new", "warnings": ["표 1", "<i>그림 2</i>"]})
        else:
            held.append(route)

    page.route("**/api/import?*", respond)
    file = {"name": "달 & 별.TXT", "mimeType": "text/plain", "buffer": b"raw"}
    with page.expect_request("**/api/import?*"):
        select_import(page, file)
    expect(page.locator("#save")).to_be_disabled()
    if replacement == "import":
        with page.expect_request("**/api/import?*"):
            select_import(page, file)
        expected_name = "달 & 별.md"
        warnings = ["표 1", "<i>그림 2</i>"]
    elif replacement == "open":
        page.locator("#file").set_input_files({"name": "new.md", "mimeType": "text/markdown", "buffer": b"new"})
        expected_name, warnings = "new.md", []
    else:
        desk.source.fill("new")
        expected_name, warnings = "비교 원고.md", []
    expect(desk.source).to_have_value("new")
    with page.expect_request_finished(lambda req: req == held[0].request):
        if replacement == "open":
            held[0].fulfill(status=422, json={"message": "오래된 오류"})
        else:
            held[0].fulfill(json={"markdown": "stale", "warnings": ["오래된 경고"]})
    page.evaluate("() => new Promise(requestAnimationFrame)")
    assert saved_manuscript(page) == (expected_name, b"new")
    assert page.locator("#import-warnings li").all_text_contents() == warnings
    expect(page.locator("#error")).to_be_empty()


def test_corrupt_import_keeps_previous_warnings_and_draft(browser, server_url):
    with browser.new_context() as context:
        page = context.new_page()
        page.goto(os.environ.get("JJOKGYEOL_IMPORT_URL", server_url))
        select_import(page, SAMPLES / "field.html")
        expect(page.locator("#filename")).to_have_text("field.md")
        notice = page.locator("#import-notice")
        expect(notice).to_be_visible()
        warnings = notice.locator("li").all_text_contents()
        assert any("그림" in warning for warning in warnings)
        page.locator("#source").fill("교정 중")
        with page.expect_response(lambda r: "/api/import?" in r.url) as attempt:
            select_import(page, {"name": "손상.docx", "mimeType": "", "buffer": b"broken"})
        assert attempt.value.status == 422
        expect(page.get_by_role("alert")).not_to_be_empty()
        assert saved_manuscript(page) == ("field.md", "교정 중".encode())
        assert notice.locator("li").all_text_contents() == warnings
        page.locator("#file").set_input_files({"name": "교정.md", "mimeType": "text/markdown", "buffer": b"new"})
        expect(page.locator("#source")).to_have_value("new")
        expect(notice).to_be_hidden()
