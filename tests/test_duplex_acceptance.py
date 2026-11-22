"""Host acceptance for duplex pages, measured against the shipped single-sided PDFs."""
import hashlib
import json

import pytest
from playwright.sync_api import expect

from settings_ui import open_settings, close_settings
from pdf_metrics import read_pages
from test_reference_acceptance import (save, download_proof, blob_hash, commit_pdf, editor, only_unit, trial, upload, url)
from test_running_heads_acceptance import (
    BASE, PROOF as LEGACY, STYLE, area, check_book, compact, glyphs, open_style,
    regions, rule, set_text, setup_book as _setup_book,
)
from test_slice import render


PROOF = LEGACY.parent / "duplex-running-heads"
LEFT, RIGHT = 39.685, 323.150  # 14 and 114 mm, in PDF points
SINGLE_SHA256 = {
    "before": "a20626c1e0349ddcf0c2bd4bb7d72a9661579abff2193cd8a0d9072fa2e449bc",
    "after": "96e4feb4274a0ce7fadacb127a68c01c0e89346bf19361df0a142888d893fbcf",
}


def face_rule(path, role, face, **values):
    return {**rule(path, role, **values), "face": face}


RULES = [
    rule([], "header", visible=True, offset_mm=6),
    rule([], "footer", visible=True, center="{현재쪽}", offset_mm=6),
    face_rule([], "header", "odd", left="홀수연안"),
    face_rule([], "header", "even", left="짝수연안"),
    face_rule([], "header", "first", left="표지연안"),
    face_rule([], "footer", "odd", center="", right="{현재쪽}"),
    face_rule([], "footer", "even", center="", left="{현재쪽}"),
    face_rule([], "footer", "first", visible=False),
    face_rule(["부록"], "header", "first", visible=False),
    face_rule(["판독표"], "header", "first", left="첫원고면"),
]


def measure_book(pdf, version):
    pages = read_pages(pdf)
    control = (PROOF / f"single-{version}.pdf").read_bytes()
    assert hashlib.sha256(control).hexdigest() == SINGLE_SHA256[version]
    baseline = read_pages(control)
    appendix = 10 if version == "before" else 11
    assert len(pages) == len(baseline) == appendix + 1
    measured = []
    for n, (page, old) in enumerate(zip(pages, baseline, strict=True), 1):
        body, head, foot = regions(page)
        assert (page.width, page.height) == (old.width, old.height)
        old_body = regions(old)[0]
        old_positions = set(old_body)
        old_lines = [line for line in old.lines if line.chars[0] in old_positions]
        body_set = set(body)
        lines = [line for line in page.lines if line.chars[0] in body_set]
        assert [glyphs(line.chars) for line in lines] == [glyphs(line.chars) for line in old_lines], (version, n)
        assert glyphs(body) == glyphs(old_body), (version, n)
        main = [line for line in lines if max(c.size for c in line.chars) > 8.1]
        header = "표지연안" if n == 1 else "" if n == appendix else "홀수연안" if n % 2 else "짝수연안"
        assert compact(head) == header, (version, n)
        if head:
            assert head[0].x0 == pytest.approx(LEFT, abs=.04)
        assert compact(foot) == ("" if n == 1 else str(n)), (version, n)
        if n != 1:
            assert (foot[-1].x1 if n % 2 else foot[0].x0) == pytest.approx(
                RIGHT if n % 2 else LEFT, abs=.04), (version, n)
        measured.append({"page": n, "header": compact(head), "footer": compact(foot),
                         "x0": round(foot[0].x0, 4) if foot else None,
                         "x1": round(foot[-1].x1, 4) if foot else None,
                         "first_body": main[0].text if main else None,
                         "note_lines": len(lines) - len(main),
                         "body_note_glyphs": len(body)})
    tracked = [n for n, page in enumerate(pages, 1) if any("추적문장" in line.text for line in page.lines)]
    assert tracked == [appendix - 1]
    return measured


@pytest.mark.parametrize("version", ["before", "after"])
def test_physical_outside_number_and_unchanged_body_notes(url, version, tmp_path):
    source = (LEGACY / f"{version}.md").read_text()
    baseline = render(url, source, STYLE)
    check_book(baseline, version, True)
    (tmp_path / f"single-{version}.pdf").write_bytes(baseline)
    measure_book(render(url, source, {**BASE, "section_rules": RULES}), version)


def test_nested_first_faces_follow_first_body_lines_across_siblings(url):
    source = ("갯마을 안내\n\n# 포구\n\n## 물때표\n\n" + "갯벌조사 깊은 갯골을 따라 발자국을 기록했다.\n\n" * 28
              + "## 염전\n\n" + "소금조사 해질녘 소금을 걷어 무게를 기록했다.\n\n" * 24)
    rules = [
        rule([], "header", visible=True, offset_mm=6),
        face_rule([], "header", "odd", left="홀수"),
        face_rule([], "header", "even", left="짝수"),
        face_rule(["포구"], "header", "first", left="포구첫면", center="상위"),
        face_rule(["포구", "물때표"], "header", "first", center="자료첫면"),
        face_rule(["포구", "염전"], "header", "first", center="자료첫면"),
    ]
    pages = read_pages(render(url, source, {**BASE, "section_rules": rules}))
    assert [compact(regions(p)[0])[:4] for p in pages] == ["갯마을안", "갯벌조사", "갯벌조사", "갯벌조사", "소금조사", "소금조사"]
    assert [compact(regions(p)[1]) for p in pages] == ["홀수", "포구첫면자료첫면", "홀수", "짝수", "홀수자료첫면", "짝수"]


def setup_book(page, url, version):
    result = _setup_book(page, url, version)
    if page.locator("#style details").first.get_attribute("open") is None:
        page.locator("#style summary").click()
    return result


def write_rules(page, rules):
    open_settings(page)
    page.locator("#open-running").click()
    expect(page.locator("#running-face")).to_be_visible()
    for entry in rules:
        area(page, entry["path"], entry["role"])
        page.locator("#running-face").select_option(entry.get("face", "default"))
        for key, value in entry["values"].items():
            if key == "visible":
                page.locator("#running-visible").select_option("show" if value else "hide")
            else:
                set_text(page, key, str(value))


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def reopen_file(page, path):
    open_settings(page)
    details = page.locator("#style details").first
    if details.get_attribute("open") is None:
        details.locator("summary").click()
    with page.expect_file_chooser() as chooser:
        page.locator("#open-style").click()
    with page.expect_response(lambda r: r.url.endswith("/api/style/open")) as response:
        chooser.value.set_files(path)
    assert response.value.ok
    expect(page.locator("#save-style")).to_be_enabled()


def test_browser_authors_duplex_spreads_and_reopens_saved_style(editor, url, tmp_path):
    source, _ = setup_book(editor, url, "before")
    write_rules(editor, RULES)
    before = commit_pdf(editor, trial(editor), tmp_path / "before.pdf")
    book = {"before": measure_book(before, "before")}
    assert save(editor, "#save") == source.encode()
    saved = save(editor, "#save-style")
    (tmp_path / "style.json").write_bytes(saved)
    stored = json.loads(saved)["section_rules"]
    assert len(stored) == len(RULES)
    for entry in RULES:
        actual, = [r for r in stored if (r["path"], r["role"], r.get("face")) ==
                   (entry["path"], entry["role"], entry.get("face"))]
        values = dict(entry["values"])
        if "offset_mm" in values:
            del values["offset_mm"]
            values["offset_sp"] = 1118808  # six millimetres, rounded to integer TeX sp
        assert actual["values"] == values
    close_settings(editor)
    with editor.expect_file_chooser() as chooser:
        editor.locator("#open").click()
    chooser.value.set_files(LEGACY / "after.md")
    expect(editor.locator("#source")).to_have_value((LEGACY / "after.md").read_text())
    after = download_proof(editor, tmp_path / "after.pdf")
    book["after"] = measure_book(after, "after")
    open_style(editor, (LEGACY / "style.json").read_bytes())
    check_book(download_proof(editor, tmp_path / "single-reopened.pdf"), "after", True)
    assert json.loads(save(editor, "#save-style")) == json.loads((LEGACY / "style.json").read_bytes())
    reopen_file(editor, tmp_path / "style.json")
    assert measure_book(download_proof(editor, tmp_path / "reopened.pdf"), "after") == book["after"]
    assert json.loads(save(editor, "#save-style")) == json.loads(saved)
    write_json(tmp_path / "pages.json", book)


def test_collision_only_on_two_digit_even_side_keeps_download(editor, url, tmp_path):
    source, _ = setup_book(editor, url, "before")
    close_settings(editor)
    editor.locator("#source").fill(source.split("추적문장", 1)[0])
    write_rules(editor, [
        rule([], "footer", visible=True, font_pt=10, offset_mm=6),
        face_rule([], "footer", "even", left="{현재쪽}", right="나" * 29),
        face_rule([], "footer", "odd", right="{현재쪽}"),
    ])
    valid = commit_pdf(editor, trial(editor), tmp_path / "fits.pdf")
    pages = read_pages(valid)
    assert len(pages) == 9
    assert compact(regions(pages[7])[2]) == "8" + "나" * 29
    assert compact(regions(pages[8])[2]) == "9"
    saved = save(editor, "#save-style")
    close_settings(editor)
    editor.locator("#source").fill(source)
    open_settings(editor)
    editor.locator("#open-running").click()
    with editor.expect_response(lambda r: r.url.endswith("/api/render")) as failed:
        editor.locator("#reference-trial").click()
    assert failed.value.status == 422
    message = failed.value.json()["message"]
    assert "10쪽" in message and "바닥글" in message
    expect(editor.locator("#reference-message")).to_contain_text("10쪽")
    expect(editor.locator("#reference-apply")).to_be_disabled()
    assert blob_hash(editor.locator("#preview"), "src") == hashlib.sha256(valid).hexdigest()
    editor.locator("#reference-cancel").click()
    assert save(editor, "#save-style") == saved
    close_settings(editor)
    editor.locator("#source").fill(source.split("추적문장", 1)[0])
    restored = download_proof(editor, tmp_path / "restored.pdf")
    assert read_pages(restored) == pages


def source_candidate(page, units, index, role, key, face):
    found, = [c for c in units[index]["candidates"] if c["role"] == role and c["key"] == key
              and c["suggested_face"] == face]
    block = page.locator(".reference-unit").filter(has=page.get_by_role(
        "heading", name=units[index]["section"] + " · " + units[index]["location"], exact=True))
    row = block.locator(f'[data-role="{role}"][data-key="{key}"][data-variant="{found["variant"]}"]'
                        f'[data-source-group="{found["source_group"]}"]')
    row.get_by_role("checkbox").check()
    expect(row.get_by_label("적용할 면", exact=True)).to_have_value(face or "")
    return row, found


def finish_import(page, directory, origin, original, candidates):
    pdf = commit_pdf(page, trial(page), directory / "from-reference.pdf")
    saved = save(page, "#save-style")
    (directory / "imported.json").write_bytes(saved)
    assert origin.read_bytes() == original and save(page, "#save") == (LEGACY / "before.md").read_bytes()
    assert "source_group" not in saved.decode() and "ORBITONLY" not in saved.decode()
    pages = read_pages(pdf)
    old = read_pages((PROOF / "single-before.pdf").read_bytes())
    for printed, prior in zip(pages, old, strict=True):
        assert glyphs(regions(printed)[0]) == glyphs(regions(prior)[0])
    reopen_file(page, LEGACY / "style.json")
    check_book(download_proof(page, directory / "single.pdf"), "before", True)
    reopen_file(page, directory / "imported.json")
    reopened = download_proof(page, directory / "reopened.pdf")
    assert read_pages(reopened) == pages
    assert json.loads(save(page, "#save-style")) == json.loads(saved)
    write_json(directory / "reading.json", {"input": origin.name, "input_sha256": hashlib.sha256(original).hexdigest(),
               "candidates": candidates, "download_sha256": hashlib.sha256(pdf).hexdigest(),
               "reopened_download_sha256": hashlib.sha256(reopened).hexdigest(),
               "printed": [[compact(a) for a in regions(p)[1:]] for p in pages]})
    return pages, json.loads(saved)["section_rules"]


def test_docx_empty_cover_and_two_headers_applied_together(editor, url, tmp_path):
    setup_book(editor, url, "before")
    # Keep the existing common centred number on the cover and put page 2 outside.
    write_rules(editor, [rule([], "footer", visible=True, center="{현재쪽}", offset_mm=6),
                         face_rule([], "footer", "even", center="", left="{현재쪽}")])
    initial = read_pages(commit_pdf(editor, trial(editor), tmp_path / "numbered-cover.pdf"))
    number = regions(initial[0])[2]
    assert compact(number) == "1"
    assert (number[0].x0 + number[-1].x1) / 2 == pytest.approx(initial[0].width / 2, abs=.04)
    origin = PROOF / "banks.docx"
    raw = origin.read_bytes()
    open_settings(editor)
    result = upload(editor, origin.name, directory=PROOF)
    assert not result["warnings"]
    only_unit(editor, 0)
    chosen = []
    for side, old, new in [("odd", "East bank", "동녘연안"), ("even", "West bank", "서녘연안")]:
        row, c = source_candidate(editor, result["units"], 0, "header", "left", side)
        assert c["value"] == old and c["basis"] == "read"
        row.get_by_label("제안 값", exact=True).fill(new)
        chosen.append(c)
    _, hide = source_candidate(editor, result["units"], 0, "footer", "visible", "first")
    assert hide["value"] is False and hide["basis"] == "read"
    pages, rules = finish_import(editor, tmp_path, origin, raw, [*chosen, hide])
    assert face_rule([], "footer", "first", visible=False) in rules
    assert not regions(pages[0])[2]
    for n, page in enumerate(pages, 1):
        _, head, foot = regions(page)
        assert compact(head) == ("동녘연안" if n % 2 else "서녘연안")
        if n % 2 == 0:
            assert compact(foot) == str(n) and foot[0].x0 == pytest.approx(LEFT, abs=.04)


def test_hwpx_both_outside_controls_keep_distinct_faces(editor, url, tmp_path):
    setup_book(editor, url, "before")
    origin = PROOF / "outer.hwpx"
    raw = origin.read_bytes()
    open_settings(editor)
    result = upload(editor, origin.name, directory=PROOF)
    assert not result["warnings"]
    only_unit(editor, 0)
    chosen = []
    for side, slot in [("odd", "right"), ("even", "left")]:
        row, c = source_candidate(editor, result["units"], 0, "footer", slot, side)
        assert c["basis"] == "read" and c["value"] == "{현재쪽}"
        row.get_by_label("제안 값", exact=True).fill("항{현재쪽}")
        chosen.append(c)
    pages, _ = finish_import(editor, tmp_path, origin, raw, chosen)
    for n, page in enumerate(pages, 1):
        foot = regions(page)[2]
        assert compact(foot) == f"항{n}"
        assert (foot[-1].x1 if n % 2 else foot[0].x0) == pytest.approx(RIGHT if n % 2 else LEFT, abs=.04)


def test_text_pdf_reassigns_face_and_keeps_edit_through_retry(editor, url, tmp_path):
    setup_book(editor, url, "before")
    origin = PROOF / "banks.pdf"
    raw = origin.read_bytes()
    open_settings(editor)
    result = upload(editor, origin.name, directory=PROOF)
    assert not result["warnings"] and len(result["units"]) == 6
    only_unit(editor, 0)
    row, c = source_candidate(editor, result["units"], 0, "header", "left", "odd")
    assert c["value"] == "East bank" and "추정" in c["evidence"]
    face = row.get_by_label("적용할 면", exact=True)
    face.select_option("even")
    row.get_by_label("제안 값", exact=True).fill("다시읽은연안")
    with editor.expect_response(lambda r: "/api/reference/analyze?" in r.url and "page=1" in r.url) as response:
        editor.get_by_role("button", name="1쪽 다시 분석").click()
    assert response.value.ok
    editor.wait_for_function("() => window.referenceReplies.length === 2")
    expect(editor.locator("#reference-analyze")).to_be_enabled()
    for keep in row.get_by_role("button", name="기존 선택 유지").all():
        keep.click()
    expect(face).to_have_value("even")
    expect(row.get_by_label("제안 값", exact=True)).to_have_value("다시읽은연안")
    pages, rules = finish_import(editor, tmp_path, origin, raw, [c])
    assert rules == [face_rule([], "header", "even", left="다시읽은연안", visible=True)]
    for n, page in enumerate(pages, 1):
        assert compact(regions(page)[1]) == ("" if n % 2 else "다시읽은연안")


def test_real_scan_requires_separate_face_and_number_choices(editor, url, tmp_path):
    setup_book(editor, url, "before")
    origin = LEGACY / "scan.png"
    raw = origin.read_bytes()
    open_settings(editor)
    result = upload(editor, origin.name, directory=LEGACY)
    assert not result["warnings"]
    expect(editor.locator("#reference-model")).to_contain_text("google/gemma-4-e2b")
    row, c = source_candidate(editor, result["units"], 0, "footer", "center", None)
    assert c["variant"] == "unknown" and c["basis"] == "confirm"
    options = {o["kind"]: o["value"] for o in c["options"]}
    assert "17" in options["literal"] and "{현재쪽}" in options["page"]
    editor.locator("#reference-trial").click()
    expect(editor.locator("#reference-message")).to_contain_text("적용할 면")
    row.get_by_label("적용할 면", exact=True).select_option("odd")
    editor.locator("#reference-trial").click()
    expect(editor.locator("#reference-message")).to_contain_text("번호 해석")
    row.get_by_label("번호 해석").select_option("page")
    row.get_by_label("제안 값", exact=True).fill("홀{현재쪽}면")
    pages, rules = finish_import(editor, tmp_path, origin, raw, [c])
    assert rules == [face_rule([], "footer", "odd", center="홀{현재쪽}면", visible=True)]
    for n, page in enumerate(pages, 1):
        assert compact(regions(page)[2]) == (f"홀{n}면" if n % 2 else "")
