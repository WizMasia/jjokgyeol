"""Freeze editor revisions and bind genuine T13 selections to the PDF under review."""
import fcntl
import json
import math
import re
import tempfile
from pathlib import Path

from tests.pdf_metrics import read_pages

from evaluation.packet import (
    PAGE, RNG, add_pdf, blind_pdf, compact, corpus, digest, new_group,
    new_run, paragraphs, read_json, save_json, verify_split,
)

MODEL_STYLE = {**PAGE, "body_font": "nanum-myeongjo", "body_pt": 11, "leading_pt": 17,
               "ko_tracking_pct": 0, "ko_tracking_bounds_pct": [-7, 0], "space_pct": [70, 130]}


def pdf_ends(pdf, manuscript):
    if not isinstance(pdf, bytes) or not pdf.startswith(b"%PDF-"):
        raise ValueError("missing PDF")
    text = []
    ends = []
    size = 0
    for page in read_pages(pdf):
        mm = [value * 25.4 / 72 for value in (page.width, page.height)]
        if any(abs(a-b) > .03 for a, b in zip(mm, MODEL_STYLE["page_mm"], strict=True)):
            raise ValueError("wrong reference or candidate page size")
        for line in page.lines:
            if any(c.font.split("+")[-1] != "NanumMyeongjo" or abs(c.size - 11 * 72 / 72.27) > .05
                   for c in line.chars if c.text.strip()):
                raise ValueError("wrong reference or candidate typeface/size")
            value = compact(line.text)
            if value:
                text.append(value)
                size += len(value)
                ends.append(size)
    if "".join(text) != compact(manuscript["source"]):
        raise ValueError("PDF text differs from manuscript source")
    return ends


def paragraph_numbers(value, maximum):
    parts = value.split(",")
    if not all(re.fullmatch(r"[1-9]\d{0,5}", p) and int(p) <= maximum for p in parts):
        raise ValueError("invalid paragraph numbers")
    if len(set(parts)) != len(parts):
        raise ValueError("duplicate paragraph numbers")


def revision(root, name, pdf, editor, paragraph, reason, final):
    ms = next((r for r in corpus() if r["group"] == name and r["split"] == "model"), None)
    if ms is None or not editor.strip() or not reason.strip() or type(final) is not bool:
        raise ValueError("reference needs a manuscript, editor and reason")
    if paragraph != "none":
        paragraph_numbers(paragraph, len(paragraphs(ms["source"])))
    pdf_ends(pdf, ms)
    folder = Path(root) / name
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (folder / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = sorted(p for p in folder.iterdir() if p.name.isdecimal())
        if previous and read_json(previous[-1] / "record.json")["final"]:
            raise ValueError("reference already final")
        number = len(previous) + 1
        record = {"revision": number, "source_sha256": digest(ms["source"].encode()),
                  "pdf_sha256": digest(pdf), "style": MODEL_STYLE, "editor": editor,
                  "paragraph": paragraph, "reason": reason, "final": final}
        with tempfile.TemporaryDirectory(dir=folder) as stage:
            path = Path(stage)
            (path / "page.pdf").write_bytes(pdf)
            save_json(path / "record.json", record)
            path.rename(folder / f"{number:06d}")
    return record


def final_reference(root, ms):
    folder = Path(root) / ms["group"]
    history = []
    pdf = None
    for path in sorted(folder.glob("[0-9]*")):
        record = read_json(path / "record.json")
        pdf = (path / "page.pdf").read_bytes()
        if (record["source_sha256"] != digest(ms["source"].encode()) or record["style"] != MODEL_STYLE
                or record["pdf_sha256"] != digest(pdf) or record["revision"] != len(history) + 1):
            raise ValueError("reference source, revision or PDF changed")
        history.append(record)
    if not history or history[-1]["final"] is not True:
        raise ValueError("final editor reference required for " + ms["group"])
    return pdf, history


def selected_lines(rows, ms, mode):
    parts = paragraphs(ms["source"])
    if len(rows) != len(parts):
        raise ValueError("missing paragraph log")
    choices, ends, catalog, violations = [], [], [], []
    base = 0
    different = False
    for block, (row, part) in enumerate(zip(rows, parts, strict=True), 1):
        if not isinstance(row, dict) or type(row.get("block")) is not int or row["block"] != block:
            raise ValueError("invalid paragraph block")
        items = row.get("candidates")
        if not isinstance(items, list) or not all(isinstance(c, dict) and isinstance(c.get("id"), str)
                                                  and c["id"].strip() for c in items):
            raise ValueError("invalid candidate list")
        by_id = {c["id"]: c.get("lines") for c in items}
        rule, chosen = row.get("rule_id"), row.get("selected_id")
        if len(by_id) != len(items) or rule not in by_id or chosen not in by_id:
            raise ValueError("unknown or duplicate candidate ID")
        different |= chosen != rule
        catalog.append((rule, by_id))
        choices.append(chosen)
        lines = by_id[chosen]
        if not isinstance(lines, list) or not lines:
            raise ValueError("missing selected lines")
        previous = 0
        for i, line in enumerate(lines, 1):
            fields = ("end_glyph", "available_sp", "remaining_sp", "tracking_per_10000", "space_per_10000")
            if not isinstance(line, dict) or not all(type(line.get(k)) is int for k in fields):
                raise ValueError("missing integer line measurements")
            end, width, left, tracking, space = [line[k] for k in fields]
            if end <= previous:
                raise ValueError("invalid cumulative end_glyph")
            previous = end
            ends.append(base + end)
            for field, ok in [("width", width > 0 and 0 <= left <= width),
                              ("tracking", -700 <= tracking <= 0), ("space", 7000 <= space <= 13000)]:
                if not ok:
                    violations.append(f"block {block}, line {i}: {field}")
        if previous != len(compact(part)):
            raise ValueError("end_glyph does not cover manuscript paragraph")
        base += previous
    if different != (mode == "model"):
        raise ValueError("missing distinct model selection or non-rule baseline")
    return catalog, {"choices": choices, "selected_ends": ends, "constraint_violations": violations}


def load_arm(folder, ms, mode, model_hash):
    receipt = read_json(folder / "measurement.json")
    pdf = (folder / "render.pdf").read_bytes()
    log = (folder / "paragraph-candidates.jsonl").read_bytes()
    if (not isinstance(receipt, dict) or receipt.get("applied") != mode or receipt.get("style") != MODEL_STYLE
            or receipt.get("source_sha256") != digest(ms["source"].encode())
            or receipt.get("model_sha256") != (model_hash if mode == "model" else None)
            or receipt.get("pdf_sha256") != digest(pdf) or receipt.get("log_sha256") != digest(log)):
        raise ValueError("T13 measurement differs from source, mode, runtime, style or artifacts")
    elapsed = receipt.get("elapsed_ms")
    if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed <= 0:
        raise ValueError("missing positive measured elapsed_ms")
    rows = [json.loads(line) for line in log.decode("utf-8").splitlines()]
    catalog, evidence = selected_lines(rows, ms, mode)
    found = pdf_ends(pdf, ms)
    selected = set(evidence["selected_ends"])
    evidence.update(applied=mode, style=MODEL_STYLE, ms=elapsed, pdf_ends=found,
                    end_mismatches=max(len(selected-set(found)), len(set(found)-selected)))
    return pdf, log, receipt, catalog, evidence


def model_pairs(dest, references, bundle):
    bundle = Path(bundle)
    if not (bundle / "manifest.json").is_file():
        raise ValueError("T13 measured bundle required")
    manifest = read_json(bundle / "manifest.json")
    if (not isinstance(manifest, dict) or not isinstance(manifest.get("development"), list)
            or not isinstance(manifest.get("runtime"), str) or not manifest["runtime"].strip()
            or not isinstance(manifest.get("model_sha256"), str)
            or not re.fullmatch("[0-9a-f]{64}", manifest["model_sha256"])):
        raise ValueError("missing model runtime/hash or complete development inventory")
    manuscripts = corpus()
    verify_split(manuscripts, manifest["development"])
    cases = [r for r in manuscripts if r["split"] == "model"]
    frozen = {ms["group"]: final_reference(references, ms) for ms in cases}
    RNG.shuffle(cases)
    with new_run(dest, "model") as (tmp, key):
        key["runtime"] = manifest["runtime"]
        key["model_sha256"] = manifest["model_sha256"]
        key["development"] = [{"group": r["group"], "split": r["split"], "sha256": digest(r["source"].encode())}
                              for r in manifest["development"]]
        for ms in cases:
            gid, group = new_group(key, ms)
            ref, group["reference"] = frozen[ms["group"]]
            ref_name = "ref-" + gid + ".pdf"
            (tmp / "private" / ref_name).write_bytes(ref)
            (tmp / "reader" / ref_name).write_bytes(blind_pdf(ref))
            pages, catalogs = set(), []
            for mode in RNG.sample(["rules", "model"], 2):
                try:
                    pdf, log, receipt, catalog, evidence = load_arm(bundle / ms["group"] / mode, ms, mode,
                                                                    key["model_sha256"])
                except FileNotFoundError as exc:
                    raise ValueError(f"missing T13 {mode} output: {exc.filename}") from exc
                prefix = tmp / "private" / (gid + "-" + mode)
                prefix.with_suffix(".jsonl").write_bytes(log)
                save_json(prefix.with_suffix(".json"), receipt)
                catalogs.append(catalog)
                pages.add(add_pdf(tmp, group, pdf, evidence))
            if catalogs[0] != catalogs[1]:
                raise ValueError("T13 candidate sets or rule choices differ")
            if len(pages) != 2:
                raise ValueError("identical rendered model and rules pages")

