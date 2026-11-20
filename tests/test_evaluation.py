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
    development = {"group": "other", "source": "새 문단", "split": "train"}
    with pytest.raises(ValueError, match="overlap"):
        verify_split(heldout, [development, {**development, "split": "dev"}])



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
def contract(tmp_path, monkeypatch, model_inputs, spacing_run):
    from itertools import accumulate
    from pdf_metrics import read_pages
    from evaluation import model
    from evaluation.packet import compact, digest, paragraphs, read_json, save_json

    ms, pdfs = model_inputs
    monkeypatch.setattr(model, "corpus", lambda: [ms])
    refs = tmp_path / "refs"
    model.revision(refs, ms["group"], pdfs[0], "unit-test", "1", "fixture", True)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    save_json(bundle / "manifest.json", {"runtime": "unit-test", "model_sha256": "a"*64, "development": []})
    parts = paragraphs(ms["source"])
    rows = [{"block": i, "rule_id": "R", "candidates": []} for i in range(1, len(parts)+1)]
    samples = [s for g in read_json(spacing_run / "private/key.json")["groups"].values() for s in g["samples"].values()]
    for pct, choice in [(0, "R"), (-3, "M")]:
        lines = [line.text for page in read_pages(pdfs[pct]) for line in page.lines]
        offsets = list(accumulate(len(compact(line)) for line in lines))
        base = 0
        for row, part in zip(rows, parts, strict=True):
            last = base + len(compact(part))
            candidate = [{"end_glyph": end-base, "available_sp": 100, "remaining_sp": 0,
                          "tracking_per_10000": pct*100, "space_per_10000": 10000} for end in offsets if base < end <= last]
            row["candidates"].append({"id": choice, "lines": candidate})
            base = last
    for pct, mode, choice in [(0, "rules", "R"), (-3, "model", "M")]:
        folder = bundle / ms["group"] / mode
        folder.mkdir(parents=True)
        (folder / "render.pdf").write_bytes(pdfs[pct])
        log = "".join(json.dumps({**row, "selected_id": choice}) + "\n" for row in rows).encode()
        (folder / "paragraph-candidates.jsonl").write_bytes(log)
        elapsed = next(s["ms"] for s in samples if s["style"]["ko_tracking_pct"] == pct)
        save_json(folder / "measurement.json", {
            "applied": "rule" if mode == "rules" else "model", "source_sha256": digest(ms["source"].encode()), "style": model.MODEL_STYLE,
            "model_sha256": "a"*64 if mode == "model" else None, "elapsed_ms": elapsed,
            "pdf_sha256": digest(pdfs[pct]), "log_sha256": digest(log),
        })
    return refs, bundle, ms


def alter_log(folder, change):
    from evaluation.packet import digest, read_json, save_json

    log = folder / "paragraph-candidates.jsonl"
    rows = [json.loads(s) for s in log.read_text().splitlines()]
    change(rows)
    log.write_text("".join(json.dumps(row) + "\n" for row in rows))
    receipt = folder / "measurement.json"
    save_json(receipt, {**read_json(receipt), "log_sha256": digest(log.read_bytes())})


def test_missing_bundle(tmp_path):
    from evaluation.model import model_pairs

    with pytest.raises(ValueError, match="T13"):
        model_pairs(tmp_path / "trial", tmp_path / "refs", tmp_path / "missing")
    assert not (tmp_path / "trial").exists()


@pytest.mark.parametrize("fault", ["overlap", "no-inventory", "model-used-rule", "fallback", "no-pdf", "wrong-hash",
                                   "wrong-model", "missing-block", "no-reference", "bad-ends", "candidate-set",
                                   "non-rule-baseline", "no-timing", "invalid-id"])
def test_model_refuses(tmp_path, contract, fault):
    from evaluation.model import model_pairs
    from evaluation.packet import read_json, save_json

    refs, bundle, ms = contract
    folder = bundle / ms["group"] / "model"
    receipt = read_json(folder / "measurement.json")
    if fault == "model-used-rule":
        # Even a loaded model can fall back: only the HTTP decision differs from valid agreement.
        baseline = folder.parent / "rules"
        for name in ("render.pdf", "paragraph-candidates.jsonl"):
            (folder / name).write_bytes((baseline / name).read_bytes())
        receipt = {**read_json(baseline / "measurement.json"), "model_sha256": receipt["model_sha256"]}
    if fault in ("fallback", "wrong-model", "wrong-hash", "no-timing"):
        field, value = {"fallback": ("applied", "mixed"),
                        "wrong-model": ("model_sha256", "b"*64), "wrong-hash": ("log_sha256", "b"*64),
                        "no-timing": ("elapsed_ms", 0)}[fault]
        receipt[field] = value
    save_json(folder / "measurement.json", receipt)
    if fault == "no-pdf":
        (folder / "render.pdf").unlink()
    if fault == "non-rule-baseline":
        def wrong_baseline(rows):
            rows[0]["selected_id"] = "M"
        alter_log(folder.parent / "rules", wrong_baseline)
    if fault in ("missing-block", "bad-ends", "candidate-set", "invalid-id"):
        def mutate(rows):
            if fault == "missing-block":
                rows.pop()
            elif fault == "bad-ends":
                rows[0]["candidates"][1]["lines"][0]["end_glyph"] = True
            elif fault == "candidate-set":
                rows[0]["candidates"][1]["id"] = rows[0]["selected_id"] = "different"
            else:
                rows[0]["selected_id"] = "unknown"
        alter_log(folder, mutate)
    manifest = read_json(bundle / "manifest.json")
    if fault == "overlap":
        manifest["development"] = [{**ms, "split": "dev"}]
    if fault == "no-inventory":
        del manifest["development"]
    save_json(bundle / "manifest.json", manifest)
    if fault == "no-reference":
        refs = tmp_path / "none"
    with pytest.raises(ValueError):
        model_pairs(tmp_path / "trial", refs, bundle)
    assert not (tmp_path / "trial").exists()


@pytest.mark.parametrize(("same_choices", "same_pages"), [(True, True), (True, False), (False, True)])
def test_agreement_remains_blind(tmp_path, contract, same_choices, same_pages):
    from io import BytesIO
    from pypdf import PdfWriter
    from evaluation.model import model_pairs
    from evaluation.packet import HEADER, digest, read_json, save_json
    from evaluation.results import capture, score

    refs, bundle, ms = contract
    folder = bundle / ms["group"] / "model"
    if same_choices:
        def agree(rows):
            for row in rows:
                row["selected_id"] = row["rule_id"]
        alter_log(folder, agree)
    if same_pages:
        # Source PDF bytes differ, but a reader sees exactly the rule pages.
        doc = PdfWriter(clone_from=folder.parent / "rules/render.pdf")
        doc.add_metadata({"/Title": "test-only"})
        out = BytesIO()
        doc.write(out)
        (folder / "render.pdf").write_bytes(out.getvalue())
        receipt = folder / "measurement.json"
        save_json(receipt, {**read_json(receipt), "pdf_sha256": digest(out.getvalue())})

    run = tmp_path / "study"
    model_pairs(run, refs, bundle)
    (gid, group), = read_json(run / "private/key.json")["groups"].items()
    assert group["same_choices"] is same_choices and group["same_pages"] is same_pages
    public = run / "reader"
    with (public / "reading-sheet.csv").open() as sheet:
        rows = list(csv.DictReader(sheet))
    assert len(rows) == 2 and {r["sample"] for r in rows} == group["samples"].keys()
    assert all(list(r) == HEADER and not r["preferred"] and r["group"] == gid for r in rows)
    assert {p.name for p in public.iterdir()} == {
        "reading-sheet.csv", f"ref-{gid}.pdf", *(r["sample"] + ".pdf" for r in rows)}
    assert score(run)["status"] == "awaiting_reader"
    # A unit ballot chooses one arm even when both pages agree. Only its marks count.
    ballot = tmp_path / "fixture.csv"
    ballot.write_text(filled(run, lambda r: group["samples"][r["sample"]]["applied"] == "rules"))
    capture(run, ballot, "unit-test")
    assert score(run)["counts"] == {"model": 0, "rules": 1, "tie": 0}


def test_model_pair(tmp_path, contract):
    from evaluation.model import model_pairs
    from evaluation.packet import read_json

    refs, bundle, ms = contract
    def mismatch(rows):
        lines = rows[0]["candidates"][1]["lines"]
        lines.pop(0)
        lines[0]["remaining_sp"] = -1
    for mode in ("rules", "model"):
        alter_log(bundle / ms["group"] / mode, mismatch)
    dest = tmp_path / "study"
    model_pairs(dest, refs, bundle)
    key = read_json(dest / "private/key.json")
    (gid, group), = key["groups"].items()
    assert group["manuscript"] == ms["group"] and group["reference"][-1]["final"]
    assert (dest / "reader" / ("ref-" + gid + ".pdf")).exists()
    assert len(list((dest / "reader").glob("*.pdf"))) == 3
    arms = {sample["applied"]: sample for sample in group["samples"].values()}
    assert arms["model"]["end_mismatches"] == 1 and arms["rules"]["end_mismatches"] == 0
    assert len(arms["model"]["constraint_violations"]) == 1
    for mode, arm in arms.items():
        receipt = read_json(bundle / ms["group"] / mode / "measurement.json")
        assert arm["ms"] == receipt["elapsed_ms"]
    assert "runtime" not in (dest / "reader/reading-sheet.csv").read_text()



@pytest.mark.parametrize("dx,dy", [(90, 0), (0, 90)])
def test_pdf_constraint_audit(model_inputs, dx, dy, tmp_path, monkeypatch):
    from io import BytesIO
    from pypdf import PdfWriter, Transformation
    from evaluation import model
    from evaluation.model import inspect_pdf

    ms, pdfs = model_inputs
    ends, problems = inspect_pdf(pdfs[-3], ms)
    assert ends and problems == []
    shifted = PdfWriter(clone_from=BytesIO(pdfs[-3]))
    shifted.pages[0].add_transformation(Transformation().translate(tx=dx, ty=dy))
    out = BytesIO()
    shifted.write(out)
    assert any("margin" in p for p in inspect_pdf(out.getvalue(), ms)[1])
    monkeypatch.setattr(model, "corpus", lambda: [ms])
    with pytest.raises(ValueError, match="margin"):
        model.revision(tmp_path, ms["group"], out.getvalue(), "unit-test", "1", "fixture", True)


def filled(run, select):
    """Synthetic truth table for validation only, never saved to a real study."""
    from io import StringIO
    from evaluation.packet import HEADER

    f = StringIO()
    w = csv.DictWriter(f, HEADER)
    w.writeheader()
    with (run / "reader/reading-sheet.csv").open() as stream:
        for r in csv.DictReader(stream):
            w.writerow({**r, "preferred": str(int(select(r))), "crowding": "none", "paragraph": "none", "reason": "none"})
    return f.getvalue()


def test_validate_sheet(spacing_run):
    from evaluation.packet import read_json
    from evaluation.results import observations

    key = read_json(spacing_run / "private/key.json")
    good = filled(spacing_run, lambda r: True)
    header, *rows = good.splitlines()
    assert len(observations(key, good)) == 12
    first = rows[0].split(",")
    bad = ["\n".join([header, *rows[:-1]]), good + rows[0], good.replace(key["run"], "other"),
           good.replace(",1,none,", ",0,none,"), good.replace(",1,", ",tie,"),
           good.replace(",none,none,none", ",,none,none"), good.replace(",none,none,none", ",none,99,why"),
           good.replace(",none,none,none", ",none,1,none"), good.replace(first[1], "other")]
    for bad_sheet in bad:
        with pytest.raises(ValueError):
            observations(key, bad_sheet)


def test_record_spacing(tmp_path, spacing_run):
    import shutil
    from evaluation.results import capture, score, timing

    run = tmp_path / "unit"
    shutil.copytree(spacing_run, run)
    assert score(run)["status"] == "awaiting_reader"
    assert timing(run)["all"]["n"] == 12
    ballot = tmp_path / "fixture.csv"
    ballot.write_text(filled(run, lambda r: True))
    capture(run, ballot, "unit-test")
    result = score(run)
    assert result["status"] == "scored" and result["recommend_model"] is None
    assert len(result["comparisons"]) == 4
    assert all(sorted(row["preferred"]) == [-7, -3, 0] for row in result["comparisons"])
    with pytest.raises(FileExistsError):
        capture(run, ballot, "unit-test")
    assert not (run / "reader/ballot.json").exists()


@pytest.mark.parametrize(("picks", "timing_vote", "fault", "recommend"), [
    ({"model"}, "accepted", None, True), ({"model"}, "rejected", None, False),
    ({"model", "rules"}, "accepted", None, False), ({"rules"}, "accepted", None, False),
    ({"model"}, "accepted", "constraint", False), ({"model"}, "accepted", "line", False),
])
def test_decision_gate(tmp_path, contract, picks, timing_vote, fault, recommend):
    from evaluation.model import model_pairs
    from evaluation.packet import read_json
    from evaluation.results import capture, score, timing_decision

    refs, bundle, ms = contract
    if fault:
        def change(rows):
            lines = rows[0]["candidates"][1]["lines"]
            if fault == "line":
                lines.pop(0)
            else:
                lines[0]["space_per_10000"] = 6900
        for mode in ("rules", "model"):
            alter_log(bundle / ms["group"] / mode, change)
    run = tmp_path / "trial"
    model_pairs(run, refs, bundle)
    group, = read_json(run / "private/key.json")["groups"].values()
    ballot = tmp_path / "fixture.csv"
    ballot.write_text(filled(run, lambda r: group["samples"][r["sample"]]["applied"] in picks))
    capture(run, ballot, "unit-test")
    assert score(run)["recommend_model"] is False
    timing_decision(run, timing_vote, "unit-test")
    report = score(run)
    assert report["recommend_model"] is recommend
    assert report["counts"][("tie" if len(picks) == 2 else next(iter(picks)))] == 1
    assert report["timing"]["model"]["n"] == 1
    assert len(report["observations"]) == 2


def test_percentile():
    from evaluation.results import distribution

    sample = list(range(1, 21))
    assert distribution(sample) == {"n": 20, "min_ms": 1, "median_ms": 10.5, "p95_ms": 19, "max_ms": 20}
