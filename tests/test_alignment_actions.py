"""A user's save or proof request follows their pending paragraph edit."""
import pytest
from playwright.sync_api import expect
from test_editor import browser, desk
from test_jjok_editor import editor, entries, server_url

MARK = "<!-- 쪽결 정렬=오른쪽 -->"
TEXT = "작성자\n\n보고서"
URL = "**/api/paragraph/align"


def response(route):
    request = route.request.post_data_json
    value = request.get("align")
    lines = request["source"].split("\n")
    if value:
        lines.insert(request["first_line"], MARK)
    route.fulfill(json={"supported": True, "align": value, "message": "", "source": "\n".join(lines)})


@pytest.mark.parametrize("button", ["save", "refresh", "export"])
def test_output_waits_for_the_selected_alignment(desk, button):
    desk.ready()
    p = desk.page
    desk.source.fill(TEXT)
    desk.source.evaluate("e => e.setSelectionRange(0,0)")
    held = []
    def route(request):
        if "align" in request.request.post_data_json:
            held.append(request)
        else:
            response(request)
    p.route(URL, route)
    with p.expect_request(lambda r: r.url.endswith("/api/paragraph/align") and "align" in r.post_data_json):
        p.locator("#paragraph-align").select_option("right")
    if button == "save":
        with p.expect_download() as download:
            p.locator("#save").click()
            response(held[0])
        assert download.value.path().read_text() == MARK + "\n" + TEXT
    else:
        with p.expect_request("**/api/render") as proof:
            p.locator("#" + button).click()
            response(held[0])
        assert proof.value.post_data_json["source"] == MARK + "\n" + TEXT
    expect(desk.source).to_have_value(MARK + "\n" + TEXT)


def test_obsolete_cursor_queries_leave_capacity_for_explicit_edit(desk):
    desk.ready()
    p = desk.page
    held = []
    p.route(URL, lambda r: response(r) if "align" in r.request.post_data_json else held.append(r))
    with p.expect_request(URL):
        desk.source.fill(TEXT)
        desk.source.evaluate("e => e.setSelectionRange(0,0)")
    desk.source.evaluate("e => e.setSelectionRange(e.value.length,e.value.length)")
    p.wait_for_timeout(400)  # Let the second selection's debounce expire with the first query still held.
    assert len(held) == 1
    p.locator("#paragraph-align").select_option("right")
    with p.expect_request(lambda r: r.url.endswith("/api/paragraph/align") and "align" in r.post_data_json) as edit:
        response(held.pop(0))
    assert edit.value.post_data_json["align"] == "right"
    expect(desk.source).to_have_value("작성자\n\n" + MARK + "\n보고서")


def test_archive_waits_for_the_selected_alignment(editor):
    p = editor.page
    p.locator("#include-proof").uncheck()
    editor.source.fill(TEXT)
    editor.source.evaluate("e => e.setSelectionRange(0,0)")
    held = []
    p.route(URL, lambda r: held.append(r) if "align" in r.request.post_data_json else response(r))
    with p.expect_request(lambda r: r.url.endswith("/api/paragraph/align") and "align" in r.post_data_json):
        p.locator("#paragraph-align").select_option("right")
    with p.expect_download() as saved:
        p.locator("#save-document").click()
        response(held[0])
    assert entries(saved.value.path().read_bytes())["manuscript.md"].decode() == MARK + "\n" + TEXT
