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



@pytest.fixture(scope="module")
def model_inputs(spacing_run):
    # Reuse two known typography fixtures. No T13 inference or human reference in tests.
    from evaluation.packet import corpus, read_json

    source = next(r["source"] for r in corpus() if r["split"] == "spacing")
    ms = {"source": source, "group": "unit-test-only", "split": "model"}
    pdfs = {}
    for group in read_json(spacing_run / "private/key.json")["groups"].values():
        for sid, sample in group["samples"].items():
            style = sample["style"]
            if style["body_pt"] == 11 and style["body_font"] == "nanum-myeongjo":
                pdfs[style["ko_tracking_pct"]] = (spacing_run / "private" / (sid + ".pdf")).read_bytes()
    return ms, pdfs


def test_missing_provider(tmp_path, monkeypatch):
    import sys
    from evaluation.model import model_pairs

    monkeypatch.setitem(sys.modules, "app.ai.evaluation", None)

    with pytest.raises(ValueError, match="T13"):
        model_pairs(tmp_path / "absent", tmp_path / "refs")
    assert not (tmp_path / "absent").exists()


def test_reference_history(tmp_path, monkeypatch, model_inputs):
    from evaluation import model

    ms, pdfs = model_inputs
    monkeypatch.setattr(model, "corpus", lambda: [ms])
    root = tmp_path / "refs"
    for number, final in [(1, False), (2, True)]:
        record = model.revision(root, ms["group"], pdfs[-3], "unit-test", "2", "fixture", final)
        assert record["revision"] == number and record["final"] is final
    ref, history = model.final_reference(root, ms)
    assert ref == pdfs[-3] and [r["revision"] for r in history] == [1, 2]
    with pytest.raises(ValueError, match="final"):
        model.revision(root, ms["group"], pdfs[0], "unit-test", "2", "fixture", True)
    with pytest.raises(ValueError, match="source"):
        model.final_reference(root, {**ms, "source": ms["source"] + "수정"})
    with pytest.raises(ValueError, match="paragraph"):
        model.revision(tmp_path / "bad", ms["group"], pdfs[0], "unit-test", "8", "fixture", True)


@pytest.fixture
def contract(tmp_path, monkeypatch, model_inputs):
    from types import SimpleNamespace
    from evaluation import model
    from evaluation.packet import digest

    ms, pdfs = model_inputs
    monkeypatch.setattr(model, "corpus", lambda: [ms])
    refs = tmp_path / "refs"
    model.revision(refs, ms["group"], pdfs[0], "unit-test", "1", "fixture", True)
    inventory = {"runtime": "unit-test", "model_sha256": "e"*64, "development": []}

    def render(source, style, mode):
        pdf = pdfs[0 if mode == "rules" else -3]
        ends = model.pdf_ends(pdf, ms)
        return {"pdf": pdf, "source_sha256": digest(source.encode()), "style": style, "applied": mode,
                "model_sha256": inventory["model_sha256"] if mode == "model" else None,
                "choices": ["fixture"] * 3, "selected_ends": ends, "constraint_violations": []}

    provider = SimpleNamespace(inventory=lambda: inventory, render=render)
    monkeypatch.setattr(model, "load_provider", lambda: provider)
    return refs, provider, ms


@pytest.mark.parametrize("fault", ["overlap", "no-inventory", "identical", "fallback", "no-pdf",
                                   "wrong-model", "missing-choice", "no-reference", "bad-ends"])
def test_model_refuses(tmp_path, contract, fault):
    from evaluation.model import model_pairs

    refs, provider, ms = contract
    render = provider.render

    def bad(source, style, mode):
        result = render(source, style, mode)
        if mode == "model":
            if fault == "identical":
                # Different bytes, same pages, even after PDF metadata changes.
                from io import BytesIO
                from pypdf import PdfWriter
                doc = PdfWriter(clone_from=BytesIO(render(source, style, "rules")["pdf"]))
                doc.add_metadata({"/Title": "test-only"})
                out = BytesIO()
                doc.write(out)
                result["pdf"] = out.getvalue()
            if fault == "fallback":
                result["applied"] = "rules"
            if fault == "no-pdf":
                result["pdf"] = None
            if fault == "wrong-model":
                result["model_sha256"] = "b"*64
            if fault == "missing-choice":
                result["choices"].pop()
            if fault == "bad-ends":
                result["selected_ends"] = [True]
        return result

    provider.render = bad
    if fault == "overlap":
        provider.inventory()["development"] = [{**ms, "split": "dev"}]
    if fault == "no-inventory":
        del provider.inventory()["development"]
    if fault == "no-reference":
        refs = tmp_path / "none"
    with pytest.raises(ValueError):
        model_pairs(tmp_path / "trial", refs)
    assert not (tmp_path / "trial").exists()


def test_model_pair(tmp_path, contract):
    from evaluation.model import model_pairs
    from evaluation.packet import read_json

    refs, provider, ms = contract
    render = provider.render

    def mismatched(source, style, mode):
        result = render(source, style, mode)
        if mode == "model":
            result["selected_ends"].pop(0)
            result["constraint_violations"].append("fixture: paragraph 1, gap 1")
        return result

    provider.render = mismatched
    dest = tmp_path / "study"
    model_pairs(dest, refs)
    key = read_json(dest / "private/key.json")
    (gid, group), = key["groups"].items()
    assert group["manuscript"] == ms["group"] and group["reference"][-1]["final"]
    assert (dest / "reader" / ("ref-" + gid + ".pdf")).exists()
    assert len(list((dest / "reader").glob("*.pdf"))) == 3
    arms = {sample["applied"]: sample for sample in group["samples"].values()}
    assert arms["model"]["end_mismatches"] == 1 and arms["rules"]["end_mismatches"] == 0
    assert arms["model"]["constraint_violations"] == ["fixture: paragraph 1, gap 1"]
    assert arms["model"]["ms"] > 0 and arms["rules"]["ms"] > 0
    assert "runtime" not in (dest / "reader/reading-sheet.csv").read_text()

