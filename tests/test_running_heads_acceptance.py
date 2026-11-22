"""Host-side acceptance with fixed manuscripts, pre-feature PDFs and a real service."""
import hashlib
import json
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import expect

from settings_ui import open_settings, close_settings

from pdf_metrics import read_pages
from test_reference_acceptance import save, download_proof, blob_hash, commit_pdf, editor, only_unit, trial, upload, url
from test_slice import render


PROOF = Path(__file__).resolve().parents[1] / "proofs/running-heads"
BASE = {"page_mm": [128, 100], "margins_mm": [15, 14, 15, 14], "body_pt": 11, "leading_pt": 15}
MM = 72 / 25.4


def rule(path, role, **values):
    return {"path": path, "role": role, "values": values}


STYLE = {**BASE, "section_rules": [
    rule([], "header", visible=True, left="연안기록", center="2026", right="{{현재쪽}}", font_pt=9, offset_mm=6),
    rule([], "footer", visible=True, center="－ {현재쪽} －", font_pt=9, offset_mm=6),
    rule(["표지"], "header", visible=False),
    rule(["표지"], "footer", visible=False),
    rule(["부록"], "header", left="부록기록"),
    rule(["판독표"], "header", left="판독표기록"),
]}


def regions(page):
    body, header, footer = [], [], []
    for row in page.lines:
        for c in row.chars:
            mid = (c.y0 + c.y1) / 2
            area = header if mid > page.height - 15 * MM else footer if mid < 15 * MM else body
            area.append(c)
    return body, sorted(header, key=lambda c: c.x0), sorted(footer, key=lambda c: c.x0)


def glyphs(chars):
    # Subset prefixes can change when new glyphs are embedded in the same font.
    return sorted((c.text, c.font.split("+")[-1], c.size, c.x0, c.y0, c.x1, c.y1, c.baseline, c.advance)
                  for c in chars)


def compact(chars):
    return "".join(c.text for c in chars if not c.text.isspace())


def check_book(pdf, version, enabled):
    expected = json.loads((PROOF / "expected.json").read_bytes())[version]
    control = (PROOF / f"baseline-{version}.pdf").read_bytes()
    assert hashlib.sha256(control).hexdigest() == expected["baseline_sha256"]
    assert hashlib.sha256((PROOF / f"{version}.md").read_bytes()).hexdigest() == expected["manuscript_sha256"]
    pages = read_pages(pdf)
    for page, baseline, row in zip(pages, read_pages(control), expected["pages"], strict=True):
        content, header, footer = regions(page)
        old, old_header, old_footer = regions(baseline)
        assert not old_header and not old_footer
        assert (baseline.width, baseline.height) == (page.width, page.height)
        assert glyphs(content) == glyphs(old), (version, row["page"])
        body_lines = [line for line in page.lines if line.chars[0] in content]
        main = [line for line in body_lines if max(c.size for c in line.chars) > 8.1]
        notes = [line for line in body_lines if max(c.size for c in line.chars) <= 8.1]
        assert (compact(main[0].chars) if main else None) == row["first_body"]
        assert (round(main[0].chars[0].baseline, 3) if main else None) == row["first_body_y"]
        assert len(notes) == row["note_lines"]
        assert (round(notes[-1].chars[-1].baseline, 3) if notes else None) == row["last_note_y"]
        assert compact(header) == (row["header"] if enabled else ""), (version, row)
        assert compact(footer) == (row["footer"] if enabled else ""), (version, row)
        if enabled and row["header"]:
            assert header[0].x0 == pytest.approx(14 * MM, abs=.04)
            assert header[-1].x1 == pytest.approx(page.width - 14 * MM, abs=.04)
            assert (footer[0].x0 + footer[-1].x1) / 2 == pytest.approx(page.width / 2, abs=.04)
            assert all(abs(c.size - 9*72/72.27) < .001 for c in header + footer)
    hits = [i for i, page in enumerate(pages, 1) if any("추적문장" in l.text for l in page.lines)]
    assert hits == [9 if version == "before" else 10]


@pytest.mark.parametrize("version", ["before", "after"])
def test_body_footnotes_running_regions_and_physical_numbers(url, version, tmp_path):
    source = (PROOF / f"{version}.md").read_text()
    check_book(render(url, source, BASE), version, False)
    result = render(url, source, STYLE)
    check_book(result, version, True)
    (tmp_path / f"{version}.pdf").write_bytes(result)


def test_fence_setext_and_footnote_keep_the_pre_feature_layout(url):
    source = (Path(__file__).parent / "fixtures/running-heads-fence.md").read_text()
    old, = read_pages((PROOF / "baseline-fence.pdf").read_bytes())
    for patch, label in ((BASE, ""), (STYLE, "연안기록2026{현재쪽}")):
        page, = read_pages(render(url, source, patch))
        body, head, foot = regions(page)
        assert glyphs(body) == glyphs(regions(old)[0]) and compact(head) == label
    inner = {**BASE, "section_rules": [
        {**r, "values": {**r["values"], "offset_mm": 9}} for r in STYLE["section_rules"][:2]
    ]}
    adjusted, = read_pages(render(url, source, inner))
    unchanged, top, bottom = regions(adjusted)
    assert glyphs(unchanged) == glyphs(body)
    assert head[0].baseline - top[0].baseline == pytest.approx(3 * MM, abs=.002)
    assert bottom[0].baseline - foot[0].baseline == pytest.approx(3 * MM, abs=.002)


def open_style(page, raw):
    with page.expect_response(lambda r: r.url.endswith("/api/style/open")) as loaded:
        page.locator("#style-file").set_input_files({"name": "연안양식.json", "mimeType": "application/json", "buffer": raw})
    assert loaded.value.ok
    expect(page.locator("#save-style")).to_be_enabled()


def setup_book(page, url, version):
    source = (PROOF / f"{version}.md").read_text()
    page.locator("#file").set_input_files(PROOF / f"{version}.md")
    expect(page.locator("#source")).to_have_value(source)
    open_settings(page)
    page.locator("#style summary").click()
    response = httpx.post(url + "/api/style/export", json=BASE, trust_env=False)
    response.raise_for_status()
    open_style(page, response.content)
    return source, response.content


def area(page, path, role):
    page.locator("#running-target").select_option(json.dumps(path, ensure_ascii=False, separators=(",", ":")))
    page.locator("#running-role").select_option(role)


def set_text(page, key, value):
    page.locator("#running-own-" + key).check()
    page.locator("#running-" + key).fill(value)


def test_browser_authors_rules_and_reopens_layout(editor, url, tmp_path):
    source, legacy = setup_book(editor, url, "before")
    baseline = download_proof(editor, tmp_path / "unmarked.pdf")
    check_book(baseline, "before", False)
    assert editor.locator("#open-running").count() == 1, "running-area editor absent"
    open_settings(editor)
    editor.locator("#open-running").click()
    for entry in STYLE["section_rules"]:
        area(editor, entry["path"], entry["role"])
        for key, value in entry["values"].items():
            if key == "visible":
                editor.locator("#running-visible").select_option("show" if value else "hide")
            else:
                set_text(editor, key, str(value))
    result = commit_pdf(editor, trial(editor), tmp_path / "before.pdf")
    check_book(result, "before", True)
    assert save(editor, "#save") == source.encode()
    saved = save(editor, "#save-style")
    (tmp_path / "style.json").write_bytes(saved)
    assert json.loads(saved)["section_rules"]
    editor.locator("#file").set_input_files(PROOF / "after.md")
    expect(editor.locator("#source")).to_have_value((PROOF / "after.md").read_text())
    check_book(download_proof(editor, tmp_path / "after.pdf"), "after", True)
    open_style(editor, legacy)
    check_book(download_proof(editor, tmp_path / "after-off.pdf"), "after", False)
    open_style(editor, saved)
    check_book(download_proof(editor, tmp_path / "after-reopened.pdf"), "after", True)
    assert json.loads(save(editor, "#save-style")) == json.loads(saved)
    open_settings(editor)
    editor.locator("#open-running").click()
    area(editor, ["부록"], "header")
    set_text(editor, "center", "")
    cleared = commit_pdf(editor, trial(editor), tmp_path / "explicit-empty.pdf")
    assert compact(regions(read_pages(cleared)[10])[1]) == "부록기록{현재쪽}"
    open_settings(editor)
    editor.locator("#open-running").click()
    area(editor, ["부록"], "header")
    editor.locator("#running-own-center").uncheck()
    expect(editor.locator("#running-center")).to_have_value("2026")
    restored = commit_pdf(editor, trial(editor), tmp_path / "restored.pdf")
    check_book(restored, "after", True)


def test_page_ten_rejects_collision_and_preserves_committed_preview(editor, url, tmp_path):
    source, _ = setup_book(editor, url, "before")
    close_settings(editor)
    editor.locator("#source").fill(source.split("추적문장", 1)[0])
    assert editor.locator("#open-running").count() == 1
    open_settings(editor)
    editor.locator("#open-running").click()
    area(editor, [], "footer")
    editor.locator("#running-visible").select_option("show")
    # At 10 TeX pt 29 NanumMyeongjo syllables leave 8.94 PDF pt of 100 mm.
    # One digit is 5.35 PDF pt and two take 10.70.
    for k, v in {"left": "나" * 29, "right": "{현재쪽}", "font_pt": "10", "offset_mm": "6"}.items():
        set_text(editor, k, v)
    valid = commit_pdf(editor, trial(editor), tmp_path / "fits-nine.pdf")
    pages = read_pages(valid)
    assert len(pages) == 9 and compact(regions(pages[-1])[2]) == "나" * 29 + "9"
    old_hash = hashlib.sha256(valid).hexdigest()
    old_style = save(editor, "#save-style")
    close_settings(editor)
    editor.locator("#source").fill(source)
    open_settings(editor)
    editor.locator("#open-running").click()
    for action in ("#reference-trial", "#export"):
        (open_settings if action == "#reference-trial" else close_settings)(editor)
        with editor.expect_response(lambda r: r.url.endswith("/api/render")) as bad:
            editor.locator(action).click()
        assert bad.value.status == 422
        assert "10쪽" in bad.value.json()["message"] and "바닥글" in bad.value.json()["message"]
        assert blob_hash(editor.locator("#preview"), "src") == old_hash
        if action == "#reference-trial":
            expect(editor.locator("#reference-apply")).to_be_disabled()
            editor.locator("#reference-cancel").click()
    assert save(editor, "#save-style") == old_style
    assert save(editor, "#save") == source.encode()


def slot(units, role, key):
    for i, u in enumerate(units):
        for c in u["candidates"]:
            if c["role"] == role and c["key"] == key and c["basis"] != "unsupported":
                return i, c
    pytest.fail(f"{role}.{key} missing from {[(u['location'], u['section']) for u in units]}")


@pytest.mark.parametrize("name", ["reference.docx", "reference.hwpx", "reference.pdf", "scan.png"])
def test_import_edit_running_text_without_reference_body(editor, name, tmp_path):
    source = "# 항구\n\n해안선에 부표를 띄웠다."
    close_settings(editor)
    editor.locator("#source").fill(source)
    open_settings(editor)
    if name == "reference.docx":
        editor.locator("#open-running").click()
        area(editor, [], "footer")
        editor.locator("#running-visible").select_option("show")
        set_text(editor, "center", "제{현재쪽}면")
        initial = commit_pdf(editor, trial(editor), tmp_path / "own-footer.pdf")
        assert compact(regions(read_pages(initial)[0])[2]) == "제1면"
    raw = (PROOF / name).read_bytes()
    result = upload(editor, name, directory=PROOF)
    assert not result["warnings"], result["warnings"]
    units = result["units"]
    i, head = slot(units, "header", "left")
    j, foot = slot(units, "footer", "center")
    if name == "reference.docx":
        groups = {c["variant"]: c["source_group"] for c in units[i]["candidates"]
                  if (c["role"], c["key"], c["value"]) == ("header", "left", "COAST 2026")}
        assert groups["first"] != groups["default"]
    if name != "scan.png":
        assert "COAST2026" == "".join(head["value"].split())
        assert "{현재쪽}" in foot["value"] and "17" not in foot["value"]
    else:
        expect(editor.locator("#reference-model")).to_contain_text("google/gemma-4-e2b")
        assert "COAST" in head["value"].upper() or "2026" in head["value"]
        assert foot["basis"] == "confirm"
        choices = {o["kind"]: o["value"] for o in foot["options"]}
        assert "17" in choices["literal"] and "{현재쪽}" in choices["page"]

    # Each selected unit keeps only the author's two chosen pieces of text.
    assert i == j
    only_unit(editor, i)
    def row(c):
        return editor.locator(f'[data-role="{c["role"]}"][data-key="{c["key"]}"]').filter(
            has_text=c["source_group"]).first
    row(head).get_by_role("checkbox").check()
    row(head).get_by_label("적용할 면", exact=True).select_option("default")
    expect(editor.locator("#running-visible")).to_have_value("show")
    if head.get("options"):
        assert any(o["kind"] == "literal" and "2026" in o["value"] for o in head["options"])
        interpretation = row(head).get_by_label("번호 해석")
        expect(interpretation).to_have_value("")
        editor.locator("#reference-trial").click()
        message = editor.locator("#reference-message")
        expect(message).to_contain_text("번호 해석")
        print(head["role"], head["key"], repr(head["value"]), "before literal selection:", message.inner_text())
        interpretation.select_option("literal")
    editor.locator("#running-left").fill("파도기록실")
    if name != "reference.docx":
        row(foot).get_by_role("checkbox").check()
        row(foot).get_by_label("적용할 면", exact=True).select_option("default")
        if name == "scan.png":
            interpretation = row(foot).get_by_label("번호 해석")
            expect(interpretation).to_have_value("")
            editor.locator("#reference-trial").click()
            expect(editor.locator("#reference-message")).to_contain_text("번호 해석")
            expect(editor.locator("#reference-apply")).to_be_disabled()
            interpretation.select_option("page")
            print(foot["role"], foot["key"], repr(foot["value"]), "page:", choices["page"])
        editor.locator("#running-center").fill("제{현재쪽}면")
    if name == "reference.pdf":
        with editor.expect_response(lambda r: "/api/reference/analyze?" in r.url) as retry:
            editor.get_by_role("button", name=f"{i+1}쪽 다시 분석").click()
        assert retry.value.ok and f"page={i+1}" in retry.value.url
        editor.wait_for_function("() => window.referenceReplies.length === 2")
        expect(editor.locator("#reference-analyze")).to_be_enabled()
        for keep in editor.get_by_role("button", name="기존 선택 유지").all():
            keep.click()
        expect(editor.locator("#running-center")).to_have_value("제{현재쪽}면")
    pdf = commit_pdf(editor, trial(editor), tmp_path / "from-reference.pdf")
    page, = read_pages(pdf)
    body, header, footer = regions(page)
    assert compact(header) == "파도기록실" and compact(footer) == "제1면"
    assert "해안선" in compact(body)
    saved = json.loads(save(editor, "#save-style"))
    assert {r["role"]:r for r in saved["section_rules"]} == {
        "header": rule([], "header", visible=True, left="파도기록실"),
        "footer": rule([], "footer", visible=True, center="제{현재쪽}면")}
    serialized = json.dumps(saved, ensure_ascii=False)
    assert all(secret not in serialized + compact(body) for secret in ("ORBITONLY", "COAST", "잔별수첩", "data:image/", "source_group"))
    assert save(editor, "#save") == source.encode() and (PROOF / name).read_bytes() == raw
    evidence = {
        "file": name, "sha256": hashlib.sha256(raw).hexdigest(), "location": units[i]["location"],
        "warnings": result["warnings"],
        "candidates": [c for c in units[i]["candidates"] if c["role"] in {"header", "footer"}
                       and c["key"] in {"left", "center", "right"}],
        "printed_header": compact(header), "printed_footer": compact(footer),
        "download_sha256": hashlib.sha256(pdf).hexdigest(),
    }
    (tmp_path / "reference-result.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
