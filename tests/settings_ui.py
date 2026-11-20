"""Open and close the web editor's settings dialog through its controls."""
from playwright.sync_api import Page, expect


def open_settings(page: Page):
    dialog = page.locator("dialog#settings-dialog")
    if not dialog.is_visible():
        page.locator("#open-settings").click()
    expect(dialog).to_be_visible()


def close_settings(page: Page):
    dialog = page.locator("dialog#settings-dialog")
    if dialog.is_visible():
        page.locator("#close-settings").click()
    expect(dialog).to_be_hidden()
