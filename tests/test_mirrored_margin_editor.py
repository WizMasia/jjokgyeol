"""Mirrored margins through the editing, proof and style-file interfaces."""
import json

import pytest
from playwright.sync_api import expect

from test_editor import browser
from test_reference_editor import candidate, review, unit
from test_slice import server_url


def controls(d):
    p = d.page
    p.locator("#style summary").click()
    mode = p.get_by_label("여백 방식", exact=True)
    expect(mode).to_have_value("fixed", timeout=1200)
    return p, mode


def field(p, label):
    return p.get_by_label(label, exact=True)


def switch(mode, value):
    mode.select_option(value)
    expect(mode).to_have_value(value)


def save_style(p):
    with p.expect_download() as saved:
        p.locator("#save-style").click()
    return json.loads(saved.value.path().read_bytes())


def open_style(p, stored):
    with p.expect_response("**/api/style/open") as loaded:
        p.locator("#style-file").set_input_files({"name": "책.json", "mimeType": "application/json",
                                                  "buffer": json.dumps(stored).encode()})
    assert loaded.value.ok
    expect(p.locator("#save-style")).to_be_enabled()


def test_toggle_restore_and_reopen(review):
    d = review
    p, mode = controls(d)
    field(p, "왼쪽").fill("12")
    field(p, "오른쪽").fill("10")
    legacy = save_style(p)
    assert "gutter_sp" not in legacy
    mode.select_option("mirrored")
    expect(field(p, "안쪽")).to_have_value("12")
    expect(field(p, "바깥쪽")).to_have_value("10")
    gutter = field(p, "제본 여백")
    expect(gutter).to_have_value("0")
    gutter.fill("6")
    expect(p.locator("#margin-summary")).to_contain_text("실제 안쪽 18 mm")
    expect(p.locator("#margin-summary")).to_contain_text("본문 폭 120 mm")
    d.submit(d.refresh)
    style = d.requests[-1]["style"]
    assert style["gutter_mm"] == "6" and style["margins_mm"] == ["20", "10", "20", "12"]
    d.reply(0, d.pdf)
    mirrored = save_style(p)
    assert mirrored == {**legacy, "gutter_sp": 1118808}
    switch(mode, "fixed")
    expect(field(p, "왼쪽")).to_have_value("18")
    expect(gutter).to_be_hidden()
    expect(p.locator("#status")).to_contain_text("갱신 필요")
    assert "gutter_sp" not in save_style(p)
    field(p, "위").fill("21")
    field(p, "왼쪽").fill("1.80e1")
    field(p, "오른쪽").fill("1.0e1")
    p.locator("#file").set_input_files({"name": "다음.md", "mimeType": "text/markdown", "buffer": b"# next"})
    expect(d.source).to_have_value("# next")
    mode.select_option("mirrored")
    expect(field(p, "안쪽")).to_have_value("12")
    expect(field(p, "바깥쪽")).to_have_value("10")
    expect(gutter).to_have_value("6")
    expect(field(p, "위")).to_have_value("21")
    switch(mode, "fixed")
    field(p, "오른쪽").fill("9")
    mode.select_option("mirrored")
    expect(field(p, "안쪽")).to_have_value("18")
    expect(field(p, "바깥쪽")).to_have_value("9")
    expect(gutter).to_have_value("0")
    zero = save_style(p)
    assert zero["gutter_sp"] == 0
    open_style(p, mirrored)
    expect(gutter).to_have_value("6")
    switch(mode, "fixed")
    same_left = save_style(p)
    open_style(p, same_left)
    mode.select_option("mirrored")
    expect(gutter).to_have_value("0")
    open_style(p, zero)
    expect(mode).to_have_value("mirrored")
    assert save_style(p) == zero
    open_style(p, legacy)
    expect(mode).to_have_value("fixed")
    assert save_style(p) == legacy
    expect(d.source).to_have_value("# next")


def test_decimal_and_last_sp_roundtrip(review):
    p, mode = controls(review)
    legacy = save_style(p)
    mode.select_option("mirrored")
    field(p, "안쪽").fill("0.100000000000000000001")
    field(p, "제본 여백").fill("2e-1")
    switch(mode, "fixed")
    assert save_style(p)["margins_sp"][3] == 55941
    mode.select_option("mirrored")
    expect(field(p, "안쪽")).to_have_value("0.100000000000000000001")
    expect(field(p, "제본 여백")).to_have_value("2e-1")
    narrow = {**legacy, "page_sp": [21416810, legacy["page_sp"][1]],
              "margins_sp": [0, 7713205, 0, 8071449], "rest_indent_sp": 5632154, "gutter_sp": 1}
    open_style(p, narrow)
    assert save_style(p) == narrow
    switch(mode, "fixed")
    mode.select_option("mirrored")
    assert save_style(p) == narrow


@pytest.mark.parametrize(("width", "inner", "gutter", "fixed_sp"), [
    ("28.002001", "12.001", "6.001", 3356796),
    ("128", "12.1", "6.1", 3393718),
])
def test_toggle_to_fixed_preserves_rounded_width(review, width, inner, gutter, fixed_sp):
    p, mode = controls(review)
    mode.select_option("mirrored")
    field(p, "폭").fill(width)
    field(p, "안쪽").fill(inner)
    field(p, "바깥쪽").fill("10")
    field(p, "제본 여백").fill(gutter)
    mirrored = save_style(p)
    assert mirrored["margins_sp"][3] + mirrored["gutter_sp"] == fixed_sp
    if width == "28.002001":
        assert mirrored["page_sp"][0] - mirrored["margins_sp"][1] - fixed_sp == 1
    open_style(p, mirrored)
    switch(mode, "fixed")
    fixed = save_style(p)
    assert "gutter_sp" not in fixed
    assert fixed["margins_sp"] == [mirrored["margins_sp"][0], mirrored["margins_sp"][1],
                                   mirrored["margins_sp"][2], fixed_sp]
    assert fixed["page_sp"] == mirrored["page_sp"]
    mode.select_option("mirrored")
    assert save_style(p) == mirrored


def test_toggling_a_raw_one_sp_draft_preserves_its_valid_geometry(review):
    p, mode = controls(review)
    mode.select_option("mirrored")
    field(p, "폭").fill("27.4664288")
    field(p, "안쪽").fill("8.398946")
    field(p, "바깥쪽").fill("6.240822")
    field(p, "제본 여백").fill("12.826659")
    mirrored = save_style(p)
    assert mirrored["page_sp"][0] - sum(mirrored["margins_sp"][i] for i in (1, 3)) - mirrored["gutter_sp"] == 1
    switch(mode, "fixed")
    fixed = save_style(p)
    assert fixed["page_sp"] == mirrored["page_sp"]
    assert fixed["margins_sp"][:3] == mirrored["margins_sp"][:3]
    assert fixed["margins_sp"][3] == mirrored["margins_sp"][3] + mirrored["gutter_sp"]
    field(p, "위").fill("21")
    mode.select_option("mirrored")
    expect(field(p, "폭")).to_have_value("27.4664288")
    expect(field(p, "위")).to_have_value("21")
    expect(field(p, "제본 여백")).to_have_value("12.826659")
    assert save_style(p)["gutter_sp"] == mirrored["gutter_sp"]


def test_late_fixed_conversion_cannot_overwrite_an_opened_style(review):
    d = review
    p, mode = controls(d)
    field(p, "왼쪽").fill("12")
    legacy = save_style(p)
    mode.select_option("mirrored")
    field(p, "제본 여백").fill("6")
    mirrored = save_style(p)
    held = []

    def export(route):
        if not held:
            held.append(route)
        else:
            route.fallback()

    p.route("**/api/style/export", export)
    with p.expect_request("**/api/style/export"):
        mode.select_option("fixed")
    expect(d.refresh).to_be_disabled()
    replacement = {**legacy, "margins_sp": [*legacy["margins_sp"][:3], 2610552]}
    open_style(p, replacement)
    expect(field(p, "왼쪽")).to_have_value("14")
    # Drain the mode switch's follow-up validation before the test closes its context.
    with p.expect_response("**/api/style/open") as late, p.expect_response("**/api/style/export") as checked:
        held[0].fulfill(json=mirrored)
    assert late.value.ok
    assert checked.value.ok
    assert save_style(p) == replacement
    expect(mode).to_have_value("fixed")
    expect(field(p, "왼쪽")).to_have_value("14")
    p.unroute_all(behavior="wait")


@pytest.mark.parametrize("scoped", [False, True], ids=["global-indent", "reference-indent"])
def test_restore_split_after_indent_leaves_one_scaled_point(review, scoped):
    d = review
    p, mode = controls(d)
    mode.select_option("mirrored")
    field(p, "폭").fill("128")
    field(p, "안쪽").fill("12.001")
    field(p, "바깥쪽").fill("10")
    field(p, "제본 여백").fill("6.001")
    split = save_style(p)
    switch(mode, "fixed")
    if scoped:
        row = candidate("body", "rest_indent_mm", "99.9980005")
        p.route("**/api/reference/analyze?*", lambda r: r.fulfill(json={"units": [unit(1, "본문", row)], "warnings": []}))
        d.open()
        p.locator("#reference-file").set_input_files({"name": "들여쓰기.docx", "mimeType": "application/octet-stream",
                                                      "buffer": b"x"})
        p.locator("#reference-analyze").click()
        d.row("body", "rest_indent_mm").get_by_role("checkbox").check()
        d.submit(p.locator("#reference-trial"))
        d.reply(0, d.pdf)
        p.locator("#reference-apply").click()
    else:
        field(p, "이후 줄 들여쓰기").fill("99.9980005")
    fixed = save_style(p)
    switch(mode, "mirrored")
    restored = save_style(p)
    expected = {**fixed, "margins_sp": split["margins_sp"], "gutter_sp": split["gutter_sp"]}
    assert restored == expected
    indent = restored["section_rules"][0]["values"]["rest_indent_sp"] if scoped else restored["rest_indent_sp"]
    assert restored["page_sp"][0] - restored["margins_sp"][1] - restored["margins_sp"][3] - restored["gutter_sp"] - indent == 1
    d.submit(d.refresh)
    rendered = p.request.post(d.service + "/api/render", data=d.requests[-1])
    assert rendered.ok, rendered.text()
    pdf = rendered.body()
    assert pdf.startswith(b"%PDF")
    d.reply(len(d.pending) - 1, pdf)
    expect(p.locator("#status")).to_have_text("최신 버전")


def test_invalid_body_size_does_not_block_fixed_margin_switch(review):
    d = review
    p, mode = controls(d)
    old_pdf = d.frame.get_attribute("src")
    field(p, "왼쪽").fill("12")
    mode.select_option("mirrored")
    field(p, "제본 여백").fill("6")
    body = p.locator("#body_pt")
    body.fill("0")
    switch(mode, "fixed")
    expect(field(p, "왼쪽")).to_have_value("18")
    expect(body).to_have_value("0")
    with p.expect_response("**/api/style/export") as denied:
        p.locator("#save-style").click()
    assert denied.value.status == 422 and denied.value.json()["field"] == "body_pt"
    assert d.frame.get_attribute("src") == old_pdf
    body.fill("11")
    assert "gutter_sp" not in save_style(p)


def test_reference_physical_margins(review):
    d = review
    p, mode = controls(d)
    field(p, "왼쪽").fill("12")
    mode.select_option("mirrored")
    field(p, "제본 여백").fill("6")
    switch(mode, "fixed")
    rows = [candidate("document", "margins_mm", [21, 9, 21, 14]), candidate("body", "font_pt", "12")]
    p.route("**/api/reference/analyze?*", lambda r: r.fulfill(json={"units": [unit(1, "본문", *rows)], "warnings": []}))
    def analyze():
        d.open()
        p.locator("#reference-file").set_input_files({"name": "여백.docx", "mimeType": "application/octet-stream", "buffer": b"x"})
        p.locator("#reference-analyze").click()
    analyze()
    physical = d.row("document", "margins_mm")
    physical.get_by_role("checkbox").check()
    mode.select_option("mirrored")
    expect(physical.get_by_role("checkbox")).to_be_disabled()
    expect(physical.get_by_role("checkbox")).not_to_be_checked()
    expect(physical).to_contain_text("고정 좌우 여백")
    d.row("body", "font_pt").get_by_role("checkbox").check()
    d.submit(p.locator("#reference-trial"))
    style = d.requests[-1]["style"]
    assert style["gutter_mm"] == "6" and style["margins_mm"] == ["20", "18", "20", "12"]
    assert style["section_rules"][0]["values"] == {"font_pt": "12"}
    d.reply(0, d.pdf)
    p.locator("#reference-apply").click()
    switch(mode, "fixed")
    analyze()
    physical.get_by_role("checkbox").check()
    previous = d.frame.get_attribute("src")
    d.submit(p.locator("#reference-trial"))
    d.reply(1, d.pdf)
    p.locator("#reference-cancel").click()
    assert d.frame.get_attribute("src") == previous
    mode.select_option("mirrored")
    expect(field(p, "제본 여백")).to_have_value("6")
    switch(mode, "fixed")
    analyze()
    physical.get_by_role("checkbox").check()
    d.submit(p.locator("#reference-trial"))
    d.reply(2, d.pdf)
    p.locator("#reference-apply").click()
    mode.select_option("mirrored")
    expect(field(p, "안쪽")).to_have_value("14")
    expect(field(p, "바깥쪽")).to_have_value("9")
    expect(field(p, "제본 여백")).to_have_value("0")


def test_invalid_gutter_preserves_preview(review):
    d = review
    p, mode = controls(d)
    source = d.source.input_value()
    old = d.frame.get_attribute("src")
    mode.select_option("mirrored")
    gutter = field(p, "제본 여백")
    gutter.fill("-1")
    d.submit(d.refresh)
    d.reply(0, {"field": "gutter_mm", "message": "제본 여백은 0 이상이어야 합니다."}, 422)
    expect(gutter).to_have_attribute("aria-invalid", "true")
    expect(gutter).to_have_accessible_description("제본 여백은 0 이상이어야 합니다.")
    gutter.fill("-2")
    with p.expect_response("**/api/style/export") as denied:
        p.locator("#save-style").click()
    assert denied.value.status == 422
    err = denied.value.json()
    assert err["field"] == "gutter_mm"
    expect(gutter).to_have_accessible_description(err["message"])
    expect(gutter).to_have_attribute("aria-invalid", "true")
    assert d.frame.get_attribute("src") == old
    assert d.frame.evaluate("async e => [...new Uint8Array(await (await fetch(e.src)).arrayBuffer())]") == list(d.pdf)
    expect(d.source).to_have_value(source)
    gutter.fill("0")
    expect(gutter).not_to_have_attribute("aria-invalid", "true")
    assert save_style(p)["gutter_sp"] == 0
