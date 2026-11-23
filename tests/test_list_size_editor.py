"""List size controls reuse the existing optional point-size field."""

import pytest
from playwright.sync_api import expect

from settings_ui import open_settings
from test_editor import DEFAULTS, browser, desk


LEVELS = [
    {"expr": "{원숫자}", "indent_mm": "3", "gap_mm": "1", "font_pt": "12.0000152587890625"},
    {"expr": "-", "indent_mm": "3", "gap_mm": "1", "weight": "bold"},
]


@pytest.mark.parametrize("desk", [{"defaults": {**DEFAULTS, "list_levels": LEVELS}}], indirect=True)
def test_independent_list_sizes_preserve_precision_and_inheritance(desk):
    desk.ready({**DEFAULTS, "list_levels": LEVELS})
    page = desk.page
    open_settings(page)
    page.locator("#style summary").click()
    sizes = page.locator('#list-levels input[name="font_pt"]')
    expect(sizes).to_have_count(2)
    expect(sizes.first).to_have_value(LEVELS[0]["font_pt"])
    expect(sizes.last).to_have_value("")
    assert page.evaluate("styleForm.get().list_levels") == LEVELS
    sizes.last.fill("13.25")
    sizes.first.fill("")
    first, second = page.evaluate("styleForm.get().list_levels")
    assert "font_pt" not in first
    assert second == {**LEVELS[1], "font_pt": "13.25"}


def test_list_body_shortcut_opens_plain_paragraph_settings(desk):
    desk.ready()
    page = desk.page
    open_settings(page)
    page.locator("#style summary").click()
    roles = page.locator("#text-role")
    roles.select_option("heading2")
    before = page.evaluate("styleForm.get()")
    page.get_by_role("button", name="0단계 본문 설정").click()
    expect(roles).to_have_value("body")
    expect(roles).to_be_focused()
    assert page.evaluate("styleForm.get()") == before
