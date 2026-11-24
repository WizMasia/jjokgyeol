"""Candidate catalog and replay checks for measured vertical paragraph gaps."""
from decimal import Decimal, ROUND_HALF_UP
import json

from app.ai.model import PARAGRAPH_LEADING_RATIOS
from app import publication
from app.configuration import unique_fields
from app.style import SP_PER_PT, paragraph_spacing_sp
from app.style import leading_sp


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


def leading_options(row):
    if (not isinstance(row, dict) or row.get("valid") is not True or row.get("auto") is not True
            or row.get("variable") is True or type(row.get("leading_sp")) is not int
            or type(row.get("font_sp")) is not int or type(row.get("lines")) is not int
            or type(row.get("remaining_sp")) is not int or row["font_sp"] <= 0
            or row["lines"] < 2 or row["remaining_sp"] < 0
            or row.get("kind") not in ("paragraph", "heading")
            or type(row.get("level")) is not int or not 0 <= row["level"] <= 6
            or (row["kind"] == "heading") != (row["level"] > 0)):
        return {}
    try:
        rule = leading_sp(row["leading_sp"], stored=True)
    except ValueError:
        return {}
    options = {}
    candidates = dict.fromkeys((rule * ratio + publication.RATIO_UNIT // 2) // publication.RATIO_UNIT
                               for ratio in PARAGRAPH_LEADING_RATIOS)
    for index, candidate in enumerate(candidates):
        try:
            candidate = leading_sp(candidate, stored=True)
        except ValueError:
            continue
        options[f"leading-{index}"] = {key: row[key] for key in ("font_sp", "lines", "remaining_sp", "kind", "level")}
        options[f"leading-{index}"].update(leading_sp=candidate)
    return options


def verify_report(before, after, choices, original_folder):
    requested = default_gap = None
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
                    render_input = json.load(stream)
                requested = render_input["spacing"]
                style_gap = render_input.get("style", {}).get("paragraph_gap_sp")
                if type(style_gap) is int and style_gap >= 0:
                    default_gap = style_gap
                else:
                    policy_gap = render_input.get("policy", {}).get("paragraph_spacing", {}).get("default_pt")
                    default_gap = paragraph_spacing_sp(
                        policy_gap if policy_gap is not None else publication.PARAGRAPH_SPACING["default_pt"]
                    )
            gap = requested.get(key, {}).get("gap_sp", default_gap)
        if new["source"] != source or new["gap_sp"] != gap:
            raise ValueError("unverified spacing selection")
