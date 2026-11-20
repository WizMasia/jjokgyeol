"""Edit a manuscript in Chromium and inspect the immutable release service's PDFs."""

import hashlib
import json
import re
from dataclasses import asdict

import pytest
from playwright.sync_api import expect

from app.style import Style
from pdf_metrics import read_pages
from test_list_acceptance import download_proof, rows, tab
from test_refactor_acceptance import compact_json, image_id, stock


# A distinct first item identifies its parent heading even when both numbers change.
SECTIONS = {
    "목적": ("# 제8조(목적)\n\n7. 솔바람\n   - 안쪽기록\n8. 풀잎\n\n본문 제8조의2와 {번호고정} 인용."
           "\n\n4. # 제70조 참고 {번호고정}",
           ["①솔바람", "1.안쪽기록", "②풀잎", "본문제8조의2와{번호고정}인용.", "④제70조참고{번호고정}"]),
    "시행": ("## 제2조 시행\n\n7. 햇살", ["①햇살"]),
    "정의": ("### 제8조 정의\n\n7. 이슬", ["①이슬"]),
    "둘": ("## 제8조의2(가지둘)\n\n7. 물길", ["①물길"]),
    "다섯": ("## 제8조의5 가지다섯\n\n7. 산길", ["①산길"]),
    "발췌": ("## 제31조의4 발췌\n\n7. 달빛", ["①달빛"]),
}
FRONT = "# 문서\n\n## 제9조문서 {번호고정}"
FRONT_ROWS = ["문서", "제9조문서{번호고정}"]
PLAIN = "# 제8조(*목적*)\n\n7. 솔바람\n8. 푸른잎\n\n본문 제8조의2를 인용한다."
# Captured through Chromium on the last service release before article numbering.
DISABLED_GEOMETRY = "9b6b955f385ae169fe4a35bea2c125a5f6200b91d7cf259e637d2a6578207111"


def manuscript(order):
    return "\n\n".join([FRONT, *(SECTIONS[key][0] for key in order)])


def printed_lines(pdf):
    return ["".join(c.text for c in chars) for _, chars in rows(pdf)]


def check_sections(tab, path, headings):
    expected = FRONT_ROWS.copy()
    for key, heading in headings:
        expected.extend([heading, *SECTIONS[key][1]])
    pdf = download_proof(tab, path)
    assert printed_lines(pdf) == expected


def enable_numbering(tab):
    if tab.locator("#article-style").is_hidden():
        tab.locator("#legal-options").click()
    switch = tab.get_by_label("조 제목 자동 번호", exact=True)
    assert switch.count() == 1, "real editor must offer 조 제목 자동 번호"
    switch.check()
    expect(tab.get_by_label("조 번호 표현식", exact=True)).to_have_value("제{숫자}조")
    return switch


def test_browser_insert_and_move_articles(tab, tmp_path):
    enable_numbering(tab)
    editor = tab.locator("#source")
    for stage, headings in (
        ("initial", [("목적", "제1조(목적)"), ("시행", "제2조시행")]),
        ("branches", [("목적", "제1조(목적)"), ("둘", "제1조의2(가지둘)"),
                      ("다섯", "제1조의5가지다섯"), ("시행", "제2조시행")]),
        ("insert", [("목적", "제1조(목적)"), ("둘", "제1조의2(가지둘)"), ("정의", "제2조정의"),
                    ("다섯", "제2조의5가지다섯"), ("시행", "제3조시행")]),
        ("move", [("시행", "제1조시행"), ("목적", "제2조(목적)"), ("둘", "제2조의2(가지둘)"),
                  ("정의", "제3조정의"), ("다섯", "제3조의5가지다섯")]),
    ):
        source = manuscript(key for key, _ in headings)
        editor.fill(source)
        check_sections(tab, tmp_path / f"{stage}.pdf", headings)
        expect(editor).to_have_value(source)


def toggle_pin(tab, heading):
    editor = tab.locator("#source")
    before = editor.input_value()
    editor.focus()
    editor.evaluate("""(el, line) => {
        const start = el.value.indexOf(line);
        if (start < 0) throw new Error(line);
        el.setSelectionRange(start + 3, start + 3);
    }""", heading)
    editor.press("ArrowRight")
    tab.get_by_role("button", name="번호 고정/해제", exact=True).click()
    expect(editor).not_to_have_value(before)


def save(tab, button):
    with tab.expect_download() as downloaded:
        tab.locator(button).click()
    return downloaded.value.path().read_bytes()


def test_browser_unique_base_after_branches_and_pin_roundtrip(tab, tmp_path):
    enable_numbering(tab)
    editor = tab.locator("#source")
    original = [("목적", "제1조(목적)"), ("둘", "제1조의2(가지둘)"), ("다섯", "제1조의5가지다섯"),
                ("시행", "제2조시행"), ("발췌", "제31조의4발췌")]
    editor.fill(manuscript(key for key, _ in original))
    check_sections(tab, tmp_path / "before-base-move.pdf", original)
    order = ["둘", "다섯", "시행", "목적", "발췌"]
    moved = manuscript(order)
    editor.fill(moved)
    check_sections(tab, tmp_path / "base-behind-branches.pdf", [
        ("둘", "제2조의2(가지둘)"), ("다섯", "제2조의5가지다섯"), ("시행", "제1조시행"),
        ("목적", "제2조(목적)"), ("발췌", "제31조의4발췌")])
    toggle_pin(tab, "## 제8조의5")
    check_sections(tab, tmp_path / "branch-pinned.pdf", [
        ("둘", "제2조의2(가지둘)"), ("다섯", "제8조의5가지다섯"), ("시행", "제1조시행"),
        ("목적", "제2조(목적)"), ("발췌", "제31조의4발췌")])
    toggle_pin(tab, "## 제2조 시행")
    expected = moved.replace("조의5 가지다섯\n", "조의5 가지다섯 {번호고정}\n").replace(
        "제2조 시행\n", "제2조 시행 {번호고정}\n")
    expect(editor).to_have_value(expected)
    pinned = [("둘", "제3조의2(가지둘)"), ("다섯", "제8조의5가지다섯"), ("시행", "제2조시행"),
              ("목적", "제3조(목적)"), ("발췌", "제31조의4발췌")]
    check_sections(tab, tmp_path / "pinned.pdf", pinned)
    raw = save(tab, "#save")
    assert raw == expected.encode()
    tab.get_by_label("조 제목 자동 번호", exact=True).uncheck()
    off = printed_lines(download_proof(tab, tmp_path / "pinned-off.pdf"))
    assert off == [*FRONT_ROWS, "제8조의2(가지둘)", "⑦물길", "제8조의5가지다섯", "⑦산길",
                   "제2조시행", "⑦햇살", "제8조(목적)", "⑦솔바람", "1.안쪽기록", "⑧풀잎",
                   "본문제8조의2와{번호고정}인용.", "④제70조참고{번호고정}", "제31조의4발췌", "⑦달빛"]
    editor.fill("새 원고")
    tab.locator("#file").set_input_files({"name": "pinned.md", "mimeType": "text/markdown", "buffer": raw})
    expect(editor).to_have_value(expected)
    enable_numbering(tab)
    check_sections(tab, tmp_path / "reopened.pdf", pinned)
    # Move the saved five-branch section past its base, keeping the actual bytes of its pin.
    saved_branch = SECTIONS["다섯"][0].replace("가지다섯\n", "가지다섯 {번호고정}\n")
    rearranged = editor.input_value().replace(saved_branch + "\n\n", "") + "\n\n" + saved_branch
    editor.fill(rearranged)
    check_sections(tab, tmp_path / "moved-pin.pdf", [pinned[i] for i in (0, 2, 3, 4, 1)])
    toggle_pin(tab, "## 제8조의5")
    toggle_pin(tab, "## 제2조 시행")
    expect(editor).to_have_value(manuscript(["둘", "시행", "목적", "발췌", "다섯"]))
    check_sections(tab, tmp_path / "unpinned.pdf", [
        ("둘", "제2조의2(가지둘)"), ("시행", "제1조시행"), ("목적", "제2조(목적)"),
        ("발췌", "제31조의4발췌"), ("다섯", "제2조의5가지다섯")])


def test_custom_expression_error_and_disabled_geometry(tab, tmp_path):
    # This signature belongs to the fixed style shipped before article numbering.
    with tab.expect_response(lambda reply: reply.url.endswith("/api/style/open")) as opened:
        tab.locator("#style-file").set_input_files({
            "name": "기존 양식.json", "mimeType": "application/json", "buffer": compact_json(Style().to_stored())})
    assert opened.value.ok
    tab.locator("#source").fill(PLAIN)
    old = download_proof(tab, tmp_path / "disabled.pdf")
    assert printed_lines(old) == ["제8조(목적)", "⑦솔바람", "⑧푸른잎", "본문제8조의2를인용한다."]
    geometry = hashlib.sha256(compact_json([asdict(p) for p in read_pages(old)])).hexdigest()
    assert geometry == DISABLED_GEOMETRY
    legacy = save(tab, "#save-style")
    assert "article_expr" not in json.loads(legacy)
    switch = enable_numbering(tab)
    expr = tab.get_by_label("조 번호 표현식", exact=True)
    expr.fill("[조 {한글}]")
    custom = download_proof(tab, tmp_path / "custom.pdf")
    assert printed_lines(custom) == ["[조가](목적)", "①솔바람", "②푸른잎", "본문제8조의2를인용한다."]
    # Removing the italic span narrows the bounding boxes of these two glyphs.
    for pdf in (old, custom):
        title_chars = read_pages(pdf)[0].lines[0].chars
        assert [c.x1 - c.x0 for c in title_chars if c.text in "목적"] == pytest.approx(
            [19.989045, 19.989045], abs=0.001)
    encoded = save(tab, "#save-style")
    assert json.loads(encoded) == {**json.loads(legacy), "article_expr": "[조 {한글}]"}
    expr.fill("")
    empty = download_proof(tab, tmp_path / "empty-expression.pdf")
    assert printed_lines(empty) == ["(목적)", "①솔바람", "②푸른잎", "본문제8조의2를인용한다."]
    assert json.loads(save(tab, "#save-style"))["article_expr"] == ""
    previous = tab.locator("#preview").get_attribute("src")
    expr.fill("{틀린표현식}")
    tab.locator("#refresh").click()
    expect(expr).to_have_attribute("aria-invalid", "true")
    expect(expr).to_have_accessible_description(re.compile("[가-힣]"))
    assert tab.locator("#preview").get_attribute("src") == previous
    assert tab.locator("#preview").evaluate("async el => (await (await fetch(el.src)).blob()).size") == len(empty)
    for raw, enabled in ((legacy, False), (encoded, True)):
        tab.locator("#style-file").set_input_files({
            "name": "style.json", "mimeType": "application/json", "buffer": raw})
        expect(switch).to_be_checked(checked=enabled)
        if enabled:
            expect(expr).to_have_value("[조 {한글}]")
        reopened = download_proof(tab, tmp_path / f"style-reopen-{enabled}.pdf")
        assert read_pages(reopened) == read_pages(custom if enabled else old)
        assert json.loads(save(tab, "#save-style")) == json.loads(raw)
    expr.fill("조")
    tab.locator("#source").fill("# 제8조 단서\n\n# 제9조   단서")
    spaces = rows(download_proof(tab, tmp_path / "heading-spaces.pdf"))
    gaps = []
    for _, chars in spaces:
        assert "".join(c.text for c in chars) == "조단서"
        gaps.append(chars[1].x0 - (chars[0].x0 + chars[0].advance))
    one, three = gaps
    print(f"heading gap in PDF pt for one/three spaces: {one}, {three}")
    assert one > 0 and three == pytest.approx(3 * one, abs=0.03)
