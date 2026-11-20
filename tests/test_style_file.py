"""A publication style can be saved separately and opened without numeric drift."""
import json

import httpx
import pytest
from playwright.sync_api import expect, sync_playwright

from app.server import app
from test_editor import DEFAULTS, ORIGIN, browser, desk
from test_slice import server_url


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_style_file_round_trip():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://local") as client:
        encoded = await client.post("/api/style/export", json={
            "ko_tracking_pct": "-7.245", "space_pct": ["70", "129.995"],
            "body_pt": "11.125", "rest_indent_mm": "6",
        })
        assert encoded.status_code == 200
        stored = encoded.json()
        assert stored["ko_tracking_per_10000"] == -725
        assert stored["space_per_10000"] == [7000, 13000]
        assert stored["body_sp"] == 729088
        for _ in range(3):
            opened = await client.post("/api/style/open", json=stored)
            assert opened.status_code == 200
            assert opened.json()["rest_indent_mm"] == "6"
            assert opened.json()["page_mm"] == ["148", "210"]
            assert (await client.post("/api/style/export", json=opened.json())).json() == stored
        assert (await client.post("/api/style/open", json={**stored, "body_sp": 0})).status_code == 422

        # Individually shortening these four metric values can erase the last
        # scaled point of line width when the displayed inputs are summed.
        narrow = dict(stored, page_sp=[21416810, stored["page_sp"][1]],
                      margins_sp=[0, 7713205, 0, 8071449], rest_indent_sp=5632155)
        reopened = await client.post("/api/style/open", json=narrow)
        assert reopened.status_code == 200
        assert (await client.post("/api/style/export", json=reopened.json())).json() == narrow


@pytest.mark.anyio
async def test_sparse_typography_through_style_file_api():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://local") as client:
        old = (await client.post("/api/style/export", json={})).json()
        opened = (await client.post("/api/style/open", json=old)).json()
        assert "text_styles" not in opened and "ko_tracking_bounds_pct" not in opened
        assert (await client.post("/api/style/export", json=opened)).json() == old
        edited = {**opened, "text_styles": {"heading3": {"color": "#17365D"}, "note": {"font_pt": "8.5"}},
                  "ko_tracking_bounds_pct": ["-7", "0"]}
        saved = await client.post("/api/style/export", json=edited)
        assert saved.status_code == 200, saved.text
        assert saved.json() == {**old, "text_styles": {"heading3": {"color": "#17365D"}, "note": {"font_sp": 557056}},
                                "ko_tracking_bounds_per_10000": [-700, 0]}
        assert (await client.post("/api/style/open", json=saved.json())).json() == edited


@pytest.mark.anyio
async def test_running_areas_round_trip_selected_fields_through_style_api():
    rules = [
        {"path": [], "role": "footer", "values": {"visible": True, "center": "{현재쪽}", "font_pt": "9.25"}},
        {"path": ["표지"], "role": "footer", "values": {"visible": False, "center": ""}},
        {"path": ["부록"], "role": "header", "values": {"left": "{{현재쪽}}"}},
    ]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://local") as client:
        encoded = await client.post("/api/style/export", json={"section_rules": rules})
        assert encoded.status_code == 200, encoded.text
        stored = encoded.json()
        assert stored["section_rules"] == [
            {**rules[0], "values": {"visible": True, "center": "{현재쪽}", "font_sp": 606208}},
            rules[1], rules[2],
        ]
        reopened = await client.post("/api/style/open", json=stored)
        assert reopened.status_code == 200, reopened.text
        assert reopened.json()["section_rules"] == rules
        assert (await client.post("/api/style/export", json=reopened.json())).json() == stored

        rules[2]["values"]["source_group"] = "참고 원본 위치"
        invalid = await client.post("/api/style/export", json={"section_rules": rules})
        assert invalid.status_code == 422
        assert invalid.json()["field"] == "section_rules[2].values.source_group"


@pytest.mark.anyio
async def test_duplex_style_api_keeps_faces_and_sparse_fields():
    rules = [
        {"path": [], "role": "footer", "values": {"visible": True, "center": "{현재쪽}"}},
        {"path": [], "role": "footer", "face": "odd", "values": {"center": "", "right": "{현재쪽}"}},
        {"path": [], "role": "footer", "face": "even", "values": {"center": "", "left": "{현재쪽}"}},
        {"path": ["부록"], "role": "footer", "face": "first", "values": {"visible": False}},
    ]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://local") as client:
        exported = await client.post("/api/style/export", json={"section_rules": rules})
        assert exported.status_code == 200, exported.text
        stored = exported.json()
        assert stored["section_rules"] == rules
        opened = await client.post("/api/style/open", json=stored)
        assert opened.status_code == 200, opened.text
        assert opened.json()["section_rules"] == rules
        assert (await client.post("/api/style/export", json=opened.json())).json() == stored


def test_style_file_in_browser(server_url, tmp_path):
    with sync_playwright() as playwright:
        with playwright.chromium.launch() as browser:
            page = browser.new_page()
            page.goto(server_url)
            page.locator("summary").click()
            tracking = page.locator("#ko_tracking_pct")
            manuscript = "# 내 원고\n\n본문 [^주]\n"
            editor = page.get_by_role("textbox", name="마크다운 원고")
            editor.fill(manuscript)
            tracking.fill("-7.245")
            with page.expect_download() as download:
                page.get_by_role("button", name="양식 저장").click()
            file = tmp_path / "양식.json"
            download.value.save_as(file)
            tracking.fill("0")
            page.locator("#style-file").set_input_files(file)
            expect(tracking).to_have_value("-7.25")
            page.locator("#body_pt").fill("12.500")
            before = page.evaluate("styleForm.get()")
            page.locator("#style-file").set_input_files({
                "name": "invalid.json", "mimeType": "application/json", "buffer": b"{}",
            })
            expect(page.get_by_role("alert")).not_to_be_empty()
            expect(tracking).to_have_value("-7.25")
            assert page.evaluate("styleForm.get()") == before
            expect(editor).to_have_value(manuscript)
            page.locator("#style-file").set_input_files(file)
            expect(page.locator("#body_pt")).to_have_value("11")
            expect(page.get_by_role("alert")).to_be_empty()
            with page.expect_download() as recovered:
                page.get_by_role("button", name="양식 저장").click()
            assert json.loads(recovered.value.path().read_bytes()) == json.loads(file.read_bytes())
            expect(editor).to_have_value(manuscript)


def hold_style(desk, action):
    routes = []
    url = ORIGIN + "/api/style/" + action
    def capture(route):
        routes.append(route)
        desk.page.evaluate("window.styleRequestHeld = true")

    desk.page.route(url, capture)
    with desk.page.expect_request(url):
        if action == "export":
            desk.page.locator("#save-style").click()
        else:
            desk.page.locator("#style-file").set_input_files({
                "name": "양식.json", "mimeType": "application/json", "buffer": b"{}",
            })
    desk.page.wait_for_function("window.styleRequestHeld")
    return routes[0]


def test_save_finishes_after_edit(desk):
    desk.ready()
    desk.page.locator("summary").click()
    desk.page.locator("#body_pt").fill("16")
    save = hold_style(desk, "export")
    desk.source.fill("# 계속 편집")
    desk.page.locator("#body_pt").fill("13")
    assert save.request.post_data_json["body_pt"] == "16"
    with desk.page.expect_download(timeout=1500) as saved:
        save.fulfill(json={"body_sp": 16 * 65536})
    assert json.loads(saved.value.path().read_bytes()) == {"body_sp": 16 * 65536}
    expect(desk.source).to_have_value("# 계속 편집")
    expect(desk.page.locator("#body_pt")).to_have_value("13")


def test_style_open_blocks_previous_pdf(desk):
    desk.ready()
    desk.page.locator("summary").click()
    downloads = []
    desk.page.on("download", lambda item: downloads.append(item))
    old_url = desk.frame.get_attribute("src")
    desk.source.fill("# 새 원고")
    desk.submit(desk.export)
    opening = hold_style(desk, "open")
    desk.reply(0, desk.pdf)
    assert downloads == []
    assert desk.frame.get_attribute("src") == old_url
    expect(desk.export).to_be_disabled()
    expect(desk.refresh).to_be_disabled()
    opening.fulfill(json={**DEFAULTS, "body_pt": "16"})
    expect(desk.export).to_be_enabled()
    desk.submit(desk.export)
    assert desk.requests[-1]["style"]["body_pt"] == "16"
    with desk.page.expect_download():
        desk.reply(1, desk.pdf)


@pytest.mark.parametrize("first", ["source", "style"])
def test_overlapping_file_opens(desk, first):
    desk.ready()
    desk.page.locator("summary").click()
    desk.page.evaluate("""() => {
        const read = File.prototype.arrayBuffer;
        File.prototype.arrayBuffer = async function() {
            await new Promise(resolve => window.finishSource = resolve);
            return read.call(this);
        };
    }""")
    desk.page.locator("#file").set_input_files({
        "name": "다음.md", "mimeType": "text/markdown", "buffer": "# 다음".encode(),
    })
    style = hold_style(desk, "open")

    def finish_source():
        desk.page.evaluate("window.finishSource()")
        expect(desk.source).to_have_value("# 다음")

    def finish_style():
        style.fulfill(json={**DEFAULTS, "body_pt": "16"})
        expect(desk.page.locator("#body_pt")).to_have_value("16", timeout=1500)

    first_done, last_done = (finish_source, finish_style) if first == "source" else (finish_style, finish_source)
    first_done()
    expect(desk.export).to_be_disabled(timeout=1000)
    last_done()
    expect(desk.export).to_be_enabled()
    desk.submit(desk.refresh)
    assert desk.requests[-1] == {"source": "# 다음", "style": {**DEFAULTS, "body_pt": "16"}}
    desk.reply(0, desk.pdf)


def test_failed_style_open_restores_controls(desk):
    desk.ready()
    desk.page.locator("summary").click()
    opening = hold_style(desk, "open")
    expect(desk.page.locator("#save-style")).to_be_disabled(timeout=1000)
    opening.fulfill(status=422, json={"message": "양식 오류"})
    expect(desk.page.get_by_role("alert")).to_have_text("양식 오류")
    for control in [desk.export, desk.refresh, desk.page.locator("#save-style"), desk.page.locator("#save")]:
        expect(control).to_be_enabled()
    with desk.page.expect_download():
        desk.export.click()


def test_list_style_file_recovery(browser, server_url):
    with browser.new_context() as context:
        page = context.new_page()
        page.goto(server_url)
        viewer = page.locator("#preview")
        page.wait_for_function("document.querySelector('#preview').src.startsWith('blob:')")
        page.locator("summary").click()
        preset = page.get_by_label("목록 양식", exact=True)
        expect(preset).to_be_visible(timeout=1500)
        save = page.get_by_role("button", name="양식 저장")
        upload = page.locator("#style-file")
        with page.expect_download() as legacy:
            save.click()
        old_file = legacy.value.path()
        assert "list_levels" not in json.loads(old_file.read_bytes())
        preset.select_option(label="보고서형")
        second = page.get_by_role("group", name="2단계", exact=True)
        marker = second.get_by_label("표지 표현식")
        literal = " {{붙임}} ({한글}) "
        marker.fill(literal)
        with page.expect_download() as custom:
            save.click()
        custom_file = custom.value.path()
        stored = json.loads(custom_file.read_bytes())
        assert stored["list_levels"][1]["expr"] == literal
        previous = viewer.get_attribute("src")
        marker.fill("{없는표지}")
        downloads = []
        page.on("download", lambda item: downloads.append(item))
        for button in [save, page.get_by_role("button", name="PDF 다운로드")]:
            with page.expect_response(lambda response: response.request.method == "POST") as rejected:
                button.click()
            assert rejected.value.status == 422
            expect(page.get_by_role("alert")).to_contain_text("2단계")
            expect(marker).to_have_value("{없는표지}")
            assert viewer.get_attribute("src") == previous
        assert downloads == []
        assert json.loads(custom_file.read_bytes()) == stored
        upload.set_input_files(custom_file)
        expect(marker).to_have_value(literal)
        with page.expect_download() as reopened:
            save.click()
        assert json.loads(reopened.value.path().read_bytes()) == stored
        upload.set_input_files(old_file)
        expect(preset).to_have_value("")
        with page.expect_download() as restored:
            save.click()
        assert json.loads(restored.value.path().read_bytes()) == json.loads(old_file.read_bytes())
