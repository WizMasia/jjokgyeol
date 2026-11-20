"""Offline checks of the shipping TeX environment and the independent PDF ruler."""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_locked_runtime(tmp_path):
    lock_path = ROOT / "runtime.lock"
    lock = json.loads(lock_path.read_text())
    verify = [sys.executable, str(ROOT / "packaging/check_runtime.py")]
    subprocess.run([*verify, str(lock_path)], check=True)
    font = lock["fonts"]["nanum-myeongjo"]["regular"]
    font["sha256"] = "0" * 64
    changed = tmp_path / "runtime.lock"
    changed.write_text(json.dumps(lock))
    result = subprocess.run([*verify, str(changed)], capture_output=True, text=True)
    assert result.returncode != 0
    assert font["path"] in result.stderr


@pytest.fixture(scope="module")
def calibration(tmp_path_factory):
    folder = tmp_path_factory.mktemp("ruler")
    (folder / "ruler.tex").write_text(r"""
\documentclass{article}
\usepackage[paperwidth=148mm,paperheight=210mm,margin=20mm]{geometry}
\usepackage{luatexko,fontspec}
\setmainfont{NanumMyeongjo.ttf}[BoldFont=NanumMyeongjoBold.ttf]
\setsansfont{NanumGothic.ttf}[BoldFont=NanumGothicBold.ttf]
\setmainhangulfont{NanumMyeongjo.ttf}[BoldFont=NanumMyeongjoBold.ttf]
\setsanshangulfont{NanumGothic.ttf}[BoldFont=NanumGothicBold.ttf]
\tracinglostchars=3
\pagestyle{empty}
\begin{document}
\noindent A\kern4bp B\par
\noindent 봄\textbf{빛}\par
\noindent{\sffamily 달\textbf{길}}\par
\end{document}
""", encoding="utf-8")
    result = subprocess.run(
        ["lualatex", "--no-shell-escape", "-halt-on-error", "-interaction=nonstopmode", "ruler.tex"],
        cwd=folder, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout[-3000:]
    return folder / "ruler.pdf"


def test_pdf_ruler(calibration):
    from pdf_metrics import read_pages

    page, = read_pages(calibration.read_bytes())
    assert (page.width, page.height) == pytest.approx((148 * 72 / 25.4, 210 * 72 / 25.4), abs=0.05)
    line, = [line for line in page.lines if "A" in line.text]
    a, b = [c for c in line.chars if c.text in ("A", "B")]
    assert (a.text, b.text) == ("A", "B")
    assert b.x0 - (a.x0 + a.advance) == pytest.approx(4, abs=0.05)
    assert a.baseline == pytest.approx(b.baseline, abs=0.01)
    assert a.size == pytest.approx(10 * 72 / 72.27, abs=0.02)


def test_embedded_faces(calibration):
    from pdf_metrics import read_pages

    expected = {"봄": "NanumMyeongjo", "빛": "NanumMyeongjoBold",
                "달": "NanumGothic", "길": "NanumGothicBold"}
    page, = read_pages(calibration.read_bytes())
    actual = [(c.text, c.font.split("+")[-1]) for line in page.lines for c in line.chars
              if c.text in expected]
    assert actual == list(expected.items())
    table = subprocess.check_output(["pdffonts", calibration], text=True).splitlines()[2:]
    faces = set()
    for row in table:
        name, *fields = row.split()
        assert fields[-5:-2] == ["yes", "yes", "yes"], row
        faces.add(name.split("+")[-1])
    assert faces == set(expected.values())
    log = calibration.with_suffix(".log").read_text()
    assert not re.search(r"Missing character:|Font shape .* undefined|Some font shapes were not available", log)
