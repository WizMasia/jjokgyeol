"""Measure the actual PDF offered by a fresh editor with untouched publication settings."""
import json
from pathlib import Path

import pytest

from settings_ui import open_settings, close_settings
from pdf_metrics import read_pages
from test_article_acceptance import save
from test_duplex_acceptance import reopen_file
from test_list_acceptance import download_proof
from test_reference_acceptance import editor, url


SOURCE = Path(__file__).resolve().parents[1] / "proofs/justified-publication/manuscript.md"


def test_new_manuscript_fills_the_measure_without_splitting_words(editor, tmp_path):
    source = SOURCE.read_text()
    editor.locator("#source").fill(source)
    pdf = download_proof(editor, tmp_path / "publication.pdf")
    text = []
    word_ends, paragraph_ends = set(), set()
    offset = 0
    for paragraph in source.strip().split("\n\n"):
        for word in paragraph.split():
            offset += len(word)
            word_ends.add(offset)
        paragraph_ends.add(offset)

    position = 0
    short_last_lines = []
    for page in read_pages(pdf):
        for line in page.lines:
            chars = [c for c in line.chars if c.text.strip()]
            assert chars[0].x0 == pytest.approx(18 * 72 / 25.4, abs=.05)
            right = page.width - 18 * 72 / 25.4
            remaining = right - chars[-1].x0 - chars[-1].advance
            letters = "".join(c.text for c in chars)
            text.append(letters)
            position += len(letters)
            assert position in word_ends
            if position in paragraph_ends:
                short_last_lines.append(remaining)
            else:
                assert remaining == pytest.approx(0, abs=.05), line.text
    assert "".join(text) == "".join(source.split())
    assert len(short_last_lines) == 3 and max(short_last_lines) > 11
    open_settings(editor)
    editor.locator("#style summary").click()
    style = tmp_path / "publication.json"
    style.write_bytes(save(editor, "#save-style"))
    assert json.loads(style.read_bytes())["paragraph_layout"] == "justified"
    reopen_file(editor, style)
    close_settings(editor)
    assert read_pages(download_proof(editor, tmp_path / "reopened.pdf")) == read_pages(pdf)
    assert save(editor, "#save") == SOURCE.read_bytes()
