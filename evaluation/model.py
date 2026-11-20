"""Freeze editor revisions and bind genuine T13 selections to the PDF under review."""
import fcntl
import importlib
import re
import tempfile
from copy import deepcopy
from pathlib import Path

from tests.pdf_metrics import read_pages

from evaluation.packet import (
    PAGE, RNG, add_pdf, blind_pdf, compact, corpus, digest, measured, new_group,
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


def load_provider():
    try:
        return importlib.import_module("app.ai.evaluation")
    except ModuleNotFoundError as exc:
        if exc.name not in {"app.ai", "app.ai.evaluation"}:
            raise
        raise ValueError("T13 adapter app.ai.evaluation required") from exc


def check_result(result, ms, mode, model_hash):
    if not isinstance(result, dict) or result.get("applied") != mode:
        raise ValueError("missing T13 result or fallback")
    if (result.get("style") != MODEL_STYLE or result.get("source_sha256") != digest(ms["source"].encode())
            or result.get("model_sha256") != (model_hash if mode == "model" else None)):
        raise ValueError("wrong T13 style, source or model")
    choices = result.get("choices")
    if (not isinstance(choices, list) or len(choices) != len(paragraphs(ms["source"]))
            or not all(isinstance(c, str) and c.strip() for c in choices)):
        raise ValueError("missing paragraph selections")
    found = pdf_ends(result.get("pdf"), ms)
    ends = result.get("selected_ends")
    if (not isinstance(ends, list) or not ends or not all(type(e) is int and e > 0 for e in ends)
            or ends != sorted(set(ends)) or ends[-1] != found[-1]):
        raise ValueError("invalid selected line ends")
    violations = result.get("constraint_violations")
    if not isinstance(violations, list) or not all(isinstance(v, str) and v.strip() for v in violations):
        raise ValueError("missing constraint audit")
    return {"applied": mode, "style": MODEL_STYLE, "choices": choices, "selected_ends": ends,
            "pdf_ends": found, "end_mismatches": max(len(set(ends)-set(found)), len(set(found)-set(ends))),
            "constraint_violations": violations}


def model_pairs(dest, references):
    provider = load_provider()
    inventory = provider.inventory()
    if (not isinstance(inventory, dict) or not isinstance(inventory.get("development"), list)
            or not isinstance(inventory.get("runtime"), str) or not inventory["runtime"].strip()
            or not isinstance(inventory.get("model_sha256"), str)
            or not re.fullmatch("[0-9a-f]{64}", inventory["model_sha256"])):
        raise ValueError("missing model runtime/hash or complete development inventory")
    manuscripts = corpus()
    verify_split(manuscripts, inventory["development"])
    cases = [r for r in manuscripts if r["split"] == "model"]
    frozen = {ms["group"]: final_reference(references, ms) for ms in cases}
    RNG.shuffle(cases)
    with new_run(dest, "model") as (tmp, key):
        key["runtime"] = inventory["runtime"]
        key["model_sha256"] = inventory["model_sha256"]
        key["development"] = [{"group": r["group"], "split": r["split"], "sha256": digest(r["source"].encode())}
                              for r in inventory["development"]]
        for ms in cases:
            gid, group = new_group(key, ms)
            ref, group["reference"] = frozen[ms["group"]]
            ref_name = "ref-" + gid + ".pdf"
            (tmp / "private" / ref_name).write_bytes(ref)
            (tmp / "reader" / ref_name).write_bytes(blind_pdf(ref))
            pages = set()
            for mode in RNG.sample(["rules", "model"], 2):
                result, ms_elapsed = measured(provider.render, ms["source"], deepcopy(MODEL_STYLE), mode)
                evidence = check_result(result, ms, mode, inventory["model_sha256"])
                pages.add(add_pdf(tmp, group, result["pdf"], {**evidence, "ms": ms_elapsed}))
            if len(pages) != 2:
                raise ValueError("identical rendered model and rules pages")

