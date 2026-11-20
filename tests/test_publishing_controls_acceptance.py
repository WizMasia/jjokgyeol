"""Measure the selected sheet and both paragraph indents in a downloaded PDF."""
import json
import re

from playwright.sync_api import expect
import pytest

from pdf_metrics import read_pages
from test_article_acceptance import save
from test_list_acceptance import download_proof
from test_reference_acceptance import editor, url


def test_trim_size_and_point_indents_reach_both_proofs(editor, tmp_path):
    editor.locator("#style summary").click()
    paper = editor.get_by_label("판형 선택", exact=True)
    unit = editor.get_by_label("들여쓰기 단위", exact=True)
    expect(paper.locator("option:checked")).to_contain_text("A5", timeout=1500)
    a4 = paper.locator("option").filter(has_text=re.compile(r"^A4\b"))
    paper.select_option(a4.get_attribute("value"))
    unit.select_option("pt")
    editor.get_by_label("첫 줄 들여쓰기", exact=True).fill("11")
    editor.get_by_label("이후 줄 들여쓰기", exact=True).fill("22")
    editor.locator("#source").fill(("인쇄용 판형과 들여쓰기를 함께 확인할 문장입니다. " * 9).strip())
    style = save(editor, "#save-style")
    stored = json.loads(style)
    assert (stored["first_indent_sp"], stored["rest_indent_sp"]) == (11 * 65536, 22 * 65536)
    assert "article_expr" not in stored
    first = download_proof(editor, tmp_path / "model.pdf")
    editor.locator("#proof-choice").select_option("rule")
    second = save(editor, "#export")
    for pdf in (first, second):
        page, = read_pages(pdf)
        assert (page.width, page.height) == pytest.approx((210 * 72 / 25.4, 297 * 72 / 25.4), abs=.01)
        lines = [[c for c in line.chars if c.text.strip()] for line in page.lines]
        assert len(lines) >= 3
        for i, chars in enumerate(lines):
            expected_x = 18 * 72 / 25.4 + (11 if i == 0 else 22) * 72 / 72.27
            assert chars[0].x0 == pytest.approx(expected_x, abs=.03)
    unit.select_option("mm")
    expect(editor.locator("#status")).to_have_text("최신 버전")
    assert json.loads(save(editor, "#save-style")) == stored
    with editor.expect_response("**/api/style/open") as opened:
        editor.locator("#style-file").set_input_files({"name": "인쇄.json", "mimeType": "application/json", "buffer": style})
    assert opened.value.ok
    expect(paper.locator("option:checked")).to_contain_text("A4")
    unit.select_option("pt")
    assert float(editor.get_by_label("첫 줄 들여쓰기", exact=True).input_value()) == pytest.approx(11, abs=2e-5)
    assert json.loads(save(editor, "#save-style")) == stored
