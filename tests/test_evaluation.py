"""T14 fixtures are software checks. Only a submitted reader sheet is study evidence."""
import csv
import json
import unicodedata
from collections import defaultdict
from pathlib import Path

import pytest
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def spacing_run(tmp_path_factory):
    from evaluation.packet import spacing

    dest = tmp_path_factory.mktemp("evaluation") / "study"
    spacing(dest)
    return dest


def test_split():
    from evaluation.packet import corpus, verify_split

    heldout = corpus()
    assert {r["split"] for r in heldout} == {"spacing", "model"}
    verify_split(heldout, [])
    first = heldout[0]
    for group, source in [
        (first["group"], "새 글"),
        ("other", unicodedata.normalize("NFD", first["source"]).replace(" ", "   ")),
        ("other", "새 문단\n\n" + first["source"].split("\n\n")[0]),
    ]:
        with pytest.raises(ValueError, match="overlap"):
            verify_split(heldout, [{"group": group, "source": source, "split": "train"}])


def test_blind_set(spacing_run):
    from pdf_metrics import read_pages
    from evaluation.packet import corpus, verify_split

    verify_split(corpus(), [])
    key = json.loads((spacing_run / "private/key.json").read_text())
    public = spacing_run / "reader"
    sheet = (public / "reading-sheet.csv").read_text()
    assert sheet.splitlines()[0] == (ROOT / "evaluation/reading-sheet.csv").read_text().strip()
    rows = list(csv.DictReader(sheet.splitlines()))
    assert len(rows) == 12 and len({r["sample"] for r in rows}) == 12
    assert {r["run"] for r in rows} == {key["run"]}
    assert set(public.iterdir()) == {public / (r["sample"] + ".pdf") for r in rows} | {public / "reading-sheet.csv"}
    assert not any(word in sheet.lower() for word in ("nanum", "myeongjo", "gothic", "tex", "model", "rules", "tracking", "%", "pt"))
    assert set(rows[0]) == {"run", "group", "sample", "preferred", "crowding", "paragraph", "reason"}
    assert all(not any(r[k] for k in ("preferred", "crowding", "paragraph", "reason")) for r in rows)
    cells = set()
    english = defaultdict(list)
    for group in key["groups"].values():
        assert len(group["samples"]) == 3
        assert len({(s["style"]["body_font"], s["style"]["body_pt"]) for s in group["samples"].values()}) == 1
        for sid, sample in group["samples"].items():
            assert sample["ms"] > 0
            style = sample["style"]
            font, pt, tracking = (style[k] for k in ("body_font", "body_pt", "ko_tracking_pct"))
            assert style["space_pct"] == [100, 100]
            cells.add((font, pt, tracking))
            blind = PdfReader(public / (sid + ".pdf"))
            original = read_pages((spacing_run / "private" / (sid + ".pdf")).read_bytes())
            assert len(original) == len(blind.pages) == 1
            page = blind.pages[0]
            assert page.extract_text() == "" and len(page.images) == 1
            assert not page["/Resources"].get("/Font") and not blind.metadata
            assert float(page.mediabox.width) == pytest.approx(original[0].width, abs=.001)
            cs = next(line.chars for line in original[0].lines if "물안개" in line.text)
            left, right = cs[:2]
            assert left.font.endswith({"nanum-myeongjo": "NanumMyeongjo", "nanum-gothic": "NanumGothic"}[font])
            assert left.size == pytest.approx(pt * 72 / 72.27, abs=.05)
            assert right.x0-left.x0-left.advance == pytest.approx(left.size * tracking / 100, abs=.05)
            dock = next(line.chars for line in original[0].lines if "Dock" in line.text)
            n = next(i for i, c in enumerate(dock) if c.text == "D")
            english[font, pt].append([b.x0-a.x0 for a, b in zip(dock[n:n+3], dock[n+1:n+4])])
    assert cells == {(f, p, t) for f in ("nanum-myeongjo", "nanum-gothic") for p in (11, 16) for t in (0, -3, -7)}
    for advances in english.values():
        assert all(a == pytest.approx(advances[0], abs=.01) for a in advances)

