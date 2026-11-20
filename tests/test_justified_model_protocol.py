"""Measured rule output and editor-reference gates for the justified T14 protocol."""
import csv
import json
import runpy
import sys
from pathlib import Path

import pytest

from app.document import parse_md
from app.style import Style, apply_style
from app.typeset import typeset
from evaluation import model
from evaluation.packet import digest, read_json, save_json


STYLE = {"page_mm": [148, 210], "margins_mm": [20, 18, 20, 18], "body_font": "nanum-myeongjo",
         "body_pt": 11, "leading_pt": 17, "ko_tracking_pct": 0, "ko_tracking_bounds_pct": [-15, 15],
         "space_pct": [70, 130], "paragraph_layout": "justified"}
MS = {"group": "test-wind", "split": "model", "source": "바람 " * 13 + "May"}


@pytest.fixture(scope="module")
def printed(tmp_path_factory):
    # An independent editor fixture: fit thirteen two-syllable words across A5
    # with one fixed tracking adjustment per word. The final Latin word retains its natural width.
    width_pt = 112 * 72.27 / 25.4
    natural_pt = 11 * (26 * 973 + 12 * 307) / 1024
    tracking = int((width_pt - natural_pt) / (13 * 11) * 10000) / 100
    results = []
    for pct in (tracking, 0):
        settings = {**model.MODEL_STYLE, "ko_tracking_pct": pct, "ko_tracking_bounds_pct": [pct, pct],
                    "space_pct": [100, 100], "paragraph_layout": "balanced"}
        work = tmp_path_factory.mktemp("editor-pdf")
        pdf = typeset(parse_md(MS["source"]), apply_style(Style(), settings), work, collect_candidates=True)
        results.append((pdf, (work / "paragraph-candidates.jsonl").read_bytes()))
    return results


def test_justified_reference_freezes_verified_print_and_separate_history(tmp_path, monkeypatch, printed):
    monkeypatch.setattr(model, "corpus", lambda: [MS])
    (good, _), (ragged, _) = printed
    assert model.JUSTIFIED_MODEL_STYLE == STYLE

    def register(root, pdf, final, justified):
        return model.revision(root, MS["group"], pdf, "test-editor", "1", "fixture", final, justified=justified)

    with pytest.raises(ValueError, match="right edge"):
        register(tmp_path / "rejected", ragged, True, True)
    record = register(tmp_path / "corrected", good, True, True)
    assert record["style"] == STYLE
    assert record["verification"]["line_ends"] == [26, 29]
    assert model.final_reference(tmp_path / "corrected", MS, justified=True) == (good, [record])
    register(tmp_path / "legacy", ragged, False, False)
    with pytest.raises(ValueError, match="style|protocol"):
        register(tmp_path / "legacy", good, True, True)
    with pytest.raises(ValueError):
        model.final_reference(tmp_path / "corrected", MS)


@pytest.mark.parametrize("tracking", [-1500, 1500])
def test_justified_log_accepts_inclusive_tracking_bounds(printed, tracking):
    row = json.loads(printed[0][1])
    lines = next(c["lines"] for c in row["candidates"] if c["id"] == row["selected_id"])
    lines[0]["tracking_per_10000"] = tracking
    _, evidence = model.selected_lines([row], MS, "rules", justified=True)
    assert not evidence["constraint_violations"]


def arm(folder, pdf, log, mode):
    folder.mkdir(parents=True)
    (folder / "paragraph-candidates.jsonl").write_bytes(log)
    (folder / "render.pdf").write_bytes(pdf)
    save_json(folder / "measurement.json", {
        "applied": "rule" if mode == "rules" else "model", "style": STYLE,
        "model_sha256": None if mode == "rules" else "a" * 64, "elapsed_ms": 1,
        "source_sha256": digest(MS["source"].encode()), "pdf_sha256": digest(pdf), "log_sha256": digest(log)})


@pytest.mark.parametrize("mode", ["rules", "model"])
@pytest.mark.parametrize("fault", ["ragged", "split", "ends", "tracking", "low tracking",
                                   "space", "slack", "width", "indent"])
def test_both_justified_arms_require_measured_ends_and_constraints(tmp_path, printed, mode, fault):
    (pdf, log), (ragged, _) = printed
    row = json.loads(log)
    lines = next(c["lines"] for c in row["candidates"] if c["id"] == row["selected_id"])
    assert [line["end_glyph"] for line in lines] == [26, 29]
    if fault == "ragged":
        pdf = ragged
    else:
        key, value = {"split": ("end_glyph", 25), "ends": ("end_glyph", 24),
                      "tracking": ("tracking_per_10000", 1501), "low tracking": ("tracking_per_10000", -1501),
                      "space": ("space_per_10000", 6999), "width": ("available_sp", 1_000_000),
                      "indent": ("start_sp", 65_536),
                      "slack": ("remaining_sp", 65536)}[fault]
        lines[0][key] = value
    folder = tmp_path / mode
    arm(folder, pdf, json.dumps(row).encode(), mode)
    with pytest.raises(ValueError, match="right edge|word boundary|selected ends|tracking|space|width|indent"):
        model.load_arm(folder, MS, mode, "a" * 64, justified=True)


def test_verified_pair_has_private_geometry_and_fresh_blank_ballot(tmp_path, monkeypatch, printed):
    # Receipt/call plumbing only; the local-inference producer owns real model measurements.
    monkeypatch.setattr(model, "corpus", lambda: [MS])
    pdf, log = printed[0]
    refs, bundle, dest = (tmp_path / n for n in ("refs", "bundle", "run"))
    model.revision(refs, MS["group"], pdf, "test-editor", "none", "fixture", True, justified=True)
    for mode in ("rules", "model"):
        arm(bundle / MS["group"] / mode, pdf, log, mode)
    save_json(bundle / "manifest.json", {"runtime": "unit-fixture", "model_sha256": "a"*64, "development": []})
    model.model_pairs(dest, refs, bundle, justified=True)
    key = read_json(dest / "private/key.json")
    group, = key["groups"].values()
    assert key["protocol"] == 2
    for sample in group["samples"].values():
        assert sample["pdf_ends"] == sample["selected_ends"] == [26, 29]
        assert sample["verification"]["max_nonfinal_right_error_pt"] <= .05
    assert group["same_choices"] and group["same_pages"]
    with (dest / "reader/reading-sheet.csv").open() as sheet:
        rows = list(csv.DictReader(sheet))
    assert len(rows) == 2 and all(not row["preferred"] for row in rows)
    assert not (dest / "private/timing-decision.json").exists()


def test_bundle_producer_cli_selects_justified_profile(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / "packaging/decision_bundle.py"
    monkeypatch.setattr(sys, "argv", [str(path), str(tmp_path / "bundle"), "--justified"])
    from fastapi.testclient import TestClient

    calls = []

    def request(client, url, *, json):
        calls.append((json["decision"], json["style"]))
        raise RuntimeError("stop before inference")

    monkeypatch.setattr(TestClient, "post", request)
    with pytest.raises(RuntimeError, match="stop before inference"):
        runpy.run_path(str(path), run_name="__main__")
    assert calls == [("rule", STYLE)]


def test_producer_retains_actual_fallback_and_pdf_diagnostics(tmp_path, printed):
    from app.ai.decision import PIN

    producer = runpy.run_path(str(Path(__file__).resolve().parents[1] / "packaging/decision_bundle.py"))
    (good, log), (ragged, _) = printed
    arm(tmp_path / "rules", good, log, "rules")
    arm(tmp_path / "model", ragged, log, "rules")  # The recorded response took the rule path.
    producer["audit_pair"](tmp_path, MS, justified=True)
    pair = read_json(tmp_path / "outcome.json")
    rules, fallback = (pair["arms"][name] for name in ("rules", "model"))
    assert pair["same_candidate_sets"] and pair["distinct_printed_pages"]
    assert rules["t14_rejection"] is None and rules["end_mismatches"] == 0
    assert rules["verification"]["line_ends"] == [26, 29]
    assert fallback["applied"] == "rule" and fallback["t14_rejection"]
    assert "right edge" in fallback["pdf_constraint_violations"][0]
    with pytest.raises(ValueError):
        model.load_arm(tmp_path / "model", MS, "model", PIN["files"]["model.safetensors"], justified=True)
