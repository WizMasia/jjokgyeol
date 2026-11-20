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
from evaluation.packet import corpus, digest, read_json, save_json


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


def arm(folder, pdf, log, mode, ms=MS):
    folder.mkdir(parents=True)
    (folder / "paragraph-candidates.jsonl").write_bytes(log)
    (folder / "render.pdf").write_bytes(pdf)
    save_json(folder / "measurement.json", {
        "applied": "rule" if mode == "rules" else "model", "style": STYLE,
        "model_sha256": None if mode == "rules" else "a" * 64, "elapsed_ms": 1,
        "source_sha256": digest(ms["source"].encode()), "pdf_sha256": digest(pdf), "log_sha256": digest(log)})


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


def render_allocations(tmp_path_factory, ms):
    doc, style = parse_md(ms["source"]), apply_style(Style(), STYLE)

    def render(choices=None):
        work = tmp_path_factory.mktemp("production-replay")
        pdf = typeset(doc, style, work, collect_candidates=True, paragraph_choices=choices)
        return pdf, (work / "paragraph-candidates.jsonl").read_bytes()

    rule_pdf, rule_log = render()
    rows = [json.loads(line) for line in rule_log.splitlines()]
    # Change a later block to exercise physical line order across paragraphs.
    row = rows[-1]
    rule = next(c for c in row["candidates"] if c["id"] == row["rule_id"])
    ends = [line["end_glyph"] for line in rule["lines"]]
    alternate = next(c for c in row["candidates"]
                     if [line["end_glyph"] for line in c["lines"]] == ends
                     and c["lines"][0]["spaces_sp"] != rule["lines"][0]["spaces_sp"])
    replay_pdf, replay_log = render({row["block"]: alternate["id"]})
    assert json.loads(replay_log.splitlines()[-1])["selected_id"] == alternate["id"]
    return {"rules": (rule_pdf, rule_log), "model": (replay_pdf, replay_log)}


@pytest.fixture(scope="module")
def same_break_replays(tmp_path_factory):
    ms = next(m for m in corpus() if m["group"] == "drawer")
    return ms, render_allocations(tmp_path_factory, ms)


@pytest.mark.parametrize("mode,other", [("rules", "model"), ("model", "rules")])
def test_actual_spacing_allocation_must_match_selected_replay(tmp_path, same_break_replays, mode, other):
    ms, renders = same_break_replays
    pdf, log = renders[mode]
    arm(tmp_path / "matched", pdf, log, mode, ms)
    good = model.load_arm(tmp_path / "matched", ms, mode, "a" * 64, justified=True)[-1]
    assert good["end_mismatches"] == 0 and good["constraint_violations"] == []
    arm(tmp_path / "swapped", renders[other][0], log, mode, ms)
    with pytest.raises(ValueError, match="tracking|space|glyph"):
        model.load_arm(tmp_path / "swapped", ms, mode, "a" * 64, justified=True)
    audit = model.load_arm(tmp_path / "swapped", ms, mode, "a" * 64, justified=True, audit=True)[-1]
    assert audit["t14_rejection"] and audit["pdf_constraint_violations"]


@pytest.mark.parametrize("change", ["per-space", "accumulated", "space-count", "tracking", "space-ratio",
                                    "omit-spaces", "ratio-only"])
def test_selected_spacing_channels_in_actual_print(tmp_path, same_break_replays, change):
    ms, renders = same_break_replays
    pdf, log = renders["model"]
    rows = [json.loads(s) for s in log.splitlines()]
    row = rows[-1]
    selected = next(c for c in row["candidates"] if c["id"] == row["selected_id"])
    line = selected["lines"][0]
    if change == "per-space":
        line["spaces_sp"][0] += 9000
        line["spaces_sp"][1] -= 9000  # Preserve the total and the selected ends.
    elif change == "accumulated":
        # Three successive 0.032 PDF pt differences put a glyph 0.096 pt from its origin.
        widths = line["spaces_sp"]
        for i in range(3):
            widths[i] += 2100
            widths[-1 - i] -= 2100
    elif change == "space-count":
        line["spaces_sp"].pop()
    elif change == "tracking":
        line["tracking_per_10000"] += 200
    elif change == "space-ratio":
        line["space_per_10000"] += 300
    else:
        for row in rows:
            for candidate in row["candidates"]:
                for line in candidate["lines"]:
                    line.pop("spaces_sp", None)
                    if change == "ratio-only":
                        line.pop("tracking_width_sp", None)
                        line.pop("space_width_sp", None)
    log = "\n".join(json.dumps(r) for r in rows).encode()
    arm(tmp_path / "trace", pdf, log, "model", ms)
    if change in ("omit-spaces", "ratio-only"):
        evidence = model.load_arm(tmp_path / "trace", ms, "model", "a" * 64, justified=True)[-1]
        assert evidence["constraint_violations"] == []
    else:
        with pytest.raises(ValueError, match="tracking|space|glyph"):
            model.load_arm(tmp_path / "trace", ms, "model", "a" * 64, justified=True)


def test_replay_detects_cumulative_displacement_below_per_gap_tolerance(tmp_path_factory, tmp_path):
    from pdf_metrics import read_pages

    ms = {**MS, "source": "가" * 18 + " 가" * 7 + " Spring " + "나" * 12}
    renders = render_allocations(tmp_path_factory, ms)
    lines = [[c.x0 for c in read_pages(pdf)[0].lines[0].chars if c.text.strip()]
             for pdf, _ in renders.values()]
    shift = [a - b for a, b in zip(*lines, strict=True)]
    assert max(abs(a - b) for a, b in zip(shift, shift[1:])) < .05
    assert max(map(abs, shift)) > .20
    for mode, (pdf, log) in renders.items():
        arm(tmp_path / mode, pdf, log, mode, ms)
        model.load_arm(tmp_path / mode, ms, mode, "a" * 64, justified=True)
    pdf, _ = renders["rules"]
    _, log = renders["model"]
    arm(tmp_path / "swapped", pdf, log, "model", ms)
    with pytest.raises(ValueError, match="selected spacing trace"):
        model.load_arm(tmp_path / "swapped", ms, "model", "a" * 64, justified=True)


def test_selected_trace_anchors_interior_latin_glyph(tmp_path, same_break_replays):
    from io import BytesIO
    from pdf_metrics import read_pages
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject

    ms, renders = same_break_replays
    pdf, log = renders["rules"]
    writer = PdfWriter(clone_from=BytesIO(pdf))
    page = writer.pages[0]
    content = page.get_contents().get_data()
    # In this locked NanumMyeongjo subset these are the glyph IDs for "She".
    # A balanced pair of PDF text adjustments shifts just the h in Shelf.
    prefix = b"<0036004B0048"
    assert content.count(prefix) == 1
    stream = DecodedStreamObject()
    stream.set_data(content.replace(prefix, b"<0036> -18 <004B> 18 <0048"))
    page.replace_contents(stream)
    shifted = BytesIO()
    writer.write(shifted)

    def h_origin(data):
        glyph, = [c for p in read_pages(data) for r in p.lines for c in r.chars if c.text == "h"]
        return glyph.x0

    assert h_origin(shifted.getvalue()) - h_origin(pdf) == pytest.approx(.197, abs=.002)
    arm(tmp_path / "rule", shifted.getvalue(), log, "rules", ms)
    with pytest.raises(ValueError, match="glyph|origin|position|native"):
        model.load_arm(tmp_path / "rule", ms, "rules", "a" * 64, justified=True)
