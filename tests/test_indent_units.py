"""Paragraph indent display units at the browser / integer style-file boundary."""
from decimal import Decimal

import pytest
from playwright.sync_api import expect

from test_editor import browser
from test_mirrored_margin_editor import controls, field, open_style, save_style, switch
from test_reference_editor import review
from test_slice import server_url


def indent_controls(d):
    p, _ = controls(d)
    unit = field(p, "들여쓰기 단위")
    expect(unit).to_have_attribute("id", "indent-unit", timeout=1000)
    expect(unit).to_have_value("mm")
    return unit, field(p, "첫 줄 들여쓰기"), field(p, "이후 줄 들여쓰기")


def test_display_only_preserves_preview_drafts_and_opened_sp(review):
    d, p = review, review.page
    unit, first, rest = indent_controls(d)
    original = d.frame.get_attribute("src")
    unit.select_option("pt")
    expect(first).to_have_value("0")
    expect(p.get_by_role("status")).to_have_text("최신 버전")
    d.export.scroll_into_view_if_needed()
    with p.expect_download() as pdf:
        d.export.click()
    assert pdf.value.url == original and len(d.requests) == 1
    unit.select_option("mm")
    first.fill("+025.400")
    unit.select_option("pt")
    unit.select_option("mm")
    expect(first).to_have_value("+025.400")
    unit.select_option("pt")
    first.fill("11.000")
    rest.fill("0.0000152587890625")
    unit.select_option("mm")
    unit.select_option("pt")
    expect(first).to_have_value("11.000")
    stored = save_style(p)
    assert (stored["first_indent_sp"], stored["rest_indent_sp"]) == (720896, 1)
    d.submit(d.refresh)
    payload = d.requests[-1]["style"]
    assert "first_indent_pt" not in payload and "indent_unit" not in payload
    assert p.request.post(d.service + "/api/style/export", data=payload).json() == stored
    d.reply(0, d.pdf)
    open_style(p, {**stored, "first_indent_sp": 131072, "rest_indent_sp": 1})
    expect(first).to_have_value("2")
    assert Decimal(rest.input_value()) == Decimal("0.0000152587890625")
    assert save_style(p) == {**stored, "first_indent_sp": 131072, "rest_indent_sp": 1}


def test_readable_units_keep_the_precise_metric_request(review):
    d = review
    unit, first, rest = indent_controls(d)
    first.fill("25.4")
    rest.fill("3")
    unit.select_option("pt")
    expect(first).to_have_value("72.27")
    assert len(rest.input_value().split(".")[-1]) <= 6
    opened = save_style(d.page)
    open_style(d.page, opened)
    expect(first).to_have_value("72.27")
    assert len(rest.input_value().split(".")[-1]) <= 6
    first.fill("11")
    rest.fill("72.27")
    unit.select_option("mm")
    expect(first).to_have_value("3.866058")
    expect(rest).to_have_value("25.4")
    d.submit(d.refresh)
    mm = Decimal(d.requests[-1]["style"]["first_indent_mm"])
    assert abs(mm - Decimal(27940) / 7227) < Decimal("1e-18")
    d.reply(0, d.pdf)
    unit.select_option("pt")
    expect(first).to_have_value("11")
    stored = save_style(d.page)
    open_style(d.page, stored)
    expect(first).to_have_value("11")
    expect(rest).to_have_value("72.27")


def test_pt_half_sp_both_sides_and_exact_tie(review):
    unit, first, rest = indent_controls(review)
    unit.select_option("pt")
    first.fill("1.00000762939453124999999999999999999999999999999999999")
    rest.fill("1.00000762939453125")
    saved = save_style(review.page)
    assert (saved["first_indent_sp"], saved["rest_indent_sp"]) == (65536, 65537)
    first.fill("1.00000762939453125000000000000000000000000000000000001")
    assert save_style(review.page)["first_indent_sp"] == 65537


@pytest.mark.parametrize("value", ["", "1x", "-1e-100000", "1e1000000000"])
def test_invalid_pt_never_reuses_previous_value(review, value):
    p = review.page
    unit, first, _ = indent_controls(review)
    unit.select_option("pt")
    first.fill("11")
    first.fill(value)
    unit.select_option("mm")
    unit.select_option("pt")
    expect(first).to_have_value(value)
    with p.expect_response("**/api/style/export") as response:
        p.locator("#save-style").click()
    assert response.value.status == 422 and response.value.json()["field"] == "first_indent_mm"
    first.fill("1e-1000000000")
    assert save_style(p)["first_indent_sp"] == 0


def test_mirrored_conversion_with_one_sp_of_line_width_in_pt(review):
    p = review.page
    unit, first, _ = indent_controls(review)
    unit.select_option("pt")
    stored = save_style(p)
    stored.update(page_sp=[9 * 65536, stored["page_sp"][1]], gutter_sp=1,
                  margins_sp=[0, 1, 0, 2], first_indent_sp=9 * 65536 - 5)
    open_style(p, stored)
    value = Decimal(first.input_value())
    assert value * 65536 == stored["first_indent_sp"]
    mode = field(p, "여백 방식")
    switch(mode, "fixed")
    assert save_style(p)["first_indent_sp"] == stored["first_indent_sp"]
    switch(mode, "mirrored")
    assert save_style(p) == stored
    assert Decimal(first.input_value()) == value


def test_ai_and_reference_apply_in_pt_view(review):
    d, p = review, review.page
    unit, first, rest = indent_controls(d)
    unit.select_option("pt")
    rest.fill("+001.000")
    p.locator("#open-suggestion").scroll_into_view_if_needed()
    p.locator("#open-suggestion").click()
    p.route("**/api/style-suggestion", lambda r: r.fulfill(json={"patch": {"first_indent_mm": "25.4"}}))
    p.get_by_label("양식 변경 요청").fill("첫 줄 들여쓰기 변경")
    p.get_by_role("button", name="제안 받기").click()
    apply = p.get_by_role("button", name="제안 적용")
    expect(apply).to_be_visible()
    unit.select_option("mm")
    unit.select_option("pt")
    apply.click()
    expect(rest).to_have_value("+001.000")
    assert save_style(p)["first_indent_sp"] == 4736287
    d.open()
    d.analyze()
    d.choose()
    d.submit(p.locator("#reference-trial"))
    draft = d.requests[-1]["style"]
    assert draft["first_indent_mm"] == "25.4"
    d.reply(0, d.pdf)
    unit.select_option("mm")
    unit.select_option("pt")
    p.locator("#reference-apply").click()
    expect(rest).to_have_value("+001.000")
    assert save_style(p)["rest_indent_sp"] == 65536
