"""Open the distributed report style and measure the PDF downloaded by the editor."""
import json
from pathlib import Path

import pytest

from app.style import from_stored_style
from test_article_acceptance import save
from test_duplex_acceptance import reopen_file
from test_list_acceptance import download_proof
from test_list_typeset import baselines, text
from test_reference_acceptance import editor, url


ROOT = Path(__file__).resolve().parents[1]
STYLE = ROOT / "app/static/general-report.json"
SOURCE = ROOT / "proofs/general-report/manuscript.md"


def test_distributed_report_is_a_saved_style():
    assert STYLE.is_file(), "일반 보고서 양식 JSON이 배포본에 필요합니다."
    stored = json.loads(STYLE.read_bytes())
    assert from_stored_style(stored).to_stored() == stored


def test_download_open_and_reuse_report(editor, tmp_path):
    source = SOURCE.read_bytes()
    editor.locator("#source").fill(source.decode())
    editor.locator("#style summary").click()
    link = editor.get_by_role("link", name="일반 보고서 양식 다운로드")
    assert link.count() == 1
    with editor.expect_download() as download:
        link.click()
    assert download.value.suggested_filename == "일반 보고서.json"
    path = tmp_path / download.value.suggested_filename
    download.value.save_as(path)
    assert path.read_bytes() == STYLE.read_bytes()
    reopen_file(editor, path)
    saved = tmp_path / "다시 저장한 양식.json"
    saved.write_bytes(save(editor, "#save-style"))
    assert json.loads(saved.read_bytes()) == json.loads(path.read_bytes())
    reopen_file(editor, saved)
    pdf = download_proof(editor, tmp_path / "general-report.pdf")
    lines = [chars for _, chars in baselines(pdf)]
    title, = [line for line in lines if text(line) == "현장점검결과보고"]
    assert title[0].font.endswith("NanumGothicBold")
    assert title[0].size == pytest.approx(20 * 72 / 72.27, abs=.02)
    stages = [
        ("□", "점검개요", 16, "NanumGothicBold", "NanumGothicBold", 0),
        ("○", "대상시설", 15, "NanumMyeongjo", "NanumMyeongjo", 7.5),
        ("-", "점검범위", 15, "NanumMyeongjoBold", "NanumMyeongjo", 22.5),
        ("*", "현장기록", 13, "NanumGothic", "NanumGothic", 37.5),
    ]
    for symbol, prefix, pt, mark_font, item_font, indent in stages:
        row, = [line for line in lines if text(line).startswith(symbol + prefix)]
        mark, first = row[:2]
        assert mark.font.endswith(mark_font) and first.font.endswith(item_font)
        for char in (mark, first):
            assert char.size == pytest.approx(pt * 72 / 72.27, abs=.02)
        assert mark.x0 == pytest.approx(18 * 72 / 25.4 + indent * 72 / 72.27, abs=.07)
    assert save(editor, "#save") == source
