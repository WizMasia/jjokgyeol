"""Candidate catalog and replay checks for measured vertical paragraph gaps."""
from decimal import Decimal, ROUND_HALF_UP
import json

from app import publication
from app.configuration import unique_fields
from app.style import SP_PER_PT, paragraph_spacing_sp


KINDS = {"paragraph", "heading", "list_item", "table"}
SOURCES = {"manual", "style", "ai", "rule", "default"}
IDENTITY = ("id", "before_line", "after_line", "auto", "left_kind", "right_kind")


def read_report(folder):
    with (folder / "spacing.json").open(encoding="utf-8") as stream:
        rows = json.load(stream, object_pairs_hook=unique_fields)
    if not isinstance(rows, list):
        raise ValueError("invalid spacing report")
    last = 0
    for row in rows:
        key = row["id"]
        if not isinstance(key, str) or not key.isascii() or not key.isdecimal() or str(int(key)) != key:
            raise ValueError("invalid spacing boundary")
        if int(key) <= last:
            raise ValueError("invalid spacing order")
        last = int(key)
    return rows


def measured(row):
    return (row["valid"] is True and type(row["auto"]) is bool
            and row["source"] in SOURCES and row["left_kind"] in KINDS and row["right_kind"] in KINDS
            and all(type(row[key]) is int and row[key] >= 0 for key in
                    ("before_line", "after_line", "gap_sp", "remaining_sp", "page", "lines"))
            and row["page"] > 0 and row["lines"] > 0
            and type(row.get("left_page", row["page"])) is int
            and 0 < row.get("left_page", row["page"]) <= row["page"])


def at_page_break(row):
    return row.get("left_page", row["page"]) < row["page"]


def options(row, body_sp):
    policy = publication.PARAGRAPH_SPACING
    low, high = (Decimal(n) * SP_PER_PT for n in policy["range_pt"])
    gaps = [row["gap_sp"]]
    for ratio in policy["candidate_body_ratios"]:
        scaled = Decimal(body_sp) * ratio
        gaps.append(int(max(low, min(high, scaled)).to_integral_value(rounding=ROUND_HALF_UP)))
    geometry = {key: row[key] for key in ("left_kind", "right_kind", "page", "lines", "remaining_sp")}
    for key in ("left_level", "right_level"):
        if type(row.get(key)) is int:
            geometry[key] = row[key]
    return {f"gap-{i}": dict(geometry, body_sp=body_sp, gap_sp=gap)
            for i, gap in enumerate(dict.fromkeys(gaps)) if low <= gap <= high}


def verify_report(before, after, choices, original_folder):
    requested = None
    for old, new in zip(before, after, strict=True):
        if not measured(new) or any(old[k] != new[k] for k in IDENTITY):
            raise ValueError("unverified spacing boundary")
        key = new["id"]
        source = "ai" if key in choices else old["source"]
        gap = choices.get(key, old["gap_sp"])
        if at_page_break(new):
            gap = 0
        elif at_page_break(old) and key not in choices:
            # The former page edge hid the requested gap. Recover it from the original render.
            if requested is None:
                with (original_folder / "input.json").open(encoding="utf-8") as stream:
                    requested = json.load(stream)["spacing"]
            gap = requested[key].get("gap_sp", paragraph_spacing_sp(publication.PARAGRAPH_SPACING["default_pt"]))
        if new["source"] != source or new["gap_sp"] != gap:
            raise ValueError("unverified spacing selection")
