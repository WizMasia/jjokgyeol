"""Freeze editor revisions and bind genuine T13 selections to the PDF under review."""
import fcntl
import json
import math
import re
import tempfile
from itertools import accumulate
from pathlib import Path
from unicodedata import normalize

from tests.pdf_metrics import read_pages

from evaluation import packet
from evaluation.packet import (
    PAGE, RNG, add_pdf, blind_pdf, compact, corpus, digest, new_group,
    new_run, paragraphs, read_json, save_json, verify_split,
)

MODEL_STYLE = {**PAGE, "body_font": "nanum-myeongjo", "body_pt": 11, "leading_pt": 17,
               "ko_tracking_pct": 0, "ko_tracking_bounds_pct": [-7, 0], "space_pct": [70, 130]}

BP_PER_SP = 72 / 72.27 / 65536
JUSTIFIED_MODEL_STYLE = {**MODEL_STYLE, "paragraph_layout": "justified", "ko_tracking_bounds_pct": [-15, 15]}


def model_style(justified):
    return JUSTIFIED_MODEL_STYLE if justified else MODEL_STYLE


def inspect_pdf(pdf, manuscript):
    if not isinstance(pdf, bytes) or not pdf.startswith(b"%PDF-"):
        raise ValueError("missing PDF")
    text = []
    ends = []
    violations = []
    size = 0
    word_ends = set()
    word_end = 0
    for word in manuscript["source"].split():
        word_end += len(compact(word))
        word_ends.add(word_end)
    for page_no, page in enumerate(read_pages(pdf), 1):
        mm = [value * 25.4 / 72 for value in (page.width, page.height)]
        if any(abs(a-b) > .03 for a, b in zip(mm, MODEL_STYLE["page_mm"], strict=True)):
            raise ValueError("wrong reference or candidate page size")
        top, right, bottom, left = (mm * 72 / 25.4 for mm in MODEL_STYLE["margins_mm"])
        right_edge, top_edge = page.width - right, page.height - top
        for line_no, line in enumerate(page.lines, 1):
            chars = [c for c in line.chars if c.text.strip()]
            location = f"PDF page {page_no}, line {line_no}"
            if any(c.x0 < left - .05 or c.x0 + c.advance > right_edge + .05
                   or c.y0 < bottom - .05 or c.y1 > top_edge + .05 for c in chars):
                violations.append(location + ": margin")
            index = size
            for a, b in zip(chars, chars[1:]):
                index += len(compact(a.text))
                gap = b.x0 - a.x0 - a.advance
                if index in word_ends:
                    # Locked NanumMyeongjo space advance, independently calibrated in T3.
                    natural = a.size * 307 / 1024
                    if not natural * .70 - .05 <= gap <= natural * 1.30 + .05:
                        violations.append(f"{location}, glyph {index}: space")
                elif "가" <= a.text <= "힣" and "가" <= b.text <= "힣":
                    if not -.07 * a.size - .05 <= gap <= .05:
                        violations.append(f"{location}, glyph {index}: tracking")
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
    return ends, violations


def paragraph_numbers(value, maximum):
    parts = value.split(",")
    if not all(re.fullmatch(r"[1-9]\d{0,5}", p) and int(p) <= maximum for p in parts):
        raise ValueError("invalid paragraph numbers")
    if len(set(parts)) != len(parts):
        raise ValueError("duplicate paragraph numbers")


def revision(root, name, pdf, editor, paragraph, reason, final, *, justified=False):
    ms = next((r for r in corpus() if r["group"] == name and r["split"] == "model"), None)
    if ms is None or not editor.strip() or not reason.strip() or type(final) is not bool:
        raise ValueError("reference needs a manuscript, editor and reason")
    if paragraph != "none":
        paragraph_numbers(paragraph, len(paragraphs(ms["source"])))
    style = model_style(justified)
    if justified:
        verification = packet.verify_justified_pdf(pdf, ms["source"], style)
    else:
        _, violations = inspect_pdf(pdf, ms)
        if any(v.endswith(": margin") for v in violations):
            raise ValueError("reference text outside specified margins")
    folder = Path(root) / name
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (folder / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        previous = sorted(p for p in folder.iterdir() if p.name.isdecimal())
        if previous:
            last = read_json(previous[-1] / "record.json")
            if last["style"] != style:
                raise ValueError("reference style differs from this protocol")
            if last["final"]:
                raise ValueError("reference already final")
        number = len(previous) + 1
        record = {"revision": number, "source_sha256": digest(ms["source"].encode()),
                  "pdf_sha256": digest(pdf), "style": style, "editor": editor,
                  "paragraph": paragraph, "reason": reason, "final": final}
        if justified:
            record["verification"] = verification
        with tempfile.TemporaryDirectory(dir=folder) as stage:
            path = Path(stage)
            (path / "page.pdf").write_bytes(pdf)
            save_json(path / "record.json", record)
            path.rename(folder / f"{number:06d}")
    return record


def final_reference(root, ms, *, justified=False):
    folder = Path(root) / ms["group"]
    history = []
    pdf = None
    style = model_style(justified)
    for path in sorted(folder.glob("[0-9]*")):
        record = read_json(path / "record.json")
        pdf = (path / "page.pdf").read_bytes()
        if (record["source_sha256"] != digest(ms["source"].encode()) or record["style"] != style
                or record["pdf_sha256"] != digest(pdf) or record["revision"] != len(history) + 1):
            raise ValueError("reference source, revision or PDF changed")
        if justified and record.get("verification") != packet.verify_justified_pdf(pdf, ms["source"], style):
            raise ValueError("reference PDF verification changed")
        history.append(record)
    if not history or history[-1]["final"] is not True:
        raise ValueError("final editor reference required for " + ms["group"])
    return pdf, history


def selected_lines(rows, ms, mode, *, justified=False):
    style = model_style(justified)
    low, high = (pct * 100 for pct in style["ko_tracking_bounds_pct"])
    min_space, max_space = (pct * 100 for pct in style["space_pct"])
    if justified:
        _, right, _, left_margin = style["margins_mm"]
        measure_bp = (style["page_mm"][0] - right - left_margin) * 72 / 25.4
    parts = paragraphs(ms["source"])
    if len(rows) != len(parts):
        raise ValueError("missing paragraph log")
    choices, ends, catalog, violations = [], [], [], []
    base = 0
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
        if mode == "rules" and chosen != rule:
            raise ValueError("baseline selected a non-rule candidate")
        catalog.append((rule, by_id))
        choices.append(chosen)
        lines = by_id[chosen]
        if not isinstance(lines, list) or not lines:
            raise ValueError("missing selected lines")
        if justified:
            word_ends = set(accumulate(len(word) for word in normalize("NFC", part).split()))
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
                              ("tracking", low <= tracking <= high), ("space", min_space <= space <= max_space)]:
                if not ok:
                    violations.append(f"block {block}, line {i}: {field}")
            if justified:
                if end not in word_ends or line.get("split_word") is not False:
                    violations.append(f"block {block}, line {i}: word boundary")
                if type(line.get("start_sp")) is not int or line["start_sp"] != 0:
                    violations.append(f"block {block}, line {i}: indent")
                if abs(width * BP_PER_SP - measure_bp) > packet.PDF_TOLERANCE:
                    violations.append(f"block {block}, line {i}: width")
                if i < len(lines) and left * BP_PER_SP > packet.PDF_TOLERANCE:
                    violations.append(f"block {block}, line {i}: right edge")
        if previous != (max(word_ends) if justified else len(compact(part))):
            raise ValueError("end_glyph does not cover manuscript paragraph")
        base += previous
    return catalog, {"choices": choices, "selected_ends": ends, "constraint_violations": violations}


def load_arm(folder, ms, mode, model_hash, *, justified=False, audit=False):
    receipt = read_json(folder / "measurement.json")
    pdf = (folder / "render.pdf").read_bytes()
    log = (folder / "paragraph-candidates.jsonl").read_bytes()
    expected_path = "rule" if mode == "rules" else "model"
    style = model_style(justified)
    if (not isinstance(receipt, dict) or receipt.get("source_sha256") != digest(ms["source"].encode())
            or receipt.get("pdf_sha256") != digest(pdf) or receipt.get("log_sha256") != digest(log)):
        raise ValueError("T13 receipt differs from source or artifact hashes")
    rejection = None
    if (receipt.get("applied") != expected_path or receipt.get("style") != style
            or receipt.get("model_sha256") != (model_hash if mode == "model" else None)):
        rejection = "T13 measurement differs from mode, runtime or style"
        if not audit:
            raise ValueError(rejection)
    elapsed = receipt.get("elapsed_ms")
    if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed <= 0:
        raise ValueError("missing positive measured elapsed_ms")
    rows = [json.loads(line) for line in log.decode("utf-8").splitlines()]
    catalog, evidence = selected_lines(rows, ms, mode, justified=justified)
    if justified:
        try:
            verification = packet.verify_justified_pdf(pdf, ms["source"], style)
        except ValueError as exc:
            if not audit:
                raise
            found, violations = None, [str(exc)]
        else:
            evidence["verification"] = verification
            found, violations = verification["line_ends"], []
    else:
        found, violations = inspect_pdf(pdf, ms)
    evidence["constraint_violations"].extend(violations)
    selected = set(evidence["selected_ends"])
    mismatches = None if found is None else max(len(selected-set(found)), len(set(found)-selected))
    evidence.update(applied=mode, style=style, ms=elapsed, pdf_ends=found, end_mismatches=mismatches)
    if justified and evidence["constraint_violations"]:
        rejection = rejection or evidence["constraint_violations"][0]
    if justified and found is not None and found != evidence["selected_ends"]:
        rejection = rejection or "selected ends differ from printed PDF lines"
    if audit:
        evidence.update(t14_rejection=rejection, pdf_constraint_violations=violations,
                        changed_blocks=[r["block"] for r in rows if r["rule_id"] != r["selected_id"]])
    elif rejection:
        raise ValueError(rejection)
    return pdf, log, receipt, catalog, evidence


def model_pairs(dest, references, bundle, *, justified=False):
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
    frozen = {ms["group"]: final_reference(references, ms, justified=justified) for ms in cases}
    RNG.shuffle(cases)
    with new_run(dest, "model") as (tmp, key):
        if justified:
            key["protocol"] = 2
        key["runtime"] = manifest["runtime"]
        key["model_sha256"] = manifest["model_sha256"]
        key["development"] = [{"group": r["group"], "split": r["split"], "sha256": digest(r["source"].encode())}
                              for r in manifest["development"]]
        for ms in cases:
            gid, group = new_group(key, ms)
            ref, group["reference"] = frozen[ms["group"]]
            ref_name = "ref-" + gid + ".pdf"
            (tmp / "private" / ref_name).write_bytes(ref)
            blind_ref = blind_pdf(ref)
            (tmp / "reader" / ref_name).write_bytes(blind_ref)
            group["blind_reference_sha256"] = digest(blind_ref)
            catalogs = []
            for mode in RNG.sample(["rules", "model"], 2):
                try:
                    pdf, log, receipt, catalog, evidence = load_arm(bundle / ms["group"] / mode, ms, mode,
                                                                    key["model_sha256"], justified=justified)
                except FileNotFoundError as exc:
                    raise ValueError(f"missing T13 {mode} output: {exc.filename}") from exc
                prefix = tmp / "private" / (gid + "-" + mode)
                prefix.with_suffix(".jsonl").write_bytes(log)
                save_json(prefix.with_suffix(".json"), receipt)
                catalogs.append(catalog)
                add_pdf(tmp, group, pdf, evidence)
            if catalogs[0] != catalogs[1]:
                raise ValueError("T13 candidate sets or rule choices differ")
            first, second = group["samples"].values()
            group["same_choices"] = first["choices"] == second["choices"]
            group["same_pages"] = first["blind_sha256"] == second["blind_sha256"]
