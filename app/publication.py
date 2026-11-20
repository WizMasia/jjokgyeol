"""Load the installed publication policy and resolve its locked font faces."""

import re
from decimal import Decimal, localcontext
from pathlib import Path

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
            filepath = Path(path)
            if (str(filepath) != path or ".." in filepath.parts or "\x00" in path
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*\.ttf", filepath.name)):
                raise ValueError(f"runtime.lock fonts.{identifier}.{face}.path has an invalid font name")
            if directory is not None and filepath.parent != directory:
                raise ValueError(f"runtime.lock fonts.{identifier}.{face}.path requires a common font directory")
            directory = filepath.parent
            files[identifier][face] = filepath.name
    if labels.keys() != locked.keys():
        raise ValueError(f"publication.json fonts missing {sorted(locked.keys() - labels.keys())}")
    return labels, files, directory


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


_policy = read_config(CONFIG_DIR / "publication.json", parse_float=Decimal)
_keys = {"defaults", "fonts", "space_pct_range", "heading_per_10000", "heading_leading_per_10000",
         "new_paragraph_layout", "list_presets", "default_list_preset", "default_article_expr", "running_defaults", "text_defaults"}
if not isinstance(_policy, dict) or _policy.keys() != _keys:
    difference = sorted(_policy.keys() ^ _keys) if isinstance(_policy, dict) else sorted(_keys)
    raise ValueError(f"publication.json fields: {difference}")
BALANCED_PARAGRAPH_LAYOUT = "balanced"
NEW_PARAGRAPH_LAYOUT = _policy["new_paragraph_layout"]
if NEW_PARAGRAPH_LAYOUT not in (None, BALANCED_PARAGRAPH_LAYOUT):
    raise ValueError("publication.json new_paragraph_layout must be balanced or null")
DEFAULTS = _policy["defaults"]
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
DEFAULT_LIST_PRESET = _policy["default_list_preset"]
if not isinstance(DEFAULT_LIST_PRESET, str) or DEFAULT_LIST_PRESET not in LIST_PRESETS:
    raise ValueError("publication.json default_list_preset must name a list preset")
_lock = read_config(APP_DIR.parent / "runtime.lock")
FONT_LABELS, _FACES, FONT_DIR = _load_fonts(_policy["fonts"], _lock.get("fonts") if isinstance(_lock, dict) else None)
SPACE_LIMITS = _space_limits(_policy["space_pct_range"])
_headings = _policy["heading_per_10000"]
if not isinstance(_headings, list) or len(_headings) != 6:
    raise ValueError("publication.json heading_per_10000 requires six heading scales")
HEADINGS = tuple(_positive_ratio(n, "heading_per_10000") for n in _headings)
HEADING_LEADING = _positive_ratio(_policy["heading_leading_per_10000"], "heading_leading_per_10000")


def engine_policy() -> dict:
    return {
        "fonts": {key: dict(faces) for key, faces in _FACES.items()},
        "heading_per_10000": list(HEADINGS),
        "heading_leading_per_10000": HEADING_LEADING,
    }
