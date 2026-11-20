"""Inspect the PDFs served to a browser using new, legacy, and partially edited styles."""
import json
from pathlib import Path

import pytest
from playwright.sync_api import expect

from settings_ui import open_settings, close_settings
from pdf_metrics import read_pages
from test_article_acceptance import save
from test_duplex_acceptance import reopen_file
from test_list_acceptance import download_proof
from test_reference_acceptance import editor, url


PROOFS = Path(__file__).resolve().parents[1] / "proofs"
MANUSCRIPT = PROOFS / "publication-typography/manuscript.md"


def assert_sizes(pdf, h1, h2, body):
    lines = [line for page in read_pages(pdf) for line in page.lines]
    for start, points in (("새벽", h1), ("능선", h2), ("볕이", body)):
        line, = [line for line in lines if line.text.startswith(start)]
        assert line.chars[0].size == pytest.approx(points * 72 / 72.27, abs=.015), start


def test_new_book_keeps_both_heading_sizes_on_body_edit(editor, tmp_path):
    manuscript = MANUSCRIPT.read_bytes()
    editor.locator("#source").fill(manuscript.decode())
    assert_sizes(download_proof(editor, tmp_path / "new-11.pdf"), 17.6, 16.5, 11)
    open_settings(editor)
    editor.locator("#body_pt").fill("16")
    close_settings(editor)
    assert_sizes(download_proof(editor, tmp_path / "new-16.pdf"), 17.6, 16.5, 16)
    saved = tmp_path / "new-style.json"
    open_settings(editor)
    editor.locator("#style summary").click()
    saved.write_bytes(save(editor, "#save-style"))
    assert json.loads(saved.read_bytes())["text_styles"]["heading1"]["font_sp"] == 1153434
    reopen_file(editor, saved)
    close_settings(editor)
    assert_sizes(download_proof(editor, tmp_path / "reopened.pdf"), 17.6, 16.5, 16)
    assert save(editor, "#save") == manuscript


def test_old_book_can_fix_only_one_heading_level(editor, tmp_path):
    legacy = PROOFS / "web-editor/fixed-point-style.json"
    editor.locator("#source").fill(MANUSCRIPT.read_text())
    open_settings(editor)
    editor.locator("#style summary").click()
    reopen_file(editor, legacy)
    assert json.loads(save(editor, "#save-style")) == json.loads(legacy.read_bytes())
    editor.locator("#body_pt").fill("16")
    close_settings(editor)
    assert_sizes(download_proof(editor, tmp_path / "legacy.pdf"), 25.6, 24, 16)
    open_settings(editor)
    stored = json.loads(save(editor, "#save-style"))
    stored["text_styles"] = {"heading1": {"font_sp": 1179648}}
    partial = tmp_path / "partial.json"
    partial.write_text(json.dumps(stored))
    reopen_file(editor, partial)
    editor.locator("#body_pt").fill("14")
    close_settings(editor)
    assert_sizes(download_proof(editor, tmp_path / "partial.pdf"), 18, 21, 14)
    open_settings(editor)
    assert json.loads(save(editor, "#save-style"))["text_styles"] == stored["text_styles"]


def test_report_example_in_browser(editor, tmp_path):
    manuscript = MANUSCRIPT.read_bytes()
    editor.locator("#source").fill(manuscript.decode())
    open_settings(editor)
    editor.locator("#style summary").click()
    style = MANUSCRIPT.with_name("style.json")
    reopen_file(editor, style)
    close_settings(editor)
    choice = editor.locator("#proof-choice")
    for size in (11, 16):
        open_settings(editor)
        editor.locator("#body_pt").fill(str(size))
        close_settings(editor)
        choice.select_option("model")
        pages = read_pages(download_proof(editor, tmp_path / f"report-{size}.pdf"))
        choice.select_option("rule")
        # The distributed report proofs were printed with rule selection.
        assert read_pages(save(editor, "#export")) == read_pages(MANUSCRIPT.with_name(f"report-{size}.pdf").read_bytes())
        assert any(line.text.startswith("새벽") for line in pages[0].lines)
        for number, page in enumerate(pages, 1):
            assert any(line.text.strip() == str(number) for line in page.lines)
            assert any("능선관측소" == "".join(line.text.split()) for line in page.lines)
        # The H2 and header both start with 능선, so inspect the complete H2.
        for start, pt in (("새벽", 18), ("능선의", 14), ("볕이", size), ("풍향은", 9)):
            line, = [line for page in pages for line in page.lines if start in line.text]
            glyph = next(c for c in line.chars if c.text == start[0])
            assert glyph.size == pytest.approx(pt * 72 / 72.27, abs=.015)
    assert save(editor, "#save") == manuscript
    open_settings(editor)
    saved = json.loads(save(editor, "#save-style"))
    assert saved == {**json.loads(style.read_bytes()), "body_sp": 1048576}


def test_tracking_error_clears_after_body_size_recovery(editor):
    editor.locator("#source").fill("본문")
    open_settings(editor)
    editor.locator("#style summary").click()
    editor.locator("#body_pt").fill("100")
    editor.locator("#tracking-enabled").check()
    upper = editor.locator("#tracking-upper")
    upper.fill("17000")
    close_settings(editor)
    with editor.expect_response("**/api/render") as failed:
        editor.locator("#refresh").click()
    assert failed.value.status == 422
    open_settings(editor)
    expect(upper).to_have_attribute("aria-invalid", "true")
    editor.locator("#body_pt").fill("11")
    close_settings(editor)
    with editor.expect_response("**/api/render", timeout=45000) as success:
        editor.locator("#refresh").click()
    assert success.value.ok
    expect(editor.locator("#status")).to_have_text("최신 버전")
    open_settings(editor)
    expect(upper).not_to_have_attribute("aria-invalid", "true")
    expect(editor.locator("#typography-error")).to_be_empty()
