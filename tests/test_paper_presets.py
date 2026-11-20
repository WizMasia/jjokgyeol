"""Preset names are editor choices. The file and render contracts carry dimensions."""
import pytest
from playwright.sync_api import expect

from test_editor import browser, desk
from test_mirrored_margin_editor import open_style, save_style
from test_reference_editor import candidate, review, unit
from test_slice import server_url


SIZES = {"A4": ("210", "297"), "A5": ("148", "210"), "A6": ("105", "148"),
         "신국판": ("152", "225"), "사륙판": ("128", "188"), "사륙배판": ("188", "257")}


def controls(page):
    page.locator("#style summary").click()
    preset = page.get_by_role("combobox", name="판형 선택", exact=True)
    expect(preset).to_have_id("page-preset", timeout=1000)
    return preset, page.get_by_label("폭", exact=True), page.get_by_label("높이", exact=True)


def test_portrait_presets_and_custom_edits_reach_preview(desk):
    desk.ready()
    preset, width, height = controls(desk.page)
    expect(preset).to_have_value("A5")
    for name, (w, h) in SIZES.items():
        label = f"{name} ({w} × {h} mm)"
        preset.select_option(label=label)
        expect(width).to_have_value(w)
        expect(height).to_have_value(h)
    desk.submit(desk.refresh)
    assert desk.requests[-1]["style"]["page_mm"] == ["188", "257"]
    desk.reply(0, desk.pdf)
    preset.select_option(label="직접 입력")
    expect(width).to_have_value("188")
    expect(height).to_have_value("257")
    expect(desk.page.locator("#status")).to_contain_text("최신")
    width.fill("2.10e2")
    height.fill("297.00")
    expect(preset).to_have_value("A4")
    height.fill("297.00000000000000001")
    expect(preset).to_have_value("custom")
    desk.submit(desk.refresh)
    assert desk.requests[-1]["style"]["page_mm"] == ["2.10e2", "297.00000000000000001"]


def test_open_and_save_preserve_custom_landscape_and_one_sp(review):
    preset, width, height = controls(review.page)
    preset.select_option("신국판")
    stored = save_style(review.page)
    assert stored["page_sp"] == [28343133, 41955296]
    for name, sp in [("custom", [41955296, 28343133]), ("custom", [28343134, 41955296]),
                     ("신국판", [28343133, 41955296])]:
        file = {**stored, "page_sp": sp}
        open_style(review.page, file)
        expect(preset).to_have_value(name)
        assert save_style(review.page) == file
    expect(width).to_have_value("152")
    expect(height).to_have_value("225")


@pytest.mark.parametrize("desk", [{"page_presets_mm": {"교정판": ["124.5", "192"]}}], indirect=True)
def test_installed_catalog_controls_options_and_dimensions(desk):
    desk.ready()
    preset, width, height = controls(desk.page)
    expect(preset).to_have_value("custom")
    assert preset.locator("option").all_text_contents() == ["교정판 (124.5 × 192 mm)", "직접 입력"]
    preset.select_option("교정판")
    expect(width).to_have_value("124.5")
    expect(height).to_have_value("192")


def test_reference_page_dimensions_update_selection_on_apply(review):
    page = review.page
    preset, width, height = controls(page)
    size = candidate("document", "page_mm", [170, 230])
    page.route("**/api/reference/analyze?*", lambda route: route.fulfill(
        json={"units": [unit(1, "본문", size)], "warnings": []}))
    review.open()
    page.locator("#reference-file").set_input_files(
        {"name": "참고.pdf", "mimeType": "application/pdf", "buffer": b"reference"})
    page.locator("#reference-analyze").click()
    review.row("document", "page_mm").get_by_role("checkbox").check()
    review.submit(page.locator("#reference-trial"))
    assert review.requests[-1]["style"]["page_mm"] == ["170", "230"]
    review.reply(0, review.pdf)
    expect(preset).to_have_value("A5")
    page.locator("#reference-apply").click()
    expect(preset).to_have_value("custom")
    expect(width).to_have_value("170")
    expect(height).to_have_value("230")


def test_preset_control_keeps_normal_height_in_a_tall_settings_row(desk):
    desk.ready()
    page = desk.page
    page.set_viewport_size({"width": 1360, "height": 960})
    preset, _, _ = controls(page)
    assert preset.bounding_box()["height"] <= page.locator("#margin-mode").bounding_box()["height"] + 1
