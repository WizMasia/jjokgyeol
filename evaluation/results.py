"""Capture one complete, still-blind ballot and score the prespecified comparisons."""
import csv
import json
import math
import os
import tempfile
from collections import Counter, defaultdict
from io import StringIO
from pathlib import Path
from statistics import median

from evaluation.model import paragraph_numbers
from evaluation.packet import HEADER, digest, read_json


def observations(key, sheet):
    reader = csv.DictReader(StringIO(sheet.removeprefix("\ufeff"), newline=""), strict=True)
    if reader.fieldnames != HEADER:
        raise ValueError("wrong reading-sheet columns")
    groups = key["groups"]
    expected = {(g, s) for g in groups for s in groups[g]["samples"]}
    seen, picks, rows = set(), set(), []
    for row in reader:
        if None in row or not all(isinstance(v, str) and v.strip() for v in row.values()):
            raise ValueError("missing or excess ballot fields")
        pair = row["group"], row["sample"]
        if row["run"] != key["run"] or pair not in expected or pair in seen:
            raise ValueError("foreign or duplicate ballot row")
        if row["preferred"] not in {"0", "1"}:
            raise ValueError("preferred must be 0 or 1")
        paragraph = row["paragraph"]
        if (paragraph == "none") != (row["reason"].strip() == "none"):
            raise ValueError("give paragraph and reason together")
        if paragraph != "none":
            paragraph_numbers(paragraph, groups[row["group"]]["paragraphs"])
        seen.add(pair)
        if row["preferred"] == "1":
            picks.add(row["group"])
        rows.append(row)
    if seen != expected or picks != set(groups):
        raise ValueError("incomplete comparisons or missing preference")
    return rows


def distribution(values):
    values = sorted(values)
    return {"n": len(values), "min_ms": values[0], "median_ms": median(values),
            "p95_ms": values[math.ceil(.95 * len(values))-1], "max_ms": values[-1]}


def timing(run):
    key = read_json(run / "private/key.json")
    by = defaultdict(list)
    for group in key["groups"].values():
        for s in group["samples"].values():
            by["all"].append(s["ms"])
            label = s.get("applied") or f'{s["style"]["body_font"]} {s["style"]["body_pt"]}pt'
            by[label].append(s["ms"])
    return {label: distribution(values) for label, values in sorted(by.items())}


def freeze(path, data):
    # Link a finished file exclusively, even if two CLI submissions arrive together.
    with tempfile.NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8") as tmp:
        json.dump(data, tmp, ensure_ascii=False, allow_nan=False)
        tmp.flush()
        os.link(tmp.name, path)


def capture(run, sheet, reader):
    if not reader.strip():
        raise ValueError("reader alias required")
    path = run / "private/key.json"
    key = read_json(path)
    source = Path(sheet).read_bytes().decode("utf-8")
    observations(key, source)
    for gid, group in key["groups"].items():
        for sid, sample in group["samples"].items():
            if digest((run / "reader" / (sid + ".pdf")).read_bytes()) != sample["blind_sha256"]:
                raise ValueError("reader PDF changed")
        if "reference" in group:
            if digest((run / "reader" / ("ref-" + gid + ".pdf")).read_bytes()) != group["blind_reference_sha256"]:
                raise ValueError("reader reference changed")
    freeze(run / "private/ballot.json", {"reader": reader, "key_sha256": digest(path.read_bytes()), "csv": source})


def timing_decision(run, decision, by):
    path = run / "private/key.json"
    if read_json(path)["experiment"] != "model" or decision not in {"accepted", "rejected"} or not by.strip():
        raise ValueError("model timing requires user's acceptance or rejection and alias")
    freeze(run / "private/timing-decision.json", {"by": by, "decision": decision,
           "key_sha256": digest(path.read_bytes()), "timing": timing(run)})


def score(run):
    path = run / "private/key.json"
    key = read_json(path)
    report = {"run": key["run"], "experiment": key["experiment"], "timing": timing(run),
              "status": "awaiting_reader", "recommend_model": None}
    ballot = run / "private/ballot.json"
    if not ballot.exists():
        return report
    submission = read_json(ballot)
    key_hash = digest(path.read_bytes())
    if submission["key_sha256"] != key_hash:
        raise ValueError("ballot key changed")
    rows = observations(key, submission["csv"])
    preferred = defaultdict(list)
    counts = Counter({"model": 0, "rules": 0, "tie": 0})
    comparisons, notes = [], []
    violations = mismatches = 0
    for row in rows:
        group = key["groups"][row["group"]]
        s = group["samples"][row["sample"]]
        arm = s.get("applied", s["style"]["ko_tracking_pct"])
        notes.append({**row, "manuscript": group["manuscript"], "condition": arm})
        if row["preferred"] == "1":
            preferred[row["group"]].append(arm)
        violations += len(s.get("constraint_violations", []))
        mismatches += s.get("end_mismatches", 0)
    for gid, picks in preferred.items():
        group = key["groups"][gid]
        summary = {"group": gid, "manuscript": group["manuscript"], "preferred": picks}
        if key["experiment"] == "model":
            counts[picks[0] if len(picks) == 1 else "tie"] += 1
            summary["reference_revision"] = group["reference"][-1]["revision"]
        else:
            style = next(iter(group["samples"].values()))["style"]
            summary.update(font=style["body_font"], body_pt=style["body_pt"])
        comparisons.append(summary)
    report.update(status="scored", reader=submission["reader"], comparisons=comparisons, observations=notes)
    if key["experiment"] == "model":
        decision_path = run / "private/timing-decision.json"
        vote = read_json(decision_path) if decision_path.exists() else None
        if vote and vote["key_sha256"] != key_hash:
            raise ValueError("timing decision key changed")
        accepted = vote is not None and vote["decision"] == "accepted"
        report.update(counts=dict(counts), constraint_violations=violations, end_mismatches=mismatches,
                      timing_decision=vote, recommend_model=accepted and counts["model"] > counts["rules"]
                      and violations == 0 and mismatches == 0)
    return report
