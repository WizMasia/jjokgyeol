"""Load the installed publication policy and resolve its locked font faces."""

import re
from decimal import Decimal, localcontext
from pathlib import Path, PurePosixPath

from app.configuration import APP_DIR, CONFIG_DIR, read_config
from app.list_marks import format_mark

RATIO_UNIT = 10000
MAX_SAFE_INTEGER = (1 << 53) - 1


def _load_fonts(choices, locked):
    if not isinstance(choices, list) or not choices or not isinstance(locked, dict):
        raise ValueError("publication.json and runtime.lock require fonts")
    labels, files = {}, {}
    directory = None
    for choice in choices:
        if not isinstance(choice, dict) or choice.keys() != {"id", "label"}:
            raise ValueError("publication.json fonts require id and label")
        identifier, label = choice["id"], choice["label"]
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", identifier):
            raise ValueError("publication.json fonts.id is invalid")
        if identifier in labels or not isinstance(label, str) or not label.strip():
            raise ValueError(f"publication.json fonts.{identifier}: duplicate ID or missing label")
        labels[identifier] = label
        faces = locked.get(identifier)
        if not isinstance(faces, dict):
            raise ValueError(f"runtime.lock fonts.{identifier} missing")
        files[identifier] = {}
        for face in ("regular", "bold"):
            record = faces.get(face)
            path = record.get("path") if isinstance(record, dict) else None
            if not isinstance(path, str) or not path.startswith("/"):
                raise ValueError(f"runtime.lock fonts.{identifier}.{face}.path must be absolute")
            filepath = PurePosixPath(path)
            if (str(filepath) != path or ".." in filepath.parts or "\x00" in path
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*\.ttf", filepath.name)):
                raise ValueError(f"runtime.lock fonts.{identifier}.{face}.path has an invalid font name")
            if directory is not None and filepath.parent != directory:
                raise ValueError(f"runtime.lock fonts.{identifier}.{face}.path requires a common font directory")
            directory = filepath.parent
            files[identifier][face] = filepath.name
    if labels.keys() != locked.keys():
        raise ValueError(f"publication.json fonts missing {sorted(locked.keys() - labels.keys())}")
    return labels, files, Path(str(directory))


def _positive_ratio(value, field):
    if type(value) is not int or not 0 < value <= MAX_SAFE_INTEGER:
        raise ValueError(f"publication.json {field} must be a positive exact integer")
    return value


def _space_limits(bounds):
    if not isinstance(bounds, list) or len(bounds) != 2:
        raise ValueError("publication.json space_pct_range requires two percentages")
    limits = []
    for number in bounds:
        if type(number) not in (int, Decimal) or not Decimal(number).is_finite():
            raise ValueError("publication.json space_pct_range requires finite percentages")
        percent = Decimal(number)
        if not Decimal("0.01") <= percent <= MAX_SAFE_INTEGER:
            raise ValueError("publication.json space_pct_range is out of range")
        with localcontext() as ctx:
            ctx.prec = len(percent.as_tuple().digits) + 2
            scaled = percent * 100
        if scaled != scaled.to_integral_value() or scaled > MAX_SAFE_INTEGER:
            raise ValueError("publication.json space_pct_range requires exact 0.01% units")
        limits.append(int(scaled))
    if limits[0] > limits[1]:
        raise ValueError("publication.json space_pct_range must be ordered")
    return tuple(limits)


def _paragraph_spacing(fields):
    if not isinstance(fields, dict) or fields.keys() != {"range_pt", "default_pt", "candidate_body_ratios"}:
        raise ValueError("publication.json paragraph_spacing requires range_pt, default_pt and candidate_body_ratios")
    bounds, default, ratios = fields["range_pt"], fields["default_pt"], fields["candidate_body_ratios"]
    if (not isinstance(bounds, list) or len(bounds) != 2 or not isinstance(ratios, list) or not ratios
            or any(type(n) not in (int, Decimal) or not 0 <= n <= MAX_SAFE_INTEGER
                   for n in (*bounds, default, *ratios))
            or not bounds[0] <= default <= bounds[1] or min(ratios) <= 0):
        raise ValueError("publication.json paragraph_spacing requires an in-range default and positive body ratios")
    return fields


def _paragraph_leading(fields):
    if not isinstance(fields, dict) or fields.keys() != {"candidate_ratios"}:
        raise ValueError("publication.json paragraph_leading requires candidate_ratios")
    ratios = fields["candidate_ratios"]
    if (not isinstance(ratios, list) or not ratios
            or any(type(n) is not int or not 1 <= n <= MAX_SAFE_INTEGER for n in ratios)):
        raise ValueError("publication.json paragraph_leading ratios must be positive exact integers")
    return fields


_policy = read_config(CONFIG_DIR / "publication.json", parse_float=Decimal)
_keys = {"defaults", "page_presets_mm", "fonts", "space_pct_range", "heading_per_10000", "heading_leading_per_10000",
         "paragraph_spacing", "paragraph_leading", "new_paragraph_layout", "justified_spacing", "list_presets", "default_list_preset", "default_article_expr", "running_defaults", "text_defaults"}
if not isinstance(_policy, dict) or (_policy.keys() - {"new_list_preset"}) != _keys:
    difference = sorted((_policy.keys() - {"new_list_preset"}) ^ _keys) if isinstance(_policy, dict) else sorted(_keys)
    raise ValueError(f"publication.json fields: {difference}")
BALANCED_PARAGRAPH_LAYOUT = "balanced"
PARAGRAPH_LAYOUTS = (BALANCED_PARAGRAPH_LAYOUT, "justified")
NEW_PARAGRAPH_LAYOUT = _policy["new_paragraph_layout"]
if NEW_PARAGRAPH_LAYOUT not in (None, *PARAGRAPH_LAYOUTS):
    raise ValueError("publication.json new_paragraph_layout must be balanced, justified or null")
JUSTIFIED_SPACING = _policy["justified_spacing"]
if not isinstance(JUSTIFIED_SPACING, dict) or JUSTIFIED_SPACING.keys() != {"ko_tracking_bounds_pct", "space_pct"}:
    raise ValueError("publication.json justified_spacing requires ko_tracking_bounds_pct and space_pct")
NEW_SPACING = JUSTIFIED_SPACING if NEW_PARAGRAPH_LAYOUT == "justified" else {}
PARAGRAPH_SPACING = _paragraph_spacing(_policy["paragraph_spacing"])
PARAGRAPH_LEADING = _paragraph_leading(_policy["paragraph_leading"])
DEFAULTS = _policy["defaults"]
PAGE_PRESETS_MM = _policy["page_presets_mm"]
RUNNING_DEFAULTS = _policy["running_defaults"]
TEXT_DEFAULTS = _policy["text_defaults"]
DEFAULT_ARTICLE_EXPR = _policy["default_article_expr"]
try:
    format_mark(DEFAULT_ARTICLE_EXPR, 1)
except ValueError as error:
    raise ValueError(f"publication.json default_article_expr: {error}") from error
LIST_PRESETS = _policy["list_presets"]
if not isinstance(LIST_PRESETS, dict) or LIST_PRESETS.keys() != {"legal", "report"}:
    raise ValueError("publication.json list_presets requires legal and report")
# Installations may mount their own publication.json. New numbering defaults ship separately.
_numbering = read_config(CONFIG_DIR / "numbering.json", parse_float=Decimal)
if not isinstance(_numbering, dict) or _numbering.keys() != {"line_number_gap_pt", "outline_levels"}:
    raise ValueError("numbering.json requires line_number_gap_pt and outline_levels")
LINE_NUMBER_GAP_PT = _numbering["line_number_gap_pt"]
if type(LINE_NUMBER_GAP_PT) is not int or LINE_NUMBER_GAP_PT <= 0:
    raise ValueError("numbering.json line_number_gap_pt must be a positive integer")
LIST_PRESETS = {**LIST_PRESETS, "outline": _numbering["outline_levels"]}
DEFAULT_LIST_PRESET = _policy["default_list_preset"]
if not isinstance(DEFAULT_LIST_PRESET, str) or DEFAULT_LIST_PRESET not in LIST_PRESETS:
    raise ValueError("publication.json default_list_preset must name a list preset")
NEW_LIST_PRESET = _policy.get("new_list_preset")
if "new_list_preset" in _policy and (not isinstance(NEW_LIST_PRESET, str) or NEW_LIST_PRESET not in LIST_PRESETS):
    raise ValueError("publication.json new_list_preset must name a list preset")
_lock = read_config(APP_DIR.parent / "runtime.lock")
FONT_LABELS, _FACES, FONT_DIR = _load_fonts(_policy["fonts"], _lock.get("fonts") if isinstance(_lock, dict) else None)
SPACE_LIMITS = _space_limits(_policy["space_pct_range"])
_headings = _policy["heading_per_10000"]
if not isinstance(_headings, list) or len(_headings) != 6:
    raise ValueError("publication.json heading_per_10000 requires six heading scales")
HEADINGS = tuple(_positive_ratio(n, "heading_per_10000") for n in _headings)
HEADING_LEADING = _positive_ratio(_policy["heading_leading_per_10000"], "heading_leading_per_10000")


def paragraph_spacing_settings() -> dict:
    # The installation is parsed with Decimal; the editor and TeX consume JSON numbers.
    def number(value):
        return float(value) if isinstance(value, Decimal) else value

    return {"range_pt": list(map(number, PARAGRAPH_SPACING["range_pt"])),
            "default_pt": number(PARAGRAPH_SPACING["default_pt"]),
            "candidate_body_ratios": list(map(number, PARAGRAPH_SPACING["candidate_body_ratios"]))}


def engine_policy() -> dict:
    return {
        "paragraph_spacing": paragraph_spacing_settings(),
        "font_order": list(_FACES),
        "fonts": {key: dict(faces) for key, faces in _FACES.items()},
        "heading_per_10000": list(HEADINGS),
        "heading_leading_per_10000": HEADING_LEADING,
    }
