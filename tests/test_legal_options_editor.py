"""Ordinary manuscripts keep the legal controls out of the way."""
from playwright.sync_api import expect

from settings_ui import close_settings, open_settings
from test_editor import browser, desk


def test_legal_disclosure_and_numbering_are_independent(desk):
    desk.ready()
    page = desk.page
    open_settings(page)
    options = page.get_by_role("button", name="법령 문서 옵션", exact=True)
    pin = page.locator("#toggle-article-pin")
    numbering = page.get_by_label("조 제목 자동 번호", exact=True)
    expect(options).to_be_visible(timeout=1500)
    expect(options).to_have_attribute("aria-expanded", "false")
    expect(numbering).to_be_hidden()
    expect(pin).to_be_hidden()
    options.click()
    expect(numbering).not_to_be_checked()
    expect(pin).to_be_visible()
    expect(page.locator("#status")).to_have_text("최신 버전")
    numbering.check()
    options.click()
    expect(pin).to_be_visible()
    expect(numbering).to_be_hidden()
    saved = page.evaluate("styleForm.get()")
    assert saved["article_expr"] == "제{숫자}조"

    close_settings(page)
    desk.submit(desk.refresh)
    desk.reply(0, {"field": "article_expr", "message": "조 번호 표현식 오류"}, 422)
    open_settings(page)
    expect(options).to_have_attribute("aria-expanded", "true")
    expect(page.get_by_label("조 번호 표현식", exact=True)).to_have_attribute("aria-invalid", "true")
    numbering.uncheck()
    options.click()
    expect(pin).to_be_hidden()
    assert "article_expr" not in page.evaluate("styleForm.get()")
