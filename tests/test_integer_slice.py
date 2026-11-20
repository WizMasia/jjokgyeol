"""Public HTTP and browser acceptance of fixed-point layout, using the PDF as the oracle."""

import hashlib

import httpx
import pytest
from playwright.sync_api import expect, sync_playwright

from settings_ui import close_settings, open_settings
from pdf_metrics import read_pages
from test_slice import render, server_url


@pytest.mark.parametrize("bounds", [
    "[69.99999999999999999999, 100]",
    "[100, 130.00000000000000000001]",
    '["100.004", "100.003"]',
])
def test_space_range_checked_before_rounding(server_url, bounds):
    # Send the decimal lexemes unchanged, including values a float would round into range.
    body = '{"source":"river","style":{"space_pct":' + bounds + '}}'
    response = httpx.post(server_url + "/api/render", content=body.encode(),
                          headers={"Content-Type": "application/json"}, timeout=45, trust_env=False)
    assert (response.status_code, response.headers["content-type"]) == (422, "application/json")
    assert response.json()["field"] == "space_pct"


@pytest.mark.parametrize("field", ["first_indent_mm", "rest_indent_mm"])
def test_each_line_needs_available_width(server_url, field):
    style = {"page_mm": [76, 85], "margins_mm": [12, 12, 12, 12], field: "53"}
    response = httpx.post(server_url + "/api/render", json={"source": "갈대", "style": style},
                          timeout=45, trust_env=False)
    assert response.status_code == 422 and response.json()["field"] == field


def test_negative_half_percent_step_in_print(server_url):
    word = "가" * 101

    def span(pct_literal):
        body = '{"source":"' + word + '","style":{"page_mm":[450,90],"ko_tracking_pct":' + pct_literal + '}}'
        response = httpx.post(server_url + "/api/render", content=body.encode(),
                              headers={"Content-Type": "application/json"}, timeout=45, trust_env=False)
        assert response.status_code == 200, response.text if response.is_error else ""
        page, = read_pages(response.content)
        line, = page.lines
        assert "".join(c.text for c in line.chars) == word
        return line.chars[-1].x0 - line.chars[0].x0

    # 100 gaps amplify a single 0.01% step to ~0.110 PDF pt at 11 TeX pt.
    rounded = span("-7.245")
    assert rounded == pytest.approx(span('"-7.25"'), abs=0.02)
    above_half = span("-7.24499999999999999999")
    assert above_half - rounded == pytest.approx(100 * 11 * 72 / 72.27 * 0.0001, abs=0.02)


def test_indented_paragraph_width_across_pages_with_note(server_url):
    long_word = "산" * 28
    words = (["갈대", "바람길", "River", "푸르다"] * 15 + [long_word, "물가표식"])
    source = " ".join(words) + "[^검측]\n\n[^검측]: 지면 확인용 각주."
    split_offsets = set()
    index = 0
    for word in words:
        if word == long_word:
            split_offsets.update(range(index + 1, index + len(word)))
        index += len(word)
        split_offsets.add(index)

    for first, rest in [(6, 0), (0, 6)]:
        data = render(server_url, source, {
            "page_mm": [76, 85], "margins_mm": [12, 12, 12, 12],
            "first_indent_mm": first, "rest_indent_mm": rest,
        })
        pages = read_pages(data)
        assert len(pages) > 1
        lines = []
        notes = []
        for page_no, page in enumerate(pages, 1):
            for line in page.lines:
                if "지면확인용각주." in "".join(line.text.split()):
                    notes.append((page_no, line.chars[-1].baseline))
                body = [c for c in line.chars if not c.text.isspace() and abs(c.size - 11 * 72 / 72.27) < 0.05]
                if body:
                    lines.append((page_no, body))

        printed = ""
        for number, (page_no, chars) in enumerate(lines):
            left_mm = 12 + (first if number == 0 else rest)
            assert chars[0].x0 == pytest.approx(left_mm * 72 / 25.4, abs=0.05), (number, page_no)
            assert chars[-1].x0 + chars[-1].advance <= (76 - 12) * 72 / 25.4 + 0.05
            printed += "".join(c.text for c in chars)
            assert len(printed) in split_offsets, f"어절 내부에서 줄바꿈: {printed[-20:]}"
        assert printed == "".join(words)
        ref_page, last_line = lines[-1]
        (note_page, note_y), = notes
        assert ref_page == note_page and note_y < last_line[0].baseline
        anchor = last_line[-1]
        marks = [c for line in pages[ref_page - 1].lines for c in line.chars
                 if c.text == "1" and c.size < anchor.size and c.baseline > anchor.baseline
                 and abs(c.x0 - (anchor.x0 + anchor.advance)) < 2]
        assert len(marks) == 1, "참조 단어 바로 뒤의 각주 번호"


def test_editor_exports_applied_integer_layout(server_url):
    with sync_playwright() as playwright, playwright.chromium.launch() as browser:
        page = browser.new_page()
        page.set_default_timeout(45000)
        with page.expect_response(lambda r: r.url.endswith("/api/render")):
            page.goto(server_url)
        source = ("강가 바람 River Walk " * 10) + "물가표식[^강]\n\n[^강]: 지면 확인용 각주."
        editor = page.get_by_role("textbox", name="마크다운 원고")
        editor.fill(source)
        open_settings(page)
        page.locator("#style summary").click()
        page.get_by_label("한글 자간").fill("-7.245")
        page.get_by_label("첫 줄 들여쓰기").fill("6")
        page.get_by_label("이후 줄 들여쓰기").fill("0")
        maximum = page.get_by_label("최대", exact=True)
        maximum.fill("130.00000000000000001")
        close_settings(page)
        with page.expect_response(lambda r: r.url.endswith("/api/render")) as rejected:
            page.get_by_role("button", name="미리보기 갱신").click()
        assert rejected.value.status == 422 and rejected.value.json()["field"] == "space_pct"
        open_settings(page)
        expect(maximum).to_have_value("130.00000000000000001")
        page.get_by_label("최소", exact=True).fill("100.005")
        maximum.fill("100.005")
        close_settings(page)
        with page.expect_download() as saved:
            with page.expect_response(lambda r: r.url.endswith("/api/render")) as result:
                page.get_by_role("button", name="PDF 다운로드").click()
            assert result.value.status == 200
        sent = result.value.request.post_data_json
        assert sent["source"] == source and sent["style"]["ko_tracking_pct"] == "-7.245"
        assert sent["style"]["space_pct"] == ["100.005", "100.005"]
        caption = page.locator("#applied-ratios")
        expect(caption).to_contain_text("한글 자간 -7.25%")
        expect(caption).to_contain_text("어절 공백 100.01% ~ 100.01%")
        expect(editor).to_have_value(source)
        pdf = saved.value.path().read_bytes()
        preview_hash = page.locator("iframe").evaluate("""async frame => {
            const bytes = await (await fetch(frame.src)).arrayBuffer();
            const hash = await crypto.subtle.digest('SHA-256', bytes);
            return Array.from(new Uint8Array(hash), n => n.toString(16).padStart(2, '0')).join('');
        }""")
        assert preview_hash == hashlib.sha256(pdf).hexdigest()
        page_pdf, = read_pages(pdf)
        body_rows = [line for line in page_pdf.lines if any(c.text == "강" for c in line.chars)]
        assert len(body_rows) >= 2
        assert body_rows[0].chars[0].x0 == pytest.approx(24 * 72 / 25.4, abs=0.05)
        assert body_rows[1].chars[0].x0 == pytest.approx(18 * 72 / 25.4, abs=0.05)
