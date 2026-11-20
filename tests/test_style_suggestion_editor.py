"""Normal editor → isolated HTTP worker → model → confirmed style → downloaded PDF."""
import json
import os

import pytest
from playwright.sync_api import expect

from app.style import Style, apply_style
from pdf_metrics import read_pages
from test_editor import browser
from test_slice import server_url
from test_style_ai import BODY, BOTH, FOOT, reply
from test_style_suggestion_api import ENDPOINT, local_model

SOURCE = "# 제목\n\n본문입니다.\n"
HEADER = {"path": [], "role": "header", "values": {"left": "기관"}}
TEXT_STYLES = {"heading1": {"font_pt": "18", "color": "#17365d"}}


@pytest.fixture
def page(browser, server_url):
    with browser.new_context() as context:
        p = context.new_page()
        p.set_default_timeout(8000)
        p.goto(server_url)
        expect(p.locator("#filename")).to_have_text("비교 원고.md")
        p.locator("#source").fill(SOURCE)
        expect(p.locator("#source")).to_have_value(SOURCE)
        p.get_by_text("AI 양식 제안", exact=True).click()
        yield p


def pdf(page, destination):
    with page.expect_download(timeout=45000) as download:
        page.get_by_role("button", name="PDF 다운로드").click()
    download.value.save_as(destination)
    result, = read_pages(destination.read_bytes())
    return result


def measured(page, points, numbered):
    rows = {line.text: line.chars for line in page.lines}
    body, heading = rows["본문입니다."], rows["제목"]
    assert body[0].size == pytest.approx(points * 72 / 72.27, abs=.01)
    assert heading[0].size == pytest.approx(18 * 72 / 72.27, abs=.01)
    assert body[0].x0 == pytest.approx(23 * 72 / 25.4, abs=.02)
    foot = None
    if numbered:
        digit, = rows["1"]
        foot = (digit.x0 + digit.x1) / 2
        assert foot == pytest.approx(page.width / 2, abs=.02)
        assert digit.baseline < 15 * 72 / 25.4
    else:
        assert "1" not in rows
    return tuple(round(v, 3) if v is not None else None for v in (body[0].size, heading[0].size, body[0].x0, foot))


def open_saved_style(page):
    stored = apply_style(Style(), {"margins_mm": [20, 23, 20, 23], "section_rules": [HEADER],
                                  "text_styles": TEXT_STYLES, "ko_tracking_bounds_pct": [-2, 2]}).to_stored()
    with page.expect_response("**/api/style/open"):
        page.locator("#style-file").set_input_files({"name": "보고서.json", "mimeType": "application/json",
                                                    "buffer": json.dumps(stored).encode()})
    expect(page.locator("#body_pt")).to_have_value("11")


@pytest.mark.parametrize("real", [False, pytest.param(True, marks=pytest.mark.skipif(
    os.environ.get("JJOKGYEOL_TEST_STYLE_MODEL") != "1", reason="로컬 모델 인수 전용"))], ids=["offline", "gemma"])
def test_suggestions_reach_pdf(page, request, real, tmp_path):
    control = None if real else request.getfixturevalue("local_model")[0]
    open_saved_style(page)
    page.locator("#style summary").click()
    initial = measured(pdf(page, tmp_path / "before.pdf"), 11, False)
    for prompt, numbered in [(BOTH, True), (BODY, False)]:
        if not numbered:
            open_saved_style(page)
            if control is not None:
                control["answer"] = reply(("body_pt", "16"))
        page.get_by_label("양식 변경 요청").fill(prompt)
        with page.expect_response("**" + ENDPOINT, timeout=100000) as response:
            page.get_by_role("button", name="제안 받기").click()
        assert response.value.ok, response.value.text()
        submitted = response.value.request.post_data_json["style"]
        assert submitted["text_styles"] == TEXT_STYLES
        assert submitted["ko_tracking_bounds_pct"] == ["-2", "2"]
        patch = response.value.json()["patch"]
        assert patch == ({"body_pt": "16", "section_rules": [HEADER, FOOT]} if numbered else {"body_pt": "16"})
        expect(page.locator("#suggestion-fields")).to_contain_text("본문 크기")
        if numbered:
            expect(page.locator("#suggestion-fields")).to_contain_text("바닥글 가운데")
            expect(page.locator("#suggestion-fields")).to_contain_text("{현재쪽}")
        expect(page.locator("#body_pt")).to_have_value("11")
        page.get_by_role("button", name="제안 적용").click()
        expect(page.locator("#body_pt")).to_have_value("16")
        name = "body-and-footer.pdf" if numbered else "body-only.pdf"
        metric = measured(pdf(page, tmp_path / name), 16, numbered)
        with page.expect_download() as export:
            page.locator("#save-style").click()
        saved = json.loads(export.value.path().read_bytes())
        assert saved["text_styles"] == {"heading1": {"font_sp": 18 * 65536, "color": "#17365d"}}
        assert saved["ko_tracking_bounds_per_10000"] == [-200, 200]
        manual = None
        if numbered:
            page.locator("#body_pt").fill("14")
            manual = measured(pdf(page, tmp_path / "manual-14.pdf"), 14, True)
        expect(page.locator("#source")).to_have_value(SOURCE)
        print(prompt, patch, "PDF (본문 pt, 제목 pt, 본문 x, 번호 중심 x)", initial, metric, manual)


def test_cancel_error_and_edits_while_waiting(page):
    held = []
    page.route("**" + ENDPOINT, lambda route: held.append(route))
    page.expose_function("requestIsHeld", lambda: bool(held))
    prompt = page.get_by_label("양식 변경 요청")
    prompt.fill(BODY)
    ask = page.get_by_role("button", name="제안 받기")
    apply = page.get_by_role("button", name="제안 적용")
    body = page.locator("#body_pt")
    message = page.locator("#suggestion-message")

    def start():
        ask.click()
        page.wait_for_function("() => window.requestIsHeld()")

    def propose():
        held.pop(0).fulfill(json={"patch": {"body_pt": "16"}})

    start()
    body.fill("13")
    propose()
    expect(message).to_contain_text("다시 요청")
    expect(apply).to_be_hidden()
    expect(body).to_have_value("13")

    start()
    propose()
    expect(apply).to_be_visible()
    page.get_by_role("button", name="제안 취소").click()
    expect(apply).to_be_hidden()
    expect(body).to_have_value("13")

    start()
    held.pop(0).fulfill(status=502, json={"message": "모델 연결 실패"})
    expect(message).to_contain_text("모델 연결 실패")
    expect(apply).to_be_hidden()
    expect(body).to_have_value("13")

    start()
    page.locator("#source").fill("직접 수정한 원고")
    propose()
    expect(apply).to_be_visible()
    body.fill("14")
    expect(apply).to_be_hidden()
    expect(message).to_contain_text("다시 요청")
    expect(body).to_have_value("14")
    expect(page.locator("#source")).to_have_value("직접 수정한 원고")


def test_style_open_supersedes_review_and_source_edit_can_coexist(page):
    page.route("**" + ENDPOINT, lambda route: route.fulfill(json={"patch": {"body_pt": "16"}}))
    page.get_by_label("양식 변경 요청").fill(BODY)
    ask = page.get_by_role("button", name="제안 받기")
    apply = page.get_by_role("button", name="제안 적용")
    ask.click()
    expect(apply).to_be_visible()
    open_saved_style(page)
    expect(apply).to_be_hidden()
    expect(page.locator("#suggestion-message")).to_contain_text("다시 요청")
    ask.click()
    expect(apply).to_be_visible()
    page.locator("#source").fill("검토 중 바꾼 원고")
    apply.click()
    expect(page.locator("#body_pt")).to_have_value("16")
    expect(page.locator("#source")).to_have_value("검토 중 바꾼 원고")


def test_disabled_article_formula_survives_body_suggestion(page, local_model):
    page.locator("#style summary").click()
    numbering = page.locator("#article-numbering")
    expr = page.locator("#article-expr")
    numbering.check()
    expr.fill("조 {숫자}")
    numbering.uncheck()
    local_model[0]["answer"] = reply(("body_pt", "16"))
    page.get_by_label("양식 변경 요청").fill(BODY)
    page.get_by_role("button", name="제안 받기").click()
    page.get_by_role("button", name="제안 적용").click()
    expect(page.locator("#body_pt")).to_have_value("16")
    expect(numbering).not_to_be_checked()
    numbering.check()
    expect(expr).to_have_value("조 {숫자}")


def test_reference_exclusion_survives_late_footer_suggestion_and_retry(page, local_model):
    open_saved_style(page)
    page.locator("#open-reference").click()
    exclude = page.get_by_label("규칙 제외", exact=True)
    expect(exclude).to_be_visible()
    held = []
    page.route("**" + ENDPOINT, lambda route: held.append(route), times=1)
    page.get_by_label("양식 변경 요청").fill(BOTH)
    ask = page.get_by_role("button", name="제안 받기")
    apply = page.get_by_role("button", name="제안 적용")
    footer = {"patch": {"body_pt": "16", "section_rules": [HEADER, FOOT]}}
    with page.expect_request("**" + ENDPOINT):
        ask.click()
    exclude.check()
    held.pop().fulfill(json=footer)
    expect(page.locator("#suggestion-message")).to_contain_text("다시 요청")
    expect(apply).to_be_hidden()
    expect(exclude).to_be_checked()

    ask.click()
    expect(page.locator("#suggestion-message")).to_contain_text("참고")
    expect(apply).to_be_hidden()
    expect(exclude).to_be_checked()
    expect(page.locator("#body_pt")).to_have_value("11")

    exclude.uncheck()
    ask.click()
    expect(apply).to_be_visible()
    exclude.check()
    expect(apply).to_be_hidden()
    expect(exclude).to_be_checked()

    local_model[0]["answer"] = reply(("body_pt", "16"))
    page.get_by_label("양식 변경 요청").fill(BODY)
    ask.click()
    apply.click()
    expect(exclude).to_be_checked()
    expect(page.locator("#body_pt")).to_have_value("16")
