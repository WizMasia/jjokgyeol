import subprocess

import pytest

from app.document import parse_md
from app.style import NEW_DOCUMENT_STYLE
from app.typeset import typeset
from pdf_metrics import read_pages


def test_fallback_preserves_ideograph_and_radical_in_pdf(tmp_path):
    doc = parse_md("가人⼈나\n\n**가⼈人나**\n\n한A글")
    pdf = typeset(doc, NEW_DOCUMENT_STYLE, tmp_path)
    page, = read_pages(pdf)
    regular, bold, supported = page.lines

    # Glyph origins in the node-mode proof before repairing PDF text extraction.
    for line, baseline, suffix in ((regular, 527.624, ""), (bold, 506.702, "Bold")):
        assert [c.x0 for c in line.chars] == pytest.approx([51.024, 61.437, 71.738, 82.039], abs=.002)
        assert [c.baseline for c in line.chars] == pytest.approx([baseline] * 4, abs=.002)
        assert [c.font.split("+")[-1] for c in line.chars] == [
            "NanumMyeongjo" + suffix, "NanumGothic" + suffix, "NanumGothic" + suffix, "NanumMyeongjo" + suffix,
        ]
    assert [c.x0 for c in supported.chars] == pytest.approx([51.024, 61.437, 69.400], abs=.002)
    assert [c.baseline for c in supported.chars] == pytest.approx([485.781] * 3, abs=.002)

    text = subprocess.check_output(["pdftotext", "-raw", "-", "-"], input=pdf).decode()
    assert text.strip().splitlines() == ["가人⼈나", "가⼈人나", "한A글"]


def test_non_bmp_alias_preserves_source_in_pdf(tmp_path):
    from fontTools.ttLib import TTFont
    from fontTools.ttLib.tables._c_m_a_p import CmapSubtable

    from app.publication import FONT_DIR

    # Give the same fallback outline a third, supplementary-plane codepoint.
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    for name in ("NanumGothicBold.ttf", "NanumMyeongjo.ttf", "NanumMyeongjoBold.ttf"):
        (fonts / name).symlink_to(FONT_DIR / name)
    with TTFont(FONT_DIR / "NanumGothic.ttf") as face:
        cmap = CmapSubtable.newSubtable(12)
        cmap.platformID, cmap.platEncID, cmap.language = 3, 10, 0
        cmap.cmap = dict(face.getBestCmap())
        cmap.cmap[0xF0010] = cmap.cmap[ord("人")]
        face["cmap"].tables.append(cmap)
        face.save(fonts / "NanumGothic.ttf")

    job = tmp_path / "job"
    job.mkdir()
    source = "人\U000F0010⼈"
    pdf = typeset(parse_md(source), NEW_DOCUMENT_STYLE, job, font_dir=fonts)
    text = subprocess.check_output(["pdftotext", "-raw", "-", "-"], input=pdf).decode()
    assert text.strip() == source
