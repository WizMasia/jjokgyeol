"""Exercise the editor in Chromium with a local, controllable render endpoint."""

import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
from app import server, style
from app.font_assets import LOCKED_ASSET_PATHS
from playwright.sync_api import expect, sync_playwright

from settings_ui import close_settings, open_settings
from test_slice import server_url


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app/static"
SAMPLE = "# 첫 교정\n\n종이와 Ink.\n"
ORIGIN = "http://127.0.0.1"
NEW_TEXT = {f"heading{i}": {"font_pt": value} for i, value in enumerate((
    "17.600006103515625", "16.5", "15.399993896484375",
    "14.3000030517578125", "13.1999969482421875", "12.100006103515625",
), 1)}
REPORT_LEVELS = [
    {"expr": mark, "indent_mm": indent, "gap_mm": "2"}
    for mark, indent in (("ㅁ", "0"), ("ㅇ", "2"), ("-", "2"))
]
DEFAULTS = {
    "list_levels": REPORT_LEVELS,
    "text_styles": NEW_TEXT, "paragraph_layout": "justified", "ko_tracking_bounds_pct": ["-15", "15"],
    "page_mm": ["148", "210"], "margins_mm": ["20", "18", "20", "18"],
    "first_indent_mm": "0", "rest_indent_mm": "0",
    "body_pt": "11", "leading_pt": "17", "body_font": "nanum-myeongjo",
    "heading_font": "nanum-gothic", "ko_tracking_pct": "0", "space_pct": ["70", "130"],
}


class Desk:
    def __init__(self, page):
        self.page = page
        self.pending = []
        self.requests = []
        self.pdf = (ROOT / "proofs/engine-comparison/LuaLaTeX.pdf").read_bytes()
        page.route("**/*", self.route)
        page.goto(ORIGIN, wait_until="domcontentloaded")
        self.source = page.get_by_role("textbox", name="마크다운 원고")
        self.refresh = page.get_by_role("button", name="미리보기 갱신", exact=True)
        self.export = page.get_by_role("button", name="PDF 다운로드", exact=True)
        self.frame = page.locator("#preview")

    def route(self, route):
        url = route.request.url
        if url.startswith(("chrome-extension://", "chrome://resources/")):
            route.continue_()
        elif url == ORIGIN + "/api/render":
            self.requests.append(route.request.post_data_json)
            if len(self.requests) == 1:
                route.fulfill(content_type="application/pdf", body=self.pdf)
            else:
                self.pending.append(route)
        elif url.startswith(ORIGIN + "/api/document-fonts/"):
            asset = LOCKED_ASSET_PATHS.get(url.rsplit("/", 1)[-1])
            if asset and asset.is_file():
                route.fulfill(path=asset, content_type="application/octet-stream")
            else:
                route.fulfill(status=404, body="")
        elif url == ORIGIN + "/sample.md":
            route.fulfill(content_type="text/plain; charset=utf-8", body=SAMPLE)
        elif url == ORIGIN + "/":
            route.fulfill(content_type="text/html; charset=utf-8", body=server.editor_html())
        else:
            if url.startswith(ORIGIN + "/static/"):
                relative = unquote(urlsplit(url).path).removeprefix("/static/")
                asset = (STATIC / relative).resolve()
                if asset.is_relative_to(STATIC) and asset.is_file():
                    route.fulfill(path=asset)
                    return
            route.fulfill(status=404, body="")

    def ready(self, expected_style=DEFAULTS):
        expect(self.source).to_have_value(SAMPLE)
        expect(self.frame).to_have_attribute("src", BLOB)
        assert self.frame.get_attribute("src").endswith("#navpanes=0&view=FitH")
        if self.page.evaluate("navigator.pdfViewerEnabled"):
            expect(self.frame).to_be_visible()
        else:
            expect(self.page.get_by_text("PDF 다운로드로 지면을 확인하세요.", exact=False)).to_be_visible()
        assert self.requests == [{"source": SAMPLE, "style": expected_style, "connection": {"provider": "rule"}}]

    def submit(self, button):
        button.scroll_into_view_if_needed()
        with self.page.expect_request(ORIGIN + "/api/render"):
            button.click()
        self.page.wait_for_function("() => document.readyState !== 'loading'")

    def reply(self, index, body, status=200):
        route = self.pending[index]
        with self.page.expect_request_finished(lambda r: r == route.request):
            if status == 200:
                route.fulfill(content_type="application/pdf", body=body)
            else:
                route.fulfill(status=status, json=body)
        # Let already queued fetch continuations run before checking the DOM.
        self.page.evaluate("() => new Promise(resolve => requestAnimationFrame(resolve))")


BLOB = re.compile(r"^blob:")


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        with playwright.chromium.launch(channel=os.environ.get("JJOKGYEOL_CHROMIUM_CHANNEL")) as chromium:
            yield chromium


@pytest.fixture
def desk(browser, request, monkeypatch):
    if hasattr(request, "param"):
        bootstrap = {**style.editor_bootstrap(), **request.param}
        monkeypatch.setattr(style, "editor_bootstrap", lambda: bootstrap)
    context = browser.new_context()
    page = context.new_page()
    page.set_default_timeout(4000)
    try:
        yield Desk(page)
    finally:
        context.close()


def test_export_dirty(desk):
    desk.ready()
    revised = "# 바뀐 제목\n\n글과 **emphasis**.\n"
    desk.source.fill(revised)
    open_settings(desk.page)
    desk.page.get_by_label("본문 크기").fill("16")
    close_settings(desk.page)
    desk.submit(desk.export)
    desk.refresh.click()
    assert len(desk.requests) == 2
    assert desk.requests[-1]["source"] == revised
    assert desk.requests[-1]["style"]["body_pt"] == "16"
    new_pdf = desk.pdf + b"\n% revision 2\n"
    with desk.page.expect_download() as download:
        desk.reply(0, new_pdf)
    preview = desk.frame.evaluate("async el => Array.from(new Uint8Array(await (await fetch(el.src)).arrayBuffer()))")
    actual = download.value.path().read_bytes()
    assert download.value.url == desk.frame.get_attribute("src")
    assert hashlib.sha256(actual).digest() == hashlib.sha256(bytes(preview)).digest()
    assert actual == new_pdf and len(desk.requests) == 2


@pytest.mark.parametrize("late_status", [200, 422])
def test_out_of_order(desk, late_status):
    desk.ready()
    downloads = []
    desk.page.on("download", lambda item: downloads.append(item))
    open_settings(desk.page)
    desk.page.locator("#style summary").click()
    tracking = desk.page.get_by_label("한글 자간")
    tracking.fill("-1.111")
    close_settings(desk.page)
    desk.source.fill("이전 교정")
    desk.submit(desk.export)
    open_settings(desk.page)
    tracking.fill("-2.2250")
    close_settings(desk.page)
    desk.source.fill("최종 교정")
    desk.submit(desk.refresh)
    desk.reply(1, desk.pdf + b"\n% current\n")
    current = desk.frame.get_attribute("src")
    expect(desk.page.get_by_role("status")).to_contain_text("최신")
    late = desk.pdf if late_status == 200 else {"field": "source", "message": "오래된 오류"}
    desk.reply(0, late, late_status)
    assert desk.frame.get_attribute("src") == current
    expect(desk.page.get_by_role("status")).to_contain_text("최신")
    expect(tracking).to_have_value("-2.2250")
    expect(desk.page.locator("#applied-ratios")).to_contain_text("한글 자간 -2.23%")
    expect(desk.page.get_by_role("alert")).to_be_empty()
    assert downloads == []


def test_failed_export(desk):
    desk.ready()
    downloads = []
    desk.page.on("download", lambda item: downloads.append(item))
    previous = desk.frame.get_attribute("src")
    desk.source.fill("# 미해결 각주[^a]")
    desk.submit(desk.export)
    desk.reply(0, {"code": "invalid", "field": "source", "message": "각주를 확인하세요."}, 422)
    expect(desk.page.get_by_role("alert")).to_contain_text("각주를 확인하세요.")
    expect(desk.page.get_by_role("status")).to_contain_text("이전 버전")
    assert desk.frame.get_attribute("src") == previous
    assert downloads == []
    desk.source.fill("# 복구")
    desk.submit(desk.export)
    with desk.page.expect_download():
        desk.reply(1, desk.pdf)
    expect(desk.page.get_by_role("status")).to_contain_text("최신")


def test_markdown_round_trip(desk):
    desk.ready()
    source = "# 원고\r\n\r\n  <b>글</b> & **강조**[^주]  \r\n[^주]: 한글  \r\n"
    data = b"\xef\xbb\xbf" + source.encode()
    upload = desk.page.locator("#file")
    upload.set_input_files({"name": "활자.md", "mimeType": "text/markdown", "buffer": data})
    expect(desk.source).to_have_value(source.replace("\r\n", "\n"))
    with desk.page.expect_download() as saved:
        desk.page.get_by_role("button", name="원고 저장").click()
    assert saved.value.suggested_filename == "활자.md"
    assert saved.value.path().read_bytes() == data
    desk.source.fill("다른 원고")
    upload.set_input_files(saved.value.path())
    expect(desk.source).to_have_value(source.replace("\r\n", "\n"))
    assert len(desk.requests) == 1


@pytest.mark.parametrize(("typed", "applied"), [
    ("-7.245", "-7.25"), ("2.675", "2.68"),
    ("-0.0049999999999999999999999999", "0"),
    ("1234.49999999999999999e-3", "1.23"),
])
def test_decimal_style_and_indents(desk, typed, applied):
    desk.ready()
    open_settings(desk.page)
    desk.page.locator("#style summary").click()
    first = desk.page.get_by_label("첫 줄 들여쓰기")
    rest = desk.page.get_by_label("이후 줄 들여쓰기")
    expect(first).to_have_value("0")
    expect(rest).to_have_value("0")
    first.fill("+03.2500")
    rest.fill("+0.7500")
    desk.page.get_by_label("한글 자간").fill(typed)
    desk.page.get_by_label("최소", exact=True).fill("70.005")
    desk.page.get_by_label("최대", exact=True).fill("129.9949999999999999999999")
    close_settings(desk.page)
    desk.submit(desk.refresh)
    assert desk.requests[-1]["style"] == {
        **DEFAULTS, "first_indent_mm": "+03.2500", "rest_indent_mm": "+0.7500",
        "ko_tracking_pct": typed, "space_pct": ["70.005", "129.9949999999999999999999"],
    }
    desk.reply(0, desk.pdf)
    expect(desk.page.locator("#applied-ratios")).to_contain_text(f"한글 자간 {applied}%")
    expect(desk.page.locator("#applied-ratios")).to_contain_text("어절 공백 70.01% ~ 129.99%")
    expect(desk.page.get_by_label("한글 자간")).to_have_value(typed)
    expect(desk.source).to_have_value(SAMPLE)


def test_rejected_style_keeps_draft_and_applied_values(desk):
    desk.ready()
    open_settings(desk.page)
    desk.page.locator("#style summary").click()
    caption = desk.page.locator("#applied-ratios")
    previous = caption.inner_text()
    control = desk.page.get_by_label("최대", exact=True)
    control.fill("130.00000000000000001")
    close_settings(desk.page)
    desk.submit(desk.export)
    assert desk.requests[-1]["style"]["space_pct"][1] == "130.00000000000000001"
    desk.reply(0, {"field": "space_pct", "message": "공백 범위를 확인하세요."}, 422)
    expect(control).to_have_value("130.00000000000000001")
    expect(caption).to_have_text(previous)
    expect(desk.page.get_by_role("status")).to_contain_text("이전 버전")


def test_template_leaves_publication_settings_to_server():
    template = (STATIC / "index.html").read_text(encoding="utf-8")
    assert template.count("<!--EDITOR_SETTINGS-->") == 1
    assert not re.search(r"<input\b[^>]*\bvalue=", template)
    assert "<option" not in template


def test_changed_bootstrap_precedes_sample(desk, monkeypatch):
    desk.ready()
    # Older bootstrap responses contain only the original defaults and fonts.
    settings = {}
    defaults = {
        **{k: v for k, v in DEFAULTS.items() if k not in {"text_styles", "paragraph_layout", "ko_tracking_bounds_pct"}}, "body_pt": "13.125", "body_font": "proof",
        "margins_mm": ["9.50", "17", "8.75", "15"], "space_pct": ["84", "116"],
    }
    settings.update(defaults=defaults, fonts=[{"id": "proof", "label": "교정 서체"},
                                            {"id": "nanum-gothic", "label": "제목용 서체"}])
    monkeypatch.setattr(style, "editor_bootstrap", lambda: settings)
    samples = []
    desk.page.route(ORIGIN + "/sample.md", lambda route: samples.append(route))
    desk.page.reload(wait_until="domcontentloaded")
    open_settings(desk.page)
    size = desk.page.get_by_label("본문 크기")
    expect(size).to_have_value("13.125")
    for font in ["body_font", "heading_font"]:
        assert desk.page.locator(f"#{font} option").all_text_contents() == ["교정 서체", "제목용 서체"]
    size.fill("14.500")
    with desk.page.expect_request(ORIGIN + "/api/render"):
        samples[0].fulfill(content_type="text/plain; charset=utf-8", body=SAMPLE)
    expect(size).to_have_value("14.500")
    assert desk.requests[-1] == {"connection": {"provider": "rule"}, "source": SAMPLE, "style": {**defaults, "body_pt": "14.500"}}
    desk.reply(0, desk.pdf)


def test_file_downloads_keep_visible_pdf_url(desk):
    desk.ready()
    page = desk.page
    open_settings(page)
    page.locator("#style summary").click()
    page.route(ORIGIN + "/api/style/export", lambda route: route.fulfill(json={"body_sp": 720896}))
    current = desk.frame.get_attribute("src")
    for label in ["원고 저장", "양식 저장", "PDF 다운로드"]:
        if label == "양식 저장":
            open_settings(page)
        else:
            close_settings(page)
        button = page.get_by_role("button", name=label, exact=True)
        button.scroll_into_view_if_needed()
        with page.expect_download() as saved:
            button.click()
        if label == "PDF 다운로드":
            assert saved.value.url == current
            assert saved.value.path().read_bytes() == desk.pdf
        else:
            page.wait_for_function(
                "async url => { try { await fetch(url); return false; } catch { return true; } }",
                arg=saved.value.url,
            )
        assert desk.frame.get_attribute("src") == current
        assert page.evaluate("async url => (await (await fetch(url)).blob()).size", current) == len(desk.pdf)

    desk.source.fill("# 다음 교정")
    desk.submit(desk.refresh)
    desk.reply(0, desk.pdf)
    assert desk.frame.get_attribute("src") != current
    assert page.evaluate("url => fetch(url).then(() => false, () => true)", current)


LIST_PRESETS = {
    "legal": [{"expr": "{원숫자}", "indent_mm": "2.75", "gap_mm": "1.25"}],
    "report": [
        {"expr": marker, "indent_mm": "3", "gap_mm": "0.75"} for marker in ("ㅁ", "ㅇ", "-")
    ],
}


@pytest.mark.parametrize("desk", [{"list_presets": {"legal": REPORT_LEVELS, "report": REPORT_LEVELS}}],
                         indirect=True)
def test_new_preset_name_when_installed_rows_match(desk):
    desk.ready()
    expect(desk.page.locator("#list-preset")).to_have_value("report", timeout=1000)


@pytest.mark.parametrize("desk", [{"list_presets": LIST_PRESETS, "default_list_preset": "report", "new_list_preset": None}],
                         indirect=True)
def test_list_presets_and_custom_levels(desk):
    inherited = {key: value for key, value in DEFAULTS.items() if key != "list_levels"}
    desk.ready(inherited)
    page = desk.page
    open_settings(page)
    page.locator("#style summary").click()
    preset = page.get_by_label("목록 양식", exact=True)
    expect(preset).to_be_visible(timeout=1500)
    expect(preset.locator("option:checked")).to_contain_text("보고서형")
    first = page.get_by_role("group", name="1단계", exact=True)
    expect(first.get_by_label("표지 표현식")).to_have_value("ㅁ")
    expect(page.get_by_text("마지막 단계", exact=False).first).to_be_visible()
    preset.select_option(label="법령형")
    expect(first.get_by_label("들여쓰기 (mm)")).to_have_value("2.75")
    expect(first.get_by_role("button", name="단계 삭제")).to_be_disabled()
    preset.select_option(label="보고서형")
    manuscript = "\n".join("  " * n + "- 항목" for n in range(5))
    close_settings(page)
    desk.source.fill(manuscript)
    desk.submit(desk.export)
    assert desk.requests[-1] == {"connection": {"provider": "rule"}, "source": manuscript, "style": {**DEFAULTS, "list_levels": LIST_PRESETS["report"]}}
    with page.expect_download() as saved:
        desk.reply(0, desk.pdf)
    assert saved.value.url == desk.frame.get_attribute("src")

    open_settings(page)
    page.get_by_role("button", name="단계 추가").click()
    row = page.get_by_role("group", name="4단계", exact=True)
    expect(row.get_by_label("표지 표현식")).to_have_value("-")
    expr = " {{ [붙임 {숫자}호] }} "
    row.get_by_label("표지 표현식").fill(expr)
    row.get_by_label("들여쓰기 (mm)").fill("+04.2500")
    row.get_by_label("표지 뒤 간격 (mm)").fill("0.500")
    page.get_by_role("group", name="2단계", exact=True).get_by_role("button", name="단계 삭제").click()
    expect(preset.locator("option:checked")).to_have_text("사용자 지정")
    close_settings(page)
    desk.submit(desk.refresh)
    assert desk.requests[-1]["style"]["list_levels"] == [
        LIST_PRESETS["report"][0], LIST_PRESETS["report"][2],
        {"expr": expr, "indent_mm": "+04.2500", "gap_mm": "0.500"},
    ]
    desk.reply(1, desk.pdf)
    open_settings(page)
    preset.select_option(index=0)
    close_settings(page)
    desk.submit(desk.refresh)
    assert desk.requests[-1]["style"] == inherited
    desk.reply(2, desk.pdf)


CUSTOM_DEFAULTS = {**DEFAULTS, "list_levels": [
    {"expr": "  {{붙임}}　{숫자} 호 ", "indent_mm": "1.750", "gap_mm": "2.50"},
]}


@pytest.mark.parametrize("desk", [{"defaults": CUSTOM_DEFAULTS, "list_presets": LIST_PRESETS,
                                   "default_list_preset": "legal"}], indirect=True)
def test_explicit_default_list_keeps_literal_whitespace(desk):
    desk.ready(CUSTOM_DEFAULTS)
    open_settings(desk.page)
    desk.page.locator("#style summary").click()
    row = desk.page.get_by_role("group", name="1단계", exact=True)
    expr = row.get_by_label("표지 표현식")
    expect(expr).to_have_value("  {{붙임}}　{숫자} 호 ")
    expect(desk.page.locator("#list-preset")).to_have_value("custom")


@pytest.mark.parametrize("desk", [{"default_article_expr": "조 {숫자}"}], indirect=True)
def test_article_numbering_style_and_errors(desk):
    desk.ready()
    page = desk.page
    open_settings(page)
    page.locator("#legal-options").click()
    enabled = page.get_by_label("조 제목 자동 번호", exact=True)
    expr = page.get_by_label("조 번호 표현식", exact=True)
    expect(enabled).to_be_visible(timeout=1000)
    expect(enabled).not_to_be_checked()
    expect(expr).to_be_disabled()
    enabled.check()
    expect(expr).to_have_value("조 {숫자}")
    expr.fill("  {{법}} {한글}  ")
    close_settings(page)
    desk.submit(desk.refresh)
    assert desk.requests[-1]["style"] == {**DEFAULTS, "article_expr": "  {{법}} {한글}  "}
    desk.reply(0, desk.pdf)
    previous = desk.frame.get_attribute("src")
    open_settings(page)
    expr.fill("{미정}")
    close_settings(page)
    desk.submit(desk.export)
    desk.reply(1, {"field": "article_expr", "message": "조 번호 표현식을 확인하세요."}, 422)
    expect(expr).to_have_attribute("aria-invalid", "true")
    expect(expr).to_have_value("{미정}")
    assert desk.frame.get_attribute("src") == previous
    open_settings(page)
    enabled.uncheck()
    close_settings(page)
    desk.submit(desk.refresh)
    assert desk.requests[-1]["style"] == DEFAULTS
    desk.reply(2, desk.pdf)


@pytest.fixture
def toggle_heading(desk, server_url):
    open_settings(desk.page)
    desk.page.locator("#legal-options").click()
    close_settings(desk.page)
    path = "/api/article/pin"

    def forward_pin(route):
        headers = {k: v for k, v in route.request.headers.items() if k.lower() not in ("host", "origin", "referer")}
        headers["origin"] = server_url
        headers["referer"] = server_url + "/"
        route.fulfill(response=route.fetch(url=server_url + path, headers=headers))

    desk.page.route(ORIGIN + path, forward_pin)
    button = desk.page.get_by_role("button", name="번호 고정/해제", exact=True)

    def toggle(token):
        desk.source.evaluate("""(el, token) => {
            const index = el.value.indexOf(token);
            if (index < 0) throw new Error("테스트 제목을 찾지 못했습니다.");
            el.setSelectionRange(index, index);
        }""", token)
        with desk.page.expect_response(ORIGIN + path) as received:
            button.press("Enter")
        expect(button).to_be_enabled()
        return received.value.status

    return toggle


def test_pin_heading_markdown_round_trip(desk, toggle_heading):
    desk.ready()
    page = desk.page
    pin = page.get_by_role("button", name="번호 고정/해제", exact=True)
    expect(pin).to_be_visible(timeout=1000)
    text = "# 문서\r\n\n  ### 제8조의2(대상) **강조**  ###  \r\n본문 제8조의2 인용\n"
    pinned = text.replace("**강조**", "**강조** {번호고정}")
    upload = page.locator("#file")
    upload.set_input_files({"name": "조문.md", "mimeType": "text/markdown",
                            "buffer": b"\xef\xbb\xbf" + text.encode()})

    expect(desk.source).to_have_value(text.replace("\r\n", "\n"))
    assert toggle_heading("강조") == 200
    expect(desk.source).to_have_value(pinned.replace("\r\n", "\n"))
    with page.expect_download() as saved:
        page.get_by_role("button", name="원고 저장").click()
    assert saved.value.path().read_bytes() == b"\xef\xbb\xbf" + pinned.encode()
    desk.source.fill("다른 원고")
    upload.set_input_files(saved.value.path())
    expect(desk.source).to_have_value(pinned.replace("\r\n", "\n"))
    assert toggle_heading("대상") == 200
    expect(desk.source).to_have_value(text.replace("\r\n", "\n"))
    desk.submit(desk.refresh)
    assert desk.requests[-1]["source"] == text
    desk.reply(0, desk.pdf)


def test_pin_leaves_non_article_text_literal(desk, toggle_heading):
    desk.ready()
    page = desk.page
    pin = page.get_by_role("button", name="번호 고정/해제", exact=True)
    expect(pin).to_be_visible(timeout=1000)
    text = ("제8조 본문 인용 {번호고정}\n# 일반 제목 {번호고정}\n# 제8조사 결과\n"
            "- 목록 항목\n  ## 제9조 목록 내부\n\n```\n# 제7조 코드 예시\n```\n"
            "> # 제6조 인용 블록\n\n제2조 부칙\n----------\n")
    desk.source.fill(text)
    for token in ["본문 인용", "일반 제목", "조사 결과", "목록 내부", "코드 예시", "인용 블록"]:
        assert toggle_heading(token) == 422
        expect(page.get_by_role("alert")).not_to_be_empty()
        expect(desk.source).to_have_value(text)
    assert toggle_heading("부칙") == 200
    expect(desk.source).to_have_value(text.replace("부칙\n", "부칙 {번호고정}\n"))


@pytest.mark.parametrize(("heading", "result"), [
    ("# 제**8**조의02(범위) *상세* ###", "# 제**8**조의02(범위) *상세* {번호고정} ###"),
    ("# **제7조 *목적* {번호고정}** ##", "# **제7조 *목적*** ##"),
    ("**제8조** 시행\n기간\n====", "**제8조** 시행\n기간 {번호고정}\n===="),
    ("제8조 목적\n{번호고정}\n====", "제8조 목적\n===="),
    ("# ***제8조（범위）***", "# ***제8조（범위）*** {번호고정}"),
])
def test_pin_emphasized_article(desk, toggle_heading, heading, result):
    desk.ready()
    desk.source.fill(heading)
    assert toggle_heading("제") == 200
    expect(desk.source).to_have_value(result)


@pytest.mark.parametrize(("heading", "token", "result"), [
    ("# 제0조 제목", "제0", "# 제0조 제목"),
    ("# 제007조（목적）", "목적", "# 제007조（목적） {번호고정}"),
    ("제1조 부칙\n-", "부칙", "제1조 부칙 {번호고정}\n-"),
    ("- 첫 항\n이음\n  ## 제8조 본문", "제8", "- 첫 항\n이음\n  ## 제8조 본문"),
    ("문단\n7. 인용\n  ## 제8조 상위", "제8", "문단\n7. 인용\n  ## 제8조 상위 {번호고정}"),
    ("> 인용\n제8조 인용\n---", "제8", "> 인용\n제8조 인용\n---"),
])
def test_pin_article_markdown_boundaries(desk, toggle_heading, heading, token, result):
    desk.ready()
    desk.source.fill(heading)
    assert toggle_heading(token) == (422 if heading == result else 200)
    assert desk.source.input_value() == result


@pytest.mark.parametrize("replacement", ["edit back", "open", "new pin"])
def test_slow_pin_cannot_change_replaced_source(desk, replacement):
    desk.ready()
    open_settings(desk.page)
    desk.page.locator("#legal-options").click()
    close_settings(desk.page)
    source = "# 제8조 옛 제목"
    desk.source.fill(source)
    page = desk.page
    url = ORIGIN + "/api/article/pin"
    held = []
    page.route(url, lambda route: held.append(route))
    pin = page.get_by_role("button", name="번호 고정/해제", exact=True)
    with page.expect_request(url, timeout=1500):
        pin.press("Enter")
    expect(pin).to_be_disabled()
    expect(desk.export).to_be_disabled()
    expect(page.locator("#save")).to_be_disabled()
    assert held[0].request.post_data_json == {"source": source, "first_line": 0, "last_line": 0}

    new = "# 제1조 새 제목"
    if replacement == "open":
        page.locator("#file").set_input_files({"name": "새.md", "mimeType": "text/markdown", "buffer": new.encode()})
        expect(desk.source).to_have_value(new)
    else:
        desk.source.fill(new)
        if replacement == "edit back":
            new = source
            desk.source.fill(source)
        else:
            with page.expect_request(url):
                pin.press("Enter")
            held[1].fulfill(json={"source": new + " {번호고정}"})
            new += " {번호고정}"
            expect(desk.source).to_have_value(new)
    with page.expect_request_finished(lambda req: req == held[0].request):
        held[0].fulfill(json={"source": source + " {번호고정}"})
    expect(pin).to_be_enabled()
    expect(desk.export).to_be_enabled()
    expect(desk.source).to_have_value(new)


def test_pin_submits_selected_lines_and_preserves_file_endings(desk):
    desk.ready()
    page = desk.page
    open_settings(page)
    page.locator("#legal-options").click()
    close_settings(page)
    text = "제1조 이전\r\n\r목적\n===\r\n본문\n"
    page.locator("#file").set_input_files({
        "name": "조문.md", "mimeType": "text/markdown", "buffer": b"\xef\xbb\xbf" + text.encode(),
    })
    view = text.replace("\r\n", "\n").replace("\r", "\n")
    expect(desk.source).to_have_value(view)
    desk.source.focus()
    desk.source.evaluate("(el, end) => el.setSelectionRange(end, end)", view.index("\n"))
    desk.source.press("!")
    desk.source.press("Delete")
    desk.source.evaluate("""el => {
        el.setSelectionRange(el.value.indexOf("목적"), el.value.indexOf("본문"));
    }""")
    url = ORIGIN + "/api/article/pin"
    page.route(url, lambda route: route.fulfill(status=422, json={"field": "source", "message": "조 제목을 선택하세요."}))
    with page.expect_request(url) as sent:
        page.locator("#toggle-article-pin").press("Enter")
    # Delete the CRLF at the caret; the adjacent CR belongs to the next line.
    edited = text.replace("이전\r\n", "이전!")
    assert sent.value.post_data_json == {"source": edited, "first_line": 1, "last_line": 2}
    expect(page.get_by_role("alert")).to_have_text("조 제목을 선택하세요.")
    with page.expect_download() as saved:
        page.locator("#save").press("Enter")
    assert saved.value.path().read_bytes() == b"\xef\xbb\xbf" + edited.encode()


def test_article_style_file_round_trip(browser, server_url):
    with browser.new_context() as context:
        page = context.new_page()
        page.goto(server_url)
        open_settings(page)
        page.locator("#style summary").click()
        enabled = page.get_by_label("조 제목 자동 번호", exact=True)
        expr = page.get_by_label("조 번호 표현식", exact=True)
        expect(enabled).to_be_hidden(timeout=1200)
        manuscript = "# 제7조(가지) {번호고정}\n\n본문 제7조\n"
        close_settings(page)
        page.locator("#source").fill(manuscript)
        open_settings(page)

        def save():
            with page.expect_download() as saved:
                page.locator("#save-style").press("Enter")
            return saved.value.path()

        legacy = save()
        assert "article_expr" not in json.loads(legacy.read_bytes())
        for value in ("  {{법}} {한글}  ", ""):
            page.locator("#legal-options").click()
            enabled.check()
            expr.fill(value)
            custom = save()
            stored = json.loads(custom.read_bytes())
            assert stored["article_expr"] == value
            expr.fill("{잘못된표현}")
            with page.expect_response("**/api/style/export") as rejected:
                page.locator("#save-style").press("Enter")
            assert rejected.value.status == 422
            expect(expr).to_have_attribute("aria-invalid", "true")
            expect(expr).to_have_value("{잘못된표현}")
            assert json.loads(custom.read_bytes()) == stored
            enabled.uncheck()
            page.locator("#style-file").set_input_files(custom)
            expect(enabled).to_be_visible()
            expect(enabled).to_be_checked()
            expect(expr).to_have_value(value)
            expect(expr).to_have_attribute("aria-invalid", "false")
            assert json.loads(save().read_bytes()) == stored
            page.locator("#style-file").set_input_files(legacy)
            expect(enabled).not_to_be_checked()
            assert json.loads(save().read_bytes()) == json.loads(legacy.read_bytes())
        expect(page.locator("#source")).to_have_value(manuscript)


def test_role_edits_and_optional_tracking_bounds(desk):
    page = desk.page
    expect(desk.frame).to_have_attribute("src", BLOB)
    open_settings(page)
    page.locator("#style summary").click()
    role = page.get_by_label("글자 모양 대상", exact=True)
    role.select_option("heading1")
    size = page.get_by_label("대상 글자 크기", exact=True)
    size.fill("18")
    page.locator("#body_pt").fill("16")
    role.select_option("note")
    page.get_by_label("대상 글자 색", exact=True).fill("#713344")
    role.select_option("heading1")
    expect(size).to_have_value("18")
    assert page.evaluate("styleForm.get().text_styles.note") == {"color": "#713344"}
    page.get_by_label("최종 자간 한계 지정", exact=True).check()
    page.get_by_label("최종 자간 하한", exact=True).fill("-7")
    page.get_by_label("최종 자간 상한", exact=True).fill("0")
    close_settings(page)
    desk.submit(desk.refresh)
    received = desk.requests[-1]["style"]
    assert received["body_pt"] == "16" and received["text_styles"]["heading1"] == {"font_pt": "18"}
    assert received["ko_tracking_bounds_pct"] == ["-7", "0"]
    desk.reply(0, {"field": "ko_tracking_bounds_pct", "message": "자간 한계를 확인하세요."}, 422)
    expect(page.get_by_label("최종 자간 하한", exact=True)).to_have_attribute("aria-invalid", "true")
    expect(desk.frame).to_have_attribute("src", BLOB)
    open_settings(page)
    page.get_by_label("최종 자간 한계 지정", exact=True).uncheck()
    assert page.evaluate("styleForm.get().ko_tracking_bounds_pct") is None


def test_new_paragraph_mode_survives_save_without_upgrading_an_opened_style(desk):
    page = desk.page
    expect(desk.frame).to_have_attribute("src", BLOB)
    open_settings(page)
    page.locator("#style summary").click()
    control = page.get_by_label("문단 배치 방식", exact=True)
    expect(control).to_have_value("justified")
    assert desk.requests[0]["style"] == DEFAULTS
    for mode in ["balanced", None, "justified"]:
        saved = {**DEFAULTS, "space_pct": ["100", "100"], "ko_tracking_bounds_pct": ["0", "0"]}
        if mode is None:
            del saved["paragraph_layout"]
        else:
            saved["paragraph_layout"] = mode
        page.evaluate("values => styleForm.set(values)", saved)
        expect(control).to_have_value(mode or "")
        assert page.evaluate("styleForm.get()") == saved
    control.select_option("balanced")
    assert page.evaluate("styleForm.get()") == {**saved, "paragraph_layout": "balanced"}
    control.select_option("")
    page.get_by_label("최종 자간 한계 지정", exact=True).uncheck()
    assert page.evaluate("styleForm.get()") == {
        k: v for k, v in saved.items() if k not in {"paragraph_layout", "ko_tracking_bounds_pct"}}


def test_preview_caption_describes_justified_settings_used_for_displayed_pdf(desk):
    desk.ready()
    page = desk.page
    open_settings(page)
    page.locator("#style summary").click()
    caption = page.locator("#applied-ratios")
    expect(caption).to_have_text("표시 중인 PDF의 양끝맞춤 설정 · 기본 한글 자간 0% · "
                                 "줄별 최종 자간 허용 범위 -15% ~ 15% · 어절 공백 70% ~ 130% 허용")
    page.get_by_label("한글 자간").fill("-3")
    page.get_by_label("최종 자간 하한", exact=True).fill("-7")
    page.get_by_label("최소", exact=True).fill("90")
    close_settings(page)
    expect(caption).to_contain_text("기본 한글 자간 0%")
    desk.submit(desk.refresh)
    desk.reply(0, desk.pdf)
    expect(caption).to_have_text("표시 중인 PDF의 양끝맞춤 설정 · 기본 한글 자간 -3% · "
                                 "줄별 최종 자간 허용 범위 -7% ~ 15% · 어절 공백 90% ~ 130% 허용")
    open_settings(page)
    page.get_by_label("최종 자간 한계 지정", exact=True).uncheck()
    close_settings(page)
    desk.submit(desk.refresh)
    desk.reply(1, desk.pdf)
    expect(caption).to_contain_text("줄별 자간 보정 끔")
    open_settings(page)
    page.get_by_label("문단 배치 방식", exact=True).select_option("balanced")
    close_settings(page)
    desk.submit(desk.refresh)
    desk.reply(2, desk.pdf)
    expect(caption).to_have_text("표시 중인 PDF 적용값 · 한글 자간 -3% · 어절 공백 90% ~ 130%")


@pytest.mark.parametrize(("base", "bounds", "spaces", "typed", "activate"), [
    ("15", None, ["100", "100"], False, True),
    ("-15.000000000000000001", None, ["100", "100"], False, False),
    ("14.999999999999999999", None, ["100", "100"], False, True),
    ("0", ["0", "0"], ["100", "100"], False, False),
    ("0", ["-7", "15"], ["100", "100"], False, False),
    ("0", None, ["98", "98"], False, False),
    ("0", None, ["100", "100"], True, False),
])
def test_layout_choice_preserves_manual_spacing(desk, base, bounds, spaces, typed, activate):
    page = desk.page
    desk.ready()
    values = {**DEFAULTS, "paragraph_layout": "balanced", "ko_tracking_pct": base, "space_pct": spaces}
    if bounds is None:
        del values["ko_tracking_bounds_pct"]
    else:
        values["ko_tracking_bounds_pct"] = bounds
    page.evaluate("v => styleForm.set(v)", values)
    open_settings(page)
    if typed:
        page.locator("#style summary").click()
        low = page.get_by_label("최소", exact=True)
        low.fill("95")
        low.fill("100")
    page.get_by_label("문단 배치 방식", exact=True).select_option("justified")
    expected = {**values, "paragraph_layout": "justified"}
    if activate:
        expected.update(ko_tracking_bounds_pct=["-15", "15"], space_pct=["70", "130"])
    assert page.evaluate("styleForm.get()") == expected
    if base.startswith("-15.000"):
        expect(page.locator("#spacing-notice")).to_contain_text("현재 기본 자간을 유지했습니다")


@pytest.mark.parametrize("desk", [{"new_paragraph_layout": "balanced", "new_spacing": {},
    "justified_spacing": {"ko_tracking_bounds_pct": ["-2", "4.5"], "space_pct": ["91", "112"]}}], indirect=True)
def test_manual_mode_choice_uses_installed_justified_preset(desk):
    page = desk.page
    expect(desk.frame).to_have_attribute("src", BLOB)
    open_settings(page)
    layout = page.get_by_label("문단 배치 방식", exact=True)
    expect(layout).to_have_value("balanced")
    layout.select_option("justified")
    expect(page.locator("#tracking-lower")).to_have_value("-2")
    expect(page.locator("#tracking-upper")).to_have_value("4.5")
    assert page.evaluate("styleForm.get().space_pct") == ["91", "112"]


@pytest.mark.parametrize("setting", ["align", "space_after_pt"])
def test_pin_uses_the_inherited_heading_boundary(desk, toggle_heading, setting):
    page = desk.page
    source = "    example\n제8조 목적\n---"
    desk.source.fill(source)
    toggle_heading("제8조")
    expect(desk.source).to_have_value(source)
    open_settings(page)
    page.locator("#style summary").click()
    page.locator("#text-role").select_option("heading2")
    if setting == "align":
        page.locator('[data-text="align"]').select_option("center")
    else:
        panel = page.locator("#text-roles")
        panel.locator('[data-spacing-mode="space_after_pt"]').select_option("manual")
        panel.locator('[data-spacing-value="space_after_pt"]').fill("0")
    close_settings(page)
    toggle_heading("제8조")
    expect(desk.source).to_have_value(source.replace("목적", "목적 {번호고정}"))
    toggle_heading("제8조")
    expect(desk.source).to_have_value(source)
