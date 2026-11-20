"""Measure the operator's browser downloads against the immutable 14 mm reports."""
import hashlib
import json

import pytest
from playwright.sync_api import expect

from pdf_metrics import read_pages
from test_article_acceptance import save
from test_duplex_acceptance import LEGACY, PROOF as DUPLEX, reopen_file, source_candidate, write_json
from test_list_acceptance import download_proof
from test_reference_acceptance import blob_hash, candidate, commit_pdf, editor, only_unit, trial, upload, url
from test_running_heads_acceptance import compact, glyphs, regions


MM = 72 / 25.4
TOL = .04
FIXED = {
    "before": "40555cf7ff41b009957103d0e5407cdbb53d8bd1246d43d17eb545903eec3891",
    "after": "6627fb2fd790f17fe07f39e7a2ab9f6da4918d4fe63be657beaad83c584c29b5",
}
LEGACY_STYLE = "2ab28f8de113c7281af37e0fe7986bba12f443d89e8529236ab45f261c6ac17f"


def open_manuscript(page, version):
    manuscript = LEGACY / f"{version}.md"
    with page.expect_file_chooser() as picker:
        page.locator("#open").click()
    picker.value.set_files(manuscript)
    expect(page.locator("#source")).to_have_value(manuscript.read_text())
    return manuscript.read_bytes()


def open_book(page, version="before"):
    source = open_manuscript(page, version)
    page.locator("#style summary").click()
    path = DUPLEX / "style.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == LEGACY_STYLE
    reopen_file(page, path)
    return source


def measurements(pdf, version, odd_shift, even_shift):
    """Check every glyph on every line. Shifts are relative to the 14 mm control."""
    control = (DUPLEX / f"{version}.pdf").read_bytes()
    assert hashlib.sha256(control).hexdigest() == FIXED[version]
    pages = read_pages(pdf)
    assert len(pages) == (11 if version == "before" else 12)
    measured, hits = [], []
    for n, (p, b) in enumerate(zip(pages, read_pages(control), strict=True), 1):
        assert (p.width, p.height) == (b.width, b.height)
        shift = (odd_shift if n % 2 else even_shift) * MM
        x_error = body_y_error = 0
        track = None
        body, head, foot = regions(p)
        body_positions = set(body)
        for line, old in zip(p.lines, b.lines, strict=True):
            assert line.text == old.text, (version, n)
            for c, prior in zip(line.chars, old.chars, strict=True):
                assert (c.text, c.font.split("+")[-1]) == (prior.text, prior.font.split("+")[-1])
                assert (c.size, c.advance) == pytest.approx((prior.size, prior.advance), abs=.001, rel=0)
                y_error = max(abs(c.y0 - prior.y0), abs(c.y1 - prior.y1), abs(c.baseline - prior.baseline))
                assert y_error <= TOL, (version, n, c.text, y_error)
                if c in body_positions:
                    body_y_error = max(body_y_error, y_error)
                x_error = max(x_error, abs(c.x0 - prior.x0 - shift), abs(c.x1 - prior.x1 - shift))
            if "추적문장" in compact(line.chars):
                track = next(c.x0 for c in line.chars if c.text == "추")
                assert track == pytest.approx(39.685 + shift, abs=TOL)
                hits.append(n)
        assert x_error <= TOL and body_y_error <= .001, (version, n, x_error, body_y_error)
        assert compact(foot) == (str(n) if n > 1 else "")
        edge = (foot[-1].x1 if n % 2 else foot[0].x0) if foot else None
        if edge is not None:
            assert edge == pytest.approx((323.150 if n % 2 else 39.685) + shift, abs=TOL)
        lines = [line for line in p.lines if line.chars[0] in body_positions]
        notes = [line for line in lines if max(c.size for c in line.chars) <= 8.1]
        measured.append({"page": n, "body_note_lines": len(lines), "body_note_glyphs": len(body),
                         "note_lines": len(notes), "header": compact(head), "folio": compact(foot),
                         "folio_edge_pt": round(edge, 4) if edge is not None else None,
                         "tracked_x0_pt": round(track, 4) if track is not None else None,
                         "shift_mm": odd_shift if n % 2 else even_shift,
                         "max_x_error_pt": round(x_error, 6), "max_body_note_y_error_pt": round(body_y_error, 6)})
    assert hits == [9 if version == "before" else 10]
    assert measured[-1]["note_lines"] == measured[-1]["body_note_lines"] > 0
    return measured


def mode_control(page):
    mode = page.get_by_role("combobox", name="여백 방식", exact=True)
    expect(mode).to_be_visible()
    return mode


def set_mirrored(page):
    mode = mode_control(page)
    expect(mode).to_have_value("fixed")
    mode.select_option("mirrored")
    inner = page.get_by_label("안쪽", exact=True)
    outer = page.get_by_label("바깥쪽", exact=True)
    gutter = page.get_by_label("제본 여백", exact=True)
    expect(inner).to_have_value("14")
    expect(outer).to_have_value("14")
    expect(gutter).to_have_value("0")
    inner.fill("12")
    outer.fill("10")
    gutter.fill("6")
    expect(page.locator("#margin-summary")).to_contain_text("실제 안쪽 18 mm")
    expect(page.locator("#margin-summary")).to_contain_text("본문 폭 100 mm")
    return gutter


@pytest.mark.parametrize("version", ["before", "after"])
def test_fixed_report_matches_existing_proof(editor, tmp_path, version):
    open_book(editor, version)
    pdf = download_proof(editor, tmp_path / f"fixed-{version}.pdf")
    measurements(pdf, version, 0, 0)
    assert json.loads(save(editor, "#save-style")) == json.loads((DUPLEX / "style.json").read_bytes())


def test_browser_mirrors_both_manuscripts_and_reopens(editor, tmp_path):
    before_source = open_book(editor)
    set_mirrored(editor)
    book = {}
    for version in ("before", "after"):
        source = before_source if version == "before" else open_manuscript(editor, version)
        pdf = download_proof(editor, tmp_path / f"{version}.pdf")
        rows = measurements(pdf, version, 4, -4)
        book[version] = {"source_sha256": hashlib.sha256(source).hexdigest(),
                         "baseline_sha256": FIXED[version], "pages": rows,
                         "download_sha256": hashlib.sha256(pdf).hexdigest(),
                         "preview_sha256": blob_hash(editor.locator("#preview"), "src")}
        assert save(editor, "#save") == source
    saved = save(editor, "#save-style")
    (tmp_path / "style.json").write_bytes(saved)
    expected = json.loads((DUPLEX / "style.json").read_bytes())
    assert json.loads(saved) == {**expected, "margins_sp": [2797020, 1864680, 2797020, 2237616], "gutter_sp": 1118808}
    reopen_file(editor, DUPLEX / "style.json")
    measurements(download_proof(editor, tmp_path / "fixed-reopened.pdf"), "after", 0, 0)
    reopen_file(editor, tmp_path / "style.json")
    reopened = download_proof(editor, tmp_path / "mirror-reopened.pdf")
    assert measurements(reopened, "after", 4, -4) == book["after"]["pages"]
    assert json.loads(save(editor, "#save-style")) == json.loads(saved)
    write_json(tmp_path / "book.json", book)


def test_asymmetric_fixed_and_explicit_zero_gutter_reopen_differ_on_even_pages(editor, tmp_path):
    open_book(editor)
    mode = mode_control(editor)
    editor.get_by_label("왼쪽", exact=True).fill("18")
    editor.get_by_label("오른쪽", exact=True).fill("10")
    fixed = tmp_path / "fixed.json"
    fixed.write_bytes(save(editor, "#save-style"))
    fields = json.loads(fixed.read_bytes())
    assert "gutter_sp" not in fields

    mode.select_option("mirrored")
    expect(editor.get_by_label("제본 여백", exact=True)).to_have_value("0")
    zero = tmp_path / "zero.json"
    zero.write_bytes(save(editor, "#save-style"))
    assert json.loads(zero.read_bytes()) == {**fields, "gutter_sp": 0}
    for path, expected_mode, shift in ((fixed, "fixed", 4), (zero, "mirrored", -4)):
        reopen_file(editor, path)
        expect(mode).to_have_value(expected_mode)
        pdf = download_proof(editor, path.with_suffix(".pdf"))
        measurements(pdf, "before", 4, shift)
        assert json.loads(save(editor, "#save-style")) == json.loads(path.read_bytes())


def test_gutter_that_consumes_body_width_preserves_last_preview(editor, tmp_path):
    source = open_book(editor)
    gutter = set_mirrored(editor)
    normal = download_proof(editor, tmp_path / "valid.pdf")
    shown = editor.locator("#preview")
    valid_hash = hashlib.sha256(normal).hexdigest()
    gutter.fill("106")  # 128 mm sheet minus 12 mm inside and 10 mm outside
    for button, endpoint in (("#refresh", "render"), ("#save-style", "style/export"), ("#export", "render")):
        with editor.expect_response(lambda r: r.url.endswith("/api/" + endpoint)) as denied:
            editor.locator(button).click()
        error = denied.value.json()
        assert denied.value.status == 422 and error["field"] == "gutter_mm", error
        expect(gutter).to_have_attribute("aria-invalid", "true")
        expect(gutter).to_have_accessible_description(error["message"])
        assert blob_hash(shown, "src") == valid_hash
    gutter.fill("6")
    measurements(download_proof(editor, tmp_path / "recovered.pdf"), "before", 4, -4)
    assert save(editor, "#save") == source


def test_reference_margins_are_fixed_only_and_header_keeps_mirrored_width(editor, tmp_path):
    source = open_book(editor)
    gutter = set_mirrored(editor)
    previous = download_proof(editor, tmp_path / "mirrored.pdf")
    units = upload(editor, "banks.docx", directory=DUPLEX)["units"]
    only_unit(editor, 0)
    physical = candidate(editor, "document", "margins_mm")
    expect(physical.get_by_role("checkbox")).to_be_disabled()
    expect(physical).to_contain_text("고정 좌우 여백")
    header, _ = source_candidate(editor, units, 0, "header", "left", "odd")
    header.get_by_label("제안 값", exact=True).fill("참고홀수")
    pdf = commit_pdf(editor, trial(editor), tmp_path / "mirrored-header.pdf")
    for i, (page, original) in enumerate(zip(read_pages(pdf), read_pages(previous), strict=True), 1):
        body, head, foot = regions(page)
        old_body, _, old_foot = regions(original)
        assert glyphs(body) == glyphs(old_body) and glyphs(foot) == glyphs(old_foot)
        if i == 3:
            assert compact(head) == "참고홀수" and head[0].x0 == pytest.approx(18 * MM, abs=TOL)
    expect(gutter).to_have_value("6")

    mode_control(editor).select_option("fixed")
    upload(editor, "banks.docx", directory=DUPLEX)
    only_unit(editor, 0)
    physical.get_by_role("checkbox").check()
    physical.get_by_label("제안 값", exact=True).fill("15, 11, 15, 17")
    trial(editor)
    editor.locator("#reference-cancel").click()
    assert blob_hash(editor.locator("#preview"), "src") == hashlib.sha256(pdf).hexdigest()
    mode_control(editor).select_option("mirrored")
    expect(gutter).to_have_value("6")
    mode_control(editor).select_option("fixed")

    upload(editor, "banks.docx", directory=DUPLEX)
    only_unit(editor, 0)
    physical.get_by_role("checkbox").check()
    physical.get_by_label("제안 값", exact=True).fill("15, 11, 15, 17")
    commit_pdf(editor, trial(editor), tmp_path / "reference-fixed.pdf")
    expect(editor.get_by_label("왼쪽", exact=True)).to_have_value("17")
    expect(editor.get_by_label("오른쪽", exact=True)).to_have_value("11")
    mode_control(editor).select_option("mirrored")
    expect(gutter).to_have_value("0")
    expect(editor.get_by_label("안쪽", exact=True)).to_have_value("17")
    expect(editor.get_by_label("바깥쪽", exact=True)).to_have_value("11")
    assert save(editor, "#save") == source
