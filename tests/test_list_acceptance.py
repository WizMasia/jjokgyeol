"""Real list output on a host-selected immutable service image."""

import hashlib
import json
import re
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import expect, sync_playwright

from proof_response import selected_pdf
from pdf_metrics import read_pages
from test_refactor_acceptance import image_id, stock, style_request
from test_slice import render


PROOF = Path(__file__).resolve().parents[1] / "proofs/lists/manuscript.md"
# Short body prefixes identify the item independently of its generated marker.
ITEMS = ["편집자는", "기록의제목", "자료의출처", "사진은", "원본파일", "다시찍은",
         "교정자는원고", "수정한항목", "다음교정자는", "확인이끝난"]
LEGAL = ["①", "1.", "2.", "가.", "(1)", "(가)", "②", "1.", "①", "1."]
REPORT = ["ㅁ", "ㅇ", "ㅇ", "-", "-", "-", "ㅁ", "ㅇ", "ㅁ", "ㅇ"]
MM = 72 / 25.4  # PDF points, independent of the app's TeX conversions.


def rows(pdf):
    # PDFMiner can split the marker column and the body into separate text boxes.
    result = []
    for number, page in enumerate(read_pages(pdf), 1):
        baselines = {}
        for line in page.lines:
            for c in line.chars:
                if not c.text.isspace():
                    baselines.setdefault(round(c.baseline, 3), []).append(c)
        for y in sorted(baselines, reverse=True):
            result.append((number, sorted(baselines[y], key=lambda c: c.x0)))
    return result


def match_item(lines, body):
    matches = []
    for i, (_, chars) in enumerate(lines):
        text = "".join(c.text for c in chars)
        if body in text:
            start = text.index(body)
            matches.append((i, chars[:start], chars[start]))
    assert len(matches) == 1, (body, len(matches))
    return matches[0]


def proof_geometry(pdf, markers):
    lines = rows(pdf)
    first_x = []
    for body, expected in zip(ITEMS, markers, strict=True):
        i, label, origin = match_item(lines, body)
        assert "".join(c.text for c in label) == expected, body
        first_x.append(origin.x0)
        if body == ITEMS[0]:
            assert lines[i + 1][1][0].x0 == pytest.approx(origin.x0, abs=0.03)
    assert first_x[0] < first_x[1] < first_x[3] < first_x[4] < first_x[5]
    assert first_x[1] == pytest.approx(first_x[2], abs=0.03)
    assert first_x[0] == pytest.approx(first_x[6], abs=0.03)
    for title in ("제1조", "제2조"):
        _, prefix, head = match_item(lines, title)
        assert not prefix and "Bold" in head.font
    if markers == REPORT:
        step = first_x[3] - first_x[2]
        assert first_x[4] - first_x[3] == pytest.approx(step, abs=0.03)
        assert first_x[5] - first_x[4] == pytest.approx(step, abs=0.03)
    print(f"{markers[:2]} body origins {first_x[:6]}, pages {lines[-1][0]}")


def test_default_five_levels_and_article_resets(stock):
    _, url = stock
    assert "list_levels" not in style_request(url, "export", {})
    proof_geometry(render(url, PROOF.read_text(encoding="utf-8"), {}), LEGAL)


def test_marker_width_wrapping_and_page_continuity(stock, tmp_path):
    _, url = stock
    source = ("9. 시냇물 " + "강 위로 작은 배가 천천히 지나간다. " * 38 +
              "\n\n   이어지는 문단의 시작도 확인한다.\n\n10. 참나무 가지\n11. 느티나무 잎")
    style = {"body_font": "nanum-myeongjo", "page_mm": [148, 85], "margins_mm": [10, 18, 10, 18]}
    measures = []
    for blanks, outer in ((1, False), (2, False), (1, True)):
        expr = "[{{붙임}}" + " " * blanks + "{숫자}호]"
        if outer:
            expr = " " + expr + "  "
        levels = [{"expr": expr, "indent_mm": "4", "gap_mm": "2.125"}]
        pdf = render(url, source, {**style, "list_levels": levels})
        (tmp_path / f"width-{blanks}-{outer}.pdf").write_bytes(pdf)
        lines = rows(pdf)
        hits = [match_item(lines, word) for word in ("시냇물", "참나무", "느티나무")]
        origins = [body.x0 for _, _, body in hits]
        assert max(origins) - min(origins) < 0.03
        assert lines[hits[0][0]][0] < lines[hits[1][0]][0]
        for ordinal, (_, label, _) in enumerate(hits, 9):
            assert "".join(c.text for c in label) == f"[{{붙임}}{ordinal}호]"
        for _, continuation in lines[hits[0][0] + 1:hits[1][0]]:
            assert continuation[0].x0 == pytest.approx(origins[0], abs=0.03)
        match_item(lines, "이어지는문단")
        _, widest, body = hits[1]
        gap = body.x0 - (widest[-1].x0 + widest[-1].advance)
        if not outer:
            assert widest[0].x0 == pytest.approx((18 + 4) * MM, abs=0.03)
            assert gap == pytest.approx(2.125 * MM, abs=0.03)
        brace = next(c for c in widest if c.text == "}")
        digit = next(c for c in widest if c.text == "1")
        measures.append((digit.x0 - (brace.x0 + brace.advance), widest[0].x0, gap))
        print(f"{expr!r}, ordinal pages {[lines[i][0] for i, _, _ in hits]}, "
              f"body x {origins}, widest x {widest[0].x0}, gap {gap}, lines {len(lines)}")
    extra = measures[1][0] - measures[0][0]
    assert extra > 1
    assert measures[2][1] - measures[0][1] == pytest.approx(extra, abs=0.03)
    assert measures[2][2] - measures[0][2] == pytest.approx(2 * extra, abs=0.03)
    print(f"one literal space {extra} PDF pt")


def test_numbering_boundaries_and_stage_errors(stock):
    _, url = stock
    level = {"expr": "{한글}", "indent_mm": 0, "gap_mm": 2}
    for source, style, expected in (
        ("20. 큰바위\n21. 작은바위", {}, [("큰바위", "⑳"), ("작은바위", "(21)")]),
        ("14. 여름길\n15. 가을길", {"list_levels": [level]}, [("여름길", "하"), ("가을길", "가가")]),
    ):
        lines = rows(render(url, source, style))
        for body, marker in expected:
            _, chars, _ = match_item(lines, body)
            assert "".join(c.text for c in chars) == marker

    def rejected(path, payload):
        response = httpx.post(url + "/api/" + path, json=payload, trust_env=False, timeout=45)
        assert response.status_code == 422, response.status_code
        return response.json()

    assert rejected("style/export", {"list_levels": []})["field"] == "list_levels"
    for expr in ("{없는표지}", "중괄호{"):
        bad = {"list_levels": [level, {**level, "expr": expr}]}
        for result in (rejected("style/export", bad), rejected("render", {"source": "- 본문", "style": bad})):
            assert result["field"] == "list_levels[1].expr"
            assert re.search(r"2\s*단계", result["message"])
    wide = {**level, "expr": "너비" * 35}
    failure = rejected("render", {"source": "- 부모\n  - 자식", "style": {"list_levels": [level, wide]}})
    assert failure["field"].startswith("list_levels") and re.search(r"2\s*단계", failure["message"])


def test_nested_groups_measure_their_own_widest_marker(stock):
    _, url = stock
    source = "- 동쪽부모\n\n  9. 해오름\n  10. 별무리\n- 서쪽부모\n\n  1. 달그림자"
    levels = [{"expr": "·", "indent_mm": 2, "gap_mm": 2},
              {"expr": "{숫자}.", "indent_mm": 4, "gap_mm": 2}]
    lines = rows(render(url, source, {"list_levels": levels}))
    origins = {}
    for body, marker in (("동쪽부모", "·"), ("서쪽부모", "·"), ("해오름", "9."),
                         ("별무리", "10."), ("달그림자", "1.")):
        _, chars, first = match_item(lines, body)
        assert "".join(c.text for c in chars) == marker
        origins[body] = first.x0
    assert origins["동쪽부모"] == pytest.approx(origins["서쪽부모"], abs=0.03)
    assert origins["해오름"] == pytest.approx(origins["별무리"], abs=0.03)
    _, wide, first = match_item(lines, "별무리")
    assert wide[0].x0 - origins["동쪽부모"] == pytest.approx(4 * MM, abs=0.03)
    assert first.x0 - origins["달그림자"] == pytest.approx(wide[1].advance, abs=0.03)
    print(f"sibling group origins {origins}")


@pytest.fixture
def tab(stock):
    _, url = stock
    with sync_playwright() as p, p.chromium.launch() as browser:
        page = browser.new_page()
        page.set_default_timeout(45000)
        page.goto(url)
        expect(page.locator("#status")).to_have_text("최신 버전", timeout=45000)
        page.get_by_text("판형·여백 및 세부 양식", exact=True).click()
        assert page.get_by_label("목록 양식", exact=True).count() == 1
        yield page


def download_proof(tab, path):
    with tab.expect_response(lambda r: r.url.endswith("/api/render")) as result:
        tab.locator("#refresh").click()
    assert result.value.ok, result.value.text()
    expect(tab.locator("#status")).to_have_text("최신 버전", timeout=45000)
    frame = tab.locator("#preview")
    before = frame.get_attribute("src")
    with tab.expect_download() as download:
        tab.locator("#export").click()
    download.value.save_as(path)
    assert frame.get_attribute("src") == before and before.startswith("blob:")
    digest = frame.evaluate("""async frame => {
        const bytes = await (await fetch(frame.src)).arrayBuffer();
        const hash = await crypto.subtle.digest('SHA-256', bytes);
        return Array.from(new Uint8Array(hash), n => n.toString(16).padStart(2, '0')).join('');
    }""")
    pdf = path.read_bytes()
    assert digest == hashlib.sha256(pdf).hexdigest()
    assert pdf == selected_pdf(result.value, tab)
    print(f"{path.name} {len(pdf)} bytes SHA-256 {digest}")
    return pdf


def test_browser_presets_same_five_level_manuscript(tab, tmp_path):
    source = PROOF.read_text(encoding="utf-8")
    tab.locator("#file").set_input_files(PROOF)
    expect(tab.locator("#source")).to_have_value(source)
    for name, markers in (("legal", LEGAL), ("report", REPORT)):
        tab.get_by_label("목록 양식", exact=True).select_option(name)
        pdf = download_proof(tab, tmp_path / f"{name}.pdf")
        proof_geometry(pdf, markers)
        expect(tab.locator("#source")).to_have_value(source)
    tab.get_by_role("button", name="단계 추가", exact=True).click()
    fourth = tab.locator("#list-levels").get_by_role("group", name="4단계", exact=True)
    fourth.get_by_label("표지 표현식").fill("[{숫자}]")
    fourth.get_by_label("들여쓰기").fill("3.125")
    fourth.get_by_label("표지 뒤 간격").fill("2.125")
    extended = REPORT.copy()
    extended[4:6] = ["[1]", "[1]"]
    pdf = download_proof(tab, tmp_path / "report-fourth.pdf")
    proof_geometry(pdf, extended)
    lines = rows(pdf)
    for parent, child in (("사진은", "원본파일"), ("원본파일", "다시찍은")):
        _, _, start = match_item(lines, parent)
        _, marker, body = match_item(lines, child)
        assert marker[0].x0 - start.x0 == pytest.approx(3.125 * MM, abs=0.03)
        assert body.x0 - marker[-1].x1 == pytest.approx(2.125 * MM, abs=0.03)


def test_browser_custom_style_roundtrip_and_recovery(tab, tmp_path):
    source = "- 은행나무\n  - 어린잎\n- 자작나무"
    tab.locator("#source").fill(source)
    tab.locator("#body_font").select_option("nanum-myeongjo")
    preset = tab.get_by_label("목록 양식", exact=True)
    preset.select_option("report")
    groups = tab.locator("#list-levels").get_by_role("group")
    expect(groups).to_have_count(3)
    tab.get_by_role("button", name="단계 추가", exact=True).click()
    expect(groups).to_have_count(4)
    groups.last.get_by_role("button", name="단계 삭제", exact=True).click()
    expect(groups).to_have_count(3)
    expr = groups.first.get_by_label("표지 표현식")
    expr.fill("[붙임 {숫자}호]")
    groups.first.get_by_label("들여쓰기").fill("4.125")
    groups.first.get_by_label("표지 뒤 간격").fill("2.125")
    attachment = rows(download_proof(tab, tmp_path / "attachment.pdf"))
    for text, label in (("은행나무", "[붙임1호]"), ("자작나무", "[붙임2호]")):
        _, chars, _ = match_item(attachment, text)
        assert "".join(c.text for c in chars) == label
    expr.fill("({한글})")
    custom = rows(download_proof(tab, tmp_path / "hangul.pdf"))
    for text, label in (("은행나무", "(가)"), ("어린잎", "ㅇ"), ("자작나무", "(나)")):
        _, chars, _ = match_item(custom, text)
        assert "".join(c.text for c in chars) == label

    def save_style():
        with tab.expect_download() as download:
            tab.locator("#save-style").click()
        return download.value.path().read_bytes()

    file = tmp_path / "custom.json"
    file.write_bytes(save_style())
    stored = json.loads(file.read_bytes())
    assert stored["list_levels"][0] == {"expr": "({한글})", "indent_sp": 769180, "gap_sp": 396244}
    preset.select_option("legal")
    tab.locator("#style-file").set_input_files(file)
    expect(expr).to_have_value("({한글})")
    assert json.loads(save_style()) == stored
    previous = tab.locator("#preview").get_attribute("src")
    stored["list_levels"][1]["expr"] = "{오류"
    tab.locator("#style-file").set_input_files({
        "name": "invalid.json", "mimeType": "application/json", "buffer": json.dumps(stored).encode(),
    })
    expect(tab.get_by_role("alert")).to_contain_text(re.compile(r"2\s*단계"))
    assert tab.locator("#preview").get_attribute("src") == previous
    assert json.loads(save_style()) == json.loads(file.read_bytes())
    expect(tab.locator("#source")).to_have_value(source)
    legacy = PROOF.parents[1] / "web-editor/fixed-point-style.json"
    tab.locator("#style-file").set_input_files(legacy)
    expect(preset).to_have_value("")
    assert json.loads(save_style()) == json.loads(legacy.read_bytes())
