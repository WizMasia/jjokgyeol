"""Convert editor units once and validate the integer style consumed by LuaTeX."""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, MAX_EMAX, MIN_EMIN, ROUND_HALF_UP, localcontext
from fractions import Fraction

from app import publication
from app.document import InputError
from app.list_marks import format_mark
from app.running_heads import RUNNING_FACES, RUNNING_ROLES, SLOTS, parse_running_text


SP_PER_PT = 65536
# 72.27 TeX pt / 25.4 mm = 118407168 / 635 sp per mm.
MM_NUMERATOR = 118407168
DENOMINATOR = 635
MAX_DIMENSION_PT = 16383
# The largest configured heading and the body must both stay below TeX's 2048pt font limit.
MAX_BODY_PT = (2048 * publication.RATIO_UNIT - 1) // max(publication.RATIO_UNIT, *publication.HEADINGS)
MAX_PAGE_MM = MAX_DIMENSION_PT * 2540 // 7227
MAX_SAFE_INTEGER = publication.MAX_SAFE_INTEGER
NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def _whole_mm(mm):
    return (mm * MM_NUMERATOR + DENOMINATOR // 2) // DENOMINATOR


def _rounded(numerator, denominator):
    return int((Decimal(numerator) / denominator).to_integral_value(rounding=ROUND_HALF_UP))


# Multipliers share one denominator so checks on unrounded mm never divide.
EDITOR_UNITS = {
    "page_mm": ("page_sp", MM_NUMERATOR),
    "margins_mm": ("margins_sp", MM_NUMERATOR),
    "body_pt": ("body_sp", SP_PER_PT * DENOMINATOR),
    "leading_pt": ("leading_sp", SP_PER_PT * DENOMINATOR),
    "first_indent_mm": ("first_indent_sp", MM_NUMERATOR),
    "rest_indent_mm": ("rest_indent_sp", MM_NUMERATOR),
    "ko_tracking_pct": ("ko_tracking_per_10000", 100 * DENOMINATOR),
    "space_pct": ("space_per_10000", 100 * DENOMINATOR),
}
OPTIONAL_UNITS = {
    "gutter_mm": ("gutter_sp", MM_NUMERATOR),
    "ko_tracking_bounds_pct": ("ko_tracking_bounds_per_10000", 100 * DENOMINATOR),
}
ARRAY_LENGTHS = {"page_sp": 2, "margins_sp": 4, "space_per_10000": 2, "ko_tracking_bounds_per_10000": 2}
HEADING_ROLES = tuple(f"heading{level}" for level in range(1, len(publication.HEADINGS) + 1))
TEXT_ROLES = ("body", *HEADING_ROLES, "note", "gloss")

FONTS = ("body_font", "heading_font")
PAGE_ERROR = f"판형의 폭과 높이는 0보다 크고 {MAX_PAGE_MM}mm 이하여야 합니다."


def _number(value, stored, context):
    if type(value) is int or isinstance(value, Decimal):
        number = Decimal(value)
    elif not stored and type(value) is float:
        number = Decimal(str(value))
    elif not stored and isinstance(value, str) and NUMBER.fullmatch(value):
        number = Decimal(value)
    else:
        raise ValueError
    if not number.is_finite():
        raise ValueError
    if stored:
        if number != number.to_integral_value() or number.copy_abs() > MAX_SAFE_INTEGER:
            raise ValueError
        return int(number)
    # Every supported field is smaller than this. Keep huge exponents out of arithmetic.
    if number.copy_abs() > MAX_SAFE_INTEGER:
        return Decimal(-MAX_SAFE_INTEGER if number < 0 else MAX_SAFE_INTEGER)
    # Two operands may be multiplied. Keep all their input digits plus unit factors.
    context.prec = max(context.prec, 2 * len(number.as_tuple().digits) + 32)
    return number


def _pt(sp):
    return format(Decimal(sp) / SP_PER_PT, "f")


def _map_numbers(key, value, convert):
    if key not in ARRAY_LENGTHS:
        return convert(value)
    if not isinstance(value, (tuple, list)) or len(value) != ARRAY_LENGTHS[key]:
        raise ValueError
    return tuple(convert(item) for item in value)


def _remaining(total, offsets):
    # Subtract the largest first so a tiny positive offset survives cancellation.
    for offset in sorted(offsets, reverse=True):
        total -= offset
    return total


def _line_clearance(width, margins, gutter=None, indent=0):
    return _remaining(width, (margins[1], margins[3], gutter or 0, indent))


@dataclass(frozen=True)
class ListLevel:
    expr: str
    indent_sp: int
    gap_sp: int
    font: str | None = None
    font_sp: int | None = None
    weight: str | None = None
    mark_weight: str | None = None
    indent_origin: str | None = None

    def to_stored(self) -> dict:
        return {k: v for k, v in vars(self).items() if v is not None}

    def to_editor(self) -> dict:
        with localcontext() as context:
            context.prec = 40
            row = self.to_stored()
            row["indent_mm"] = _mm(row.pop("indent_sp"), 0)
            row["gap_mm"] = _mm(row.pop("gap_sp"), 0)
            if self.font_sp is not None:
                row["font_pt"] = _pt(row.pop("font_sp"))
            return row


def _list_levels(rows, *, stored, field="list_levels", base=None):
    if not isinstance(rows, (list, tuple)) or not rows:
        raise InputError({field: "목록 양식에는 한 단계 이상이 필요합니다."})
    distance_keys = ("indent_sp", "gap_sp") if stored else ("indent_mm", "gap_mm")
    expected = ("expr", *distance_keys)
    size_key = "font_sp" if stored else "font_pt"
    optional = {"font", size_key, "weight", "mark_weight", "indent_origin"}
    maximum = _whole_mm(MAX_PAGE_MM) if stored else MAX_PAGE_MM
    levels = []
    with localcontext() as context:
        context.prec = 64
        context.Emax, context.Emin = MAX_EMAX, MIN_EMIN
        for index, row in enumerate(rows):
            path = f"{field}[{index}]"
            stage = f"목록 {index + 1}단계"
            if not isinstance(row, dict) or row.keys() - set(expected) - optional:
                raise InputError({path: f"{stage}의 필드 구성을 확인하세요."})
            for key in expected:
                if key not in row:
                    raise InputError({f"{path}.{key}": f"{stage}에 필요한 값이 없습니다."})
            expr = row["expr"]
            if not isinstance(expr, str):
                raise InputError({f"{path}.expr": f"{stage}의 표현식은 문자열이어야 합니다."})
            try:
                format_mark(expr, 1)
            except ValueError as error:
                raise InputError({f"{path}.expr": f"{stage} {error}"}) from None
            distances = []
            for key in distance_keys:
                try:
                    value = _number(row[key], stored, context)
                    if not 0 <= value <= maximum:
                        raise ValueError
                    distances.append(value if stored else _rounded(value * MM_NUMERATOR, DENOMINATOR))
                except (ValueError, InvalidOperation):
                    unit = "sp 정수" if stored else "mm"
                    raise InputError({f"{path}.{key}": f"{stage}의 거리는 0~{maximum} {unit}로 입력하세요."}) from None
            options = {}
            for key, value in row.items():
                if key not in optional:
                    continue
                try:
                    if key == size_key:
                        amount = _point_size(key, value, stored, context)
                        scale = 1 if stored else DENOMINATOR
                        if not stored and base is not None:
                            _check_rule_fit("font_sp", amount, base, scale)
                        options["font_sp"] = _rounded(amount, scale)
                    elif key == "indent_origin":
                        if value != "margin":
                            raise ValueError("들여쓰기 기준은 margin이어야 합니다.")
                        options[key] = value
                    else:
                        options[key] = _text_attribute("weight" if key == "mark_weight" else key, value)
                except (ValueError, InvalidOperation) as error:
                    raise InputError({f"{path}.{key}": f"{stage} {str(error) or '속성 값을 확인하세요.'}"}) from None
            levels.append(ListLevel(expr, *distances, **options))
    return tuple(levels)


@dataclass(frozen=True)
class SectionRule:
    path: tuple[str, ...]
    role: str
    values: tuple[tuple[str, int | str | bool], ...]
    face: str = "default"

    def to_fields(self, *, stored):
        values = dict(self.values) if stored else _editor_rule_values(self.values)
        row = {"path": list(self.path), "role": self.role, "values": values}
        if self.face != "default":
            row["face"] = self.face
        return row


_LEVEL_KEY = re.compile(r"levels\.[1-9][0-9]*\.(expr|indent_mm|gap_mm|indent_sp|gap_sp)")
_COLOR = re.compile(r"#[0-9a-fA-F]{6}")


def _editor_rule_key(key):
    return key[:-3] + ("_pt" if key in ("font_sp", "leading_sp") else "_mm")


def _editor_rule_values(fields):
    values = {}
    for key, value in fields:
        if key.endswith("_sp"):
            if key in ("font_sp", "leading_sp"):
                value = _pt(value)
            else:
                value = _mm(value, 0 if key == "offset_sp" else 6)
            key = _editor_rule_key(key)
        values[key] = value
    return values


def _check_rule_fit(key, amount, base, scale):
    if key in ("first_indent_sp", "rest_indent_sp"):
        if _line_clearance(base["page_sp"][0], base["margins_sp"], base.get("gutter_sp"), amount) <= 0:
            raise ValueError("구간 들여쓰기를 줄여 본문 폭을 확보하세요.")
    if key == "font_sp":
        bounds = base.get("ko_tracking_bounds_per_10000") or (base["ko_tracking_per_10000"],)
        ratio = max(*map(abs, bounds), base["space_per_10000"][1])
        if amount * ratio > MAX_DIMENSION_PT * SP_PER_PT * publication.RATIO_UNIT * scale * scale:
            raise ValueError("구간 글자 크기와 자간·공백의 조합이 조판 범위를 벗어났습니다.")


def _check_existing_rules(base, scale, stored, *, bounds_only):
    for index, row in enumerate(base.get("section_rules") or ()):
        if row.role in RUNNING_ROLES:
            continue
        for key, amount in row.values:
            if key not in ("font_sp", "first_indent_sp", "rest_indent_sp"):
                continue
            try:
                _check_rule_fit(key, amount * scale, base, scale)
            except ValueError as error:
                if bounds_only and key == "font_sp":
                    raise InputError({"ko_tracking_bounds_pct": str(error)}) from None
                field = key if stored else _editor_rule_key(key)
                raise InputError({f"section_rules[{index}].values.{field}": str(error)}) from None


def _text_attribute(key, value):
    if key == "weight" and value not in ("normal", "bold"):
        raise ValueError("글자 굵기는 normal 또는 bold여야 합니다.")
    if key == "slant" and value not in ("normal", "italic"):
        raise ValueError("글자 기울기는 normal 또는 italic이어야 합니다.")
    if key == "font" and (not isinstance(value, str) or value not in publication.FONT_LABELS):
        raise ValueError("지원하는 글꼴 ID를 선택하세요.")
    if key == "color" and (not isinstance(value, str) or not _COLOR.fullmatch(value)):
        raise ValueError("색상은 #RRGGBB로 입력하세요.")
    return value


def _point_size(key, value, stored, context):
    maximum = 2048 * SP_PER_PT - 1 if key.startswith("font_") else MAX_DIMENSION_PT * SP_PER_PT
    scale = 1 if stored else DENOMINATOR
    amount = _number(value, stored, context) * (1 if stored else SP_PER_PT * scale)
    if not scale <= amount <= maximum * scale:
        raise ValueError("글자 크기와 행간의 단위와 범위를 확인하세요.")
    return amount


def _rule_value(role, key, value, stored, context, base):
    if key in ("font", "color"):
        return key, _text_attribute(key, value)
    pt_unit, mm_unit = ("sp", "sp") if stored else ("pt", "mm")
    running = role in RUNNING_ROLES
    if running:
        if key == "visible":
            if type(value) is not bool:
                raise ValueError("표시 여부는 true 또는 false여야 합니다.")
            return key, value
        if key in SLOTS:
            parse_running_text(value)
            return key, value
        if key not in (f"font_{pt_unit}", f"offset_{mm_unit}"):
            raise ValueError("머리글·바닥글에서 적용할 수 없는 속성입니다.")
    if key in (f"font_{pt_unit}", f"leading_{pt_unit}"):
        amount = _point_size(key, value, stored, context)
    else:
        level = _LEVEL_KEY.fullmatch(key) if role == "list" else None
        if level and level[1] == "expr":
            format_mark(value, 1)
            return key, value
        indent = role in ("body", "list") and key in (f"first_indent_{mm_unit}", f"rest_indent_{mm_unit}")
        if not (running or indent or level and level[1] in (f"indent_{mm_unit}", f"gap_{mm_unit}")):
            raise ValueError("이 역할에서 적용할 수 없는 속성입니다.")
        amount = _number(value, stored, context) * (1 if stored else MM_NUMERATOR)
        if not 0 <= amount <= (_whole_mm(MAX_PAGE_MM) if stored else MAX_PAGE_MM * MM_NUMERATOR):
            raise ValueError
    canonical = key.rsplit("_", 1)[0] + "_sp"
    if not running:
        _check_rule_fit(canonical, amount, base, 1 if stored else DENOMINATOR)
    return canonical, amount if stored else _rounded(amount, DENOMINATOR)


def _field_path(parent, key):
    if isinstance(key, str) and key and key.isprintable():
        return f"{parent}.{key}"
    return parent


def _section_rules(rows, stored, context, base):
    if not isinstance(rows, (list, tuple)):
        raise InputError({"section_rules": "구간 규칙은 배열이어야 합니다."})
    rules = []
    assigned = {}
    for index, row in enumerate(rows):
        field = f"section_rules[{index}]"
        if not isinstance(row, dict) or row.keys() - {"face"} != {"path", "role", "values"}:
            raise InputError({field: "구간 규칙에 path, role, values가 필요합니다."})
        path, role, raw = row["path"], row["role"], row["values"]
        if (not isinstance(path, (list, tuple)) or len(path) > 6
                or any(not isinstance(p, str) or not p or re.search(r"[\x00\r\n\ud800-\udfff]", p) for p in path)):
            raise InputError({field + ".path": "제목 경로는 제목 문자열로 된 배열이어야 합니다."})
        if role not in ("heading", "body", "list", *RUNNING_ROLES):
            raise InputError({field + ".role": "제목, 본문, 목록, 머리글, 바닥글 중 적용할 역할을 선택하세요."})
        face = row.get("face", "default")
        if "face" in row and (role not in RUNNING_ROLES or face not in RUNNING_FACES):
            raise InputError({field + ".face": "머리글·바닥글의 면은 default, first, odd, even 중에서 선택하세요."})
        if not isinstance(raw, dict) or not raw:
            raise InputError({field + ".values": "적용할 속성을 하나 이상 선택하세요."})
        path = tuple(path)
        values = {}
        for key, raw_value in raw.items():
            location = _field_path(f"{field}.values", key)
            try:
                if not isinstance(key, str):
                    raise ValueError
                canonical, value = _rule_value(role, key, raw_value, stored, context, base)
            except (ValueError, InvalidOperation) as error:
                raise InputError({location: str(error) or "속성 값의 단위와 범위를 확인하세요."}) from None
            target = path, role, face, canonical
            if target in assigned and assigned[target] != value:
                raise InputError({location: "같은 대상의 속성에 적용할 값을 하나만 선택하세요."})
            assigned[target] = values[canonical] = value
        rules.append(SectionRule(path, role, tuple(values.items()), face))

    # A heading inherits the global heading leading unless its own path supplies one.
    if ((), "heading", "default", "leading_sp") not in assigned:
        for index, row in enumerate(rules):
            if row.role != "heading" or (row.path, "heading", "default", "leading_sp") in assigned:
                continue
            size = dict(row.values).get("font_sp")
            if size is not None:
                leading = _rounded(size * publication.HEADING_LEADING, publication.RATIO_UNIT)
                if not 1 <= leading <= MAX_DIMENSION_PT * SP_PER_PT:
                    key = "font_sp" if stored else "font_pt"
                    raise InputError({f"section_rules[{index}].values.{key}":
                                      "설정된 제목 행간 배율로 조판할 수 없는 글자 크기입니다."})
    return tuple(rules) or None


def _text_styles(current, patch, stored, context, scale, errors):
    # Work on a fresh role map. Keep font sizes in the same exact units as body_sp until validation.
    styles = {role: dict(fields) for role, fields in current or ()}
    for fields in styles.values():
        if "font_sp" in fields:
            fields["font_sp"] *= scale
    if not isinstance(patch, dict):
        errors["text_styles"] = "글자 모양은 역할별 속성 객체여야 합니다."
        return styles
    size_key = "font_sp" if stored else "font_pt"
    for role, row in patch.items():
        path = _field_path("text_styles", role)
        if role not in TEXT_ROLES or not isinstance(row, dict):
            errors[path] = "지원하는 역할과 속성 객체를 지정하세요."
            continue
        for key, value in row.items():
            try:
                if role != "body" and key == size_key:
                    value = _point_size(key, value, stored, context)
                    key = "font_sp"
                elif role == "heading1" and key == "frame":
                    if value != "box":
                        raise ValueError("제목 틀은 box를 지정하세요.")
                elif key in ("color", "weight", "slant") or (role != "body" and key == "font"):
                    value = _text_attribute(key, value)
                else:
                    raise ValueError("이 역할에서 적용할 수 없는 속성입니다.")
            except (ValueError, InvalidOperation) as error:
                errors[_field_path(path, key)] = str(error) or "속성 값의 단위와 범위를 확인하세요."
            else:
                styles.setdefault(role, {})[key] = value
    return styles


def _installed_text_defaults():
    fields = publication.TEXT_DEFAULTS
    expected = {"color", "weight", "heading_weight", "slant", "note_pt", "gloss_per_10000"}
    if not isinstance(fields, dict) or fields.keys() != expected:
        raise ValueError(f"publication.json text_defaults requires {sorted(expected)}")
    result = {}
    for key, value in fields.items():
        try:
            if key == "note_pt":
                with localcontext() as context:
                    amount = _point_size("font_pt", value, False, context)
                    result["note_sp"] = _rounded(amount, DENOMINATOR)
            elif key == "gloss_per_10000":
                if type(value) is not int or not 0 < value < publication.RATIO_UNIT:
                    raise ValueError("a gloss must be smaller than its body text")
                result[key] = value
            else:
                result[key] = _text_attribute(key.removeprefix("heading_"), value)
        except (ValueError, InvalidOperation) as error:
            raise ValueError(f"publication.json text_defaults.{key}: {error}") from error
    return result


TEXT_DEFAULTS = _installed_text_defaults()


def _validate(values, scale, errors, *, tracking_supplied, stored):
    width, height = values["page_sp"]
    max_page = _whole_mm(MAX_PAGE_MM) if scale == 1 else MAX_PAGE_MM * MM_NUMERATOR
    if min(width, height) <= 0 or max(width, height) > max_page:
        errors.setdefault("page_sp", PAGE_ERROR)

    for key, maximum in (("body_sp", MAX_BODY_PT), ("leading_sp", MAX_DIMENSION_PT)):
        if not scale <= values[key] <= maximum * SP_PER_PT * scale:
            errors.setdefault(key, f"1/65536pt 이상, {maximum}pt 이하로 입력하세요.")

    margins = values["margins_sp"]
    top, _, bottom, _ = margins
    gutter = values.get("gutter_sp")
    if min(margins) < 0:
        errors.setdefault("margins_sp", "여백은 0 이상이어야 합니다.")
    if gutter is not None and gutter < 0:
        errors.setdefault("gutter_sp", "제본 여백은 0 이상이어야 합니다.")
    if "page_sp" not in errors and "margins_sp" not in errors:
        if _line_clearance(width, margins) <= 0 or _remaining(height, (top, bottom)) <= 0:
            errors["margins_sp"] = "여백을 줄여 본문 폭과 높이를 확보하세요."
        elif gutter and _line_clearance(width, margins, gutter) <= 0:
            errors.setdefault("gutter_sp", "제본 여백을 줄여 본문 폭을 확보하세요.")
    valid_geometry = not errors.keys() & {"page_sp", "margins_sp", "gutter_sp"}
    for key in ("first_indent_sp", "rest_indent_sp"):
        indent = values[key]
        if indent < 0:
            errors.setdefault(key, "들여쓰기는 0 이상이어야 합니다.")
        elif valid_geometry and _line_clearance(width, margins, gutter, indent) <= 0:
            errors.setdefault(key, "들여쓰기를 줄여 줄의 폭을 확보하세요.")

    low, high = values["space_per_10000"]
    minimum, maximum = publication.SPACE_LIMITS
    if not minimum * scale <= low <= high <= maximum * scale:
        bounds = "~".join(format(Decimal(n).scaleb(-2).normalize(), "f") for n in (minimum, maximum))
        errors.setdefault("space_per_10000", f"{bounds}% 안에서 최소와 최대를 순서대로 지정하세요.")

    _validate_typography(values, scale, errors, tracking_supplied, stored)


def _validate_typography(values, scale, errors, tracking_supplied, stored):
    tracking = values["ko_tracking_per_10000"]
    bounds = values.get("ko_tracking_bounds_per_10000")
    if bounds:
        if bounds[0] > bounds[1]:
            errors.setdefault("ko_tracking_bounds_per_10000", "최종 자간의 하한과 상한을 순서대로 지정하세요.")
        elif not bounds[0] <= tracking <= bounds[1]:
            key = "ko_tracking_per_10000" if tracking_supplied else "ko_tracking_bounds_per_10000"
            errors.setdefault(key, "기본 자간은 최종 자간의 하한과 상한 사이여야 합니다.")

    if "body_sp" in errors:
        return
    unit = publication.RATIO_UNIT
    body = values["body_sp"]
    styles = values["text_styles"]
    # A gloss always fits within the surrounding font size, including in a sized section.
    largest = max(body, styles.get("note", {}).get("font_sp", TEXT_DEFAULTS["note_sp"] * scale))
    for level in values.get("list_levels") or _LIST_PRESETS[publication.DEFAULT_LIST_PRESET]:
        if level.font_sp is not None:
            largest = max(largest, level.font_sp * scale)
    for role, ratio in zip(HEADING_ROLES, publication.HEADINGS):
        size = styles.get(role, {}).get("font_sp")
        if size is None:
            size = _rounded(body * ratio, unit * scale) * scale
            field = "body_sp"
        else:
            field = f"text_styles.{role}." + ("font_sp" if stored else "font_pt")
        leading = _rounded(size * publication.HEADING_LEADING, unit * scale)
        if not 1 <= leading <= MAX_DIMENSION_PT * SP_PER_PT:
            errors.setdefault(field, "설정된 제목 행간 배율로 조판할 수 없는 글자 크기입니다.")
        largest = max(largest, size)
    limit = MAX_DIMENSION_PT * SP_PER_PT * unit * scale * scale
    if largest * abs(tracking) > limit:
        errors.setdefault("ko_tracking_per_10000", "현재 글자 크기로 조판할 수 있는 자간 범위를 벗어났습니다.")
    if bounds and largest * max(map(abs, bounds)) > limit:
        errors.setdefault("ko_tracking_bounds_per_10000", "현재 글자 크기로 조판할 수 있는 자간 범위를 벗어났습니다.")
    if largest * values["space_per_10000"][1] > limit:
        errors.setdefault("space_per_10000", "현재 글자 크기로 조판할 수 있는 공백 범위를 벗어났습니다.")


def _load(current, patch, *, stored):
    if not isinstance(patch, dict):
        raise InputError({"style": "양식은 필드와 값으로 된 객체여야 합니다."})
    values = current.copy()
    scale = 1 if stored else DENOMINATOR
    units = {**EDITOR_UNITS, **OPTIONAL_UNITS}
    optional = {key for key, _ in OPTIONAL_UNITS.values()}
    if stored:
        units = {key: (key, 1) for key, _ in units.values()}
    names = {key: external for external, (key, _) in units.items()}
    errors = {}
    optional_fields = {"list_levels", "article_expr", "section_rules", "text_styles", "paragraph_layout"}
    if patch.keys() - units.keys() - set(FONTS) - optional_fields:
        errors["style"] = "지원하지 않는 양식 필드가 있습니다."
    if "paragraph_layout" in patch:
        if patch["paragraph_layout"] in publication.PARAGRAPH_LAYOUTS:
            values["paragraph_layout"] = patch["paragraph_layout"]
        else:
            errors["paragraph_layout"] = "문단 배치 값은 balanced 또는 justified여야 합니다."
    if "article_expr" in patch:
        expr = patch["article_expr"]
        try:
            format_mark(expr, 1)
        except ValueError as error:
            errors["article_expr"] = str(error) if isinstance(expr, str) else "조 번호 표현식은 문자열이어야 합니다."
        else:
            values["article_expr"] = expr
    with localcontext() as context:
        context.prec = 64
        context.Emax, context.Emin = MAX_EMAX, MIN_EMIN
        for external, (key, multiplier) in units.items():
            if values.get(key) is not None:
                values[key] = _map_numbers(key, values[key], lambda n: n * scale)
            if external not in patch:
                if stored and key not in optional:
                    errors[key] = "저장된 양식에 필수 필드가 없습니다."
                continue
            try:
                values[key] = _map_numbers(key, patch[external], lambda n: _number(n, stored, context) * multiplier)
            except (ValueError, InvalidOperation):
                if key in ARRAY_LENGTHS:
                    errors[key] = f"유한한 수 {ARRAY_LENGTHS[key]}개를 입력하세요."
                elif key == "ko_tracking_per_10000":
                    errors[key] = "자간은 유한한 수여야 합니다."
                elif key in ("body_sp", "leading_sp"):
                    maximum = MAX_BODY_PT if key == "body_sp" else MAX_DIMENSION_PT
                    errors[key] = f"1/65536pt 이상, {maximum}pt 이하로 입력하세요."
                else:
                    errors[key] = "유효한 길이를 입력하세요."
                if stored:
                    errors[key] = "정확한 정수 값과 필드 길이를 확인하세요."

        for key in FONTS:
            if stored and key not in patch:
                errors[key] = "저장된 양식에 필수 필드가 없습니다."
            elif key in patch:
                if isinstance(patch[key], str) and patch[key] in publication.FONT_LABELS:
                    values[key] = patch[key]
                else:
                    errors[key] = "지원하는 글꼴 ID를 선택하세요."

        if not current and errors:
            raise InputError({names.get(key, key): msg for key, msg in errors.items()})
        if "list_levels" in patch:
            try:
                values["list_levels"] = _list_levels(patch["list_levels"], stored=stored,
                                                    base=None if errors else values)
            except InputError as error:
                errors.update(error.errors)
        values["text_styles"] = _text_styles(values.get("text_styles"), patch.get("text_styles", {}),
                                            stored, context, scale, errors)
        bounds_only = patch.keys() == {"ko_tracking_bounds_pct"}
        tracking_supplied = names["ko_tracking_per_10000"] in patch
        _validate(values, scale, errors, tracking_supplied=tracking_supplied, stored=stored)
        if not errors:
            if "section_rules" in patch:
                values["section_rules"] = _section_rules(patch["section_rules"], stored, context, values)
            else:
                _check_existing_rules(values, scale, stored, bounds_only=bounds_only)
        if not stored:
            for key in names:
                if values.get(key) is not None:
                    values[key] = _map_numbers(key, values[key], lambda n: _rounded(n, scale))
            for fields in values["text_styles"].values():
                if "font_sp" in fields:
                    fields["font_sp"] = _rounded(fields["font_sp"], scale)
            _validate(values, 1, errors, tracking_supplied=tracking_supplied, stored=False)
            if not errors:
                _check_existing_rules(values, 1, stored=False, bounds_only=bounds_only)

    if errors:
        raise InputError({names.get(key, key): message for key, message in errors.items()})
    values["text_styles"] = tuple(
        (role, tuple(sorted(row.items()))) for role, row in sorted(values["text_styles"].items())
    ) or None
    return values


try:
    _LIST_PRESETS = {name: _list_levels(rows, stored=False, field=f"list_presets.{name}")
                     for name, rows in publication.LIST_PRESETS.items()}
except InputError as error:
    raise ValueError(f"publication.json {error.field}: {error.message}") from error


if not isinstance(publication.DEFAULTS, dict):
    raise ValueError("publication.json defaults must be an editor style object")
_field_difference = publication.DEFAULTS.keys() ^ (EDITOR_UNITS.keys() | set(FONTS))
if _field_difference:
    raise ValueError(f"publication.json defaults fields: {sorted(_field_difference)}")
try:
    _DEFAULT_FIELDS = _load({}, publication.DEFAULTS, stored=False)
except InputError as error:
    raise ValueError(f"publication.json defaults.{error.field}: {error.message}") from error

_running_fields = {"visible", *SLOTS, "font", "color", "font_pt", "offset_mm"}
if not isinstance(publication.RUNNING_DEFAULTS, dict):
    raise ValueError("publication.json running_defaults must be an editor values object")
if difference := publication.RUNNING_DEFAULTS.keys() ^ _running_fields:
    raise ValueError(f"publication.json running_defaults fields: {sorted(difference)}")
try:
    _default_rule = {"path": [], "role": "header", "values": publication.RUNNING_DEFAULTS}
    RUNNING_DEFAULTS = _load(_DEFAULT_FIELDS, {"section_rules": [_default_rule]}, stored=False)["section_rules"][0].values
except InputError as error:
    field = error.field.removeprefix("section_rules[0].values.")
    raise ValueError(f"publication.json running_defaults.{field}: {error.message}") from error

@dataclass(frozen=True)
class Style:
    page_sp: tuple[int, int] = _DEFAULT_FIELDS["page_sp"]
    margins_sp: tuple[int, int, int, int] = _DEFAULT_FIELDS["margins_sp"]
    body_sp: int = _DEFAULT_FIELDS["body_sp"]
    leading_sp: int = _DEFAULT_FIELDS["leading_sp"]
    body_font: str = _DEFAULT_FIELDS["body_font"]
    heading_font: str = _DEFAULT_FIELDS["heading_font"]
    first_indent_sp: int = _DEFAULT_FIELDS["first_indent_sp"]
    rest_indent_sp: int = _DEFAULT_FIELDS["rest_indent_sp"]
    ko_tracking_per_10000: int = _DEFAULT_FIELDS["ko_tracking_per_10000"]
    space_per_10000: tuple[int, int] = _DEFAULT_FIELDS["space_per_10000"]
    list_levels: tuple[ListLevel, ...] | None = None
    article_expr: str | None = None
    section_rules: tuple[SectionRule, ...] | None = None
    gutter_sp: int | None = None
    text_styles: tuple[tuple[str, tuple[tuple[str, str | int], ...]], ...] | None = None
    ko_tracking_bounds_per_10000: tuple[int, int] | None = None
    paragraph_layout: str | None = None

    def resolved_list_levels(self) -> tuple[ListLevel, ...]:
        if self.list_levels is None:
            return _LIST_PRESETS[publication.DEFAULT_LIST_PRESET]
        return self.list_levels

    def to_stored(self) -> dict:
        values = {key: list(value) if isinstance(value, tuple) else value
                  for key, value in vars(self).items() if value is not None}
        if self.text_styles:
            values["text_styles"] = {role: dict(fields) for role, fields in self.text_styles}
        if self.list_levels is not None:
            values["list_levels"] = [row.to_stored() for row in self.list_levels]
        if self.section_rules:
            values["section_rules"] = [row.to_fields(stored=True) for row in self.section_rules]
        return values

    def to_editor(self) -> dict:
        with localcontext() as context:
            context.prec = 40
            values = _editor_values(self)
            if self.text_styles:
                values["text_styles"] = {role: _editor_rule_values(fields) for role, fields in self.text_styles}
            if self.ko_tracking_bounds_per_10000 is not None:
                values["ko_tracking_bounds_pct"] = [
                    format(Decimal(n) / 100, "f") for n in self.ko_tracking_bounds_per_10000
                ]
            if self.paragraph_layout is not None:
                values["paragraph_layout"] = self.paragraph_layout
            if self.article_expr is not None:
                values["article_expr"] = self.article_expr
            if self.list_levels is not None:
                values["list_levels"] = [row.to_editor() for row in self.list_levels]
            if self.section_rules:
                values["section_rules"] = [row.to_fields(stored=False) for row in self.section_rules]
            return values


def _mm(sp, minimum_places):
    # Choose the shortest metric input that rounds back to this exact scaled point.
    mm = Fraction(sp * DENOMINATOR, MM_NUMERATOR)
    for places in range(minimum_places, 7):
        scale = 10 ** places
        nearest = round(mm * scale)
        if abs(nearest * MM_NUMERATOR - sp * DENOMINATOR * scale) * 2 < DENOMINATOR * scale:
            value = min(Decimal(nearest).scaleb(-places), Decimal(MAX_PAGE_MM))
            return format(value.normalize(), "f")
    raise ArithmeticError("six decimal places must resolve a scaled point")


def _editor_values(style):
    top, _, bottom, _ = style.margins_sp
    width, height = style.page_sp
    max_indent = max(style.first_indent_sp, style.rest_indent_sp)
    for row in style.section_rules or ():
        for key, amount in row.values:
            if key in ("first_indent_sp", "rest_indent_sp"):
                max_indent = max(max_indent, amount)
    line = _line_clearance(width, style.margins_sp, style.gutter_sp, max_indent)
    clearance = min(line, height - top - bottom)
    # Five independently rounded lengths can share one inequality with a gutter.
    places = 6 if clearance <= 2 else 0
    values = {
        "page_mm": [_mm(n, places) for n in style.page_sp],
        "margins_mm": [_mm(n, places) for n in style.margins_sp],
        "first_indent_mm": _mm(style.first_indent_sp, places),
        "rest_indent_mm": _mm(style.rest_indent_sp, places),
        "body_pt": _pt(style.body_sp),
        "leading_pt": _pt(style.leading_sp),
        "body_font": style.body_font,
        "heading_font": style.heading_font,
        "ko_tracking_pct": format(Decimal(style.ko_tracking_per_10000) / 100, "f"),
        "space_pct": [format(Decimal(n) / 100, "f") for n in style.space_per_10000],
    }
    if style.gutter_sp is not None:
        values["gutter_mm"] = _mm(style.gutter_sp, places)
    return values


def apply_style(current: Style, patch: dict) -> Style:
    """Atomically apply a patch in editor units to a canonical style."""
    return Style(**_load(vars(current), patch, stored=False))


def from_editor_style(fields: dict) -> Style:
    """A mode-only request selects the preset. A complete editor form carries its own spacing."""
    if fields == {"paragraph_layout": "justified"}:
        fields = {**publication.JUSTIFIED_SPACING, **fields}
    return apply_style(Style(), fields)


def from_stored_style(fields: dict) -> Style:
    """Read all fields of a canonical style without interpreting them as editor units."""
    return Style(**_load(_DEFAULT_FIELDS, fields, stored=True))


def _new_document_style() -> Style:
    mode = publication.NEW_PARAGRAPH_LAYOUT
    with localcontext() as context:
        context.prec = 40
        headings = {}
        for role, ratio in zip(HEADING_ROLES, publication.HEADINGS):
            sp = _rounded(_DEFAULT_FIELDS["body_sp"] * ratio, publication.RATIO_UNIT)
            headings[role] = {"font_pt": _pt(sp)}
    patch = {**publication.NEW_SPACING, "text_styles": headings}
    if mode is not None:
        patch["paragraph_layout"] = mode
    return apply_style(Style(), patch)


try:
    NEW_DOCUMENT_STYLE = _new_document_style()
except InputError as error:
    raise ValueError(f"publication.json new document {error.field}: {error.message}") from error


def editor_bootstrap() -> dict:
    new = NEW_DOCUMENT_STYLE.to_editor()
    with localcontext() as context:
        context.prec = 40
        running = _editor_rule_values(RUNNING_DEFAULTS)
        text_defaults = dict(TEXT_DEFAULTS)
        text_defaults["note_pt"] = _pt(text_defaults.pop("note_sp"))
    return {
        "defaults": Style().to_editor(),
        "new_paragraph_layout": publication.NEW_PARAGRAPH_LAYOUT,
        "new_text_styles": new["text_styles"],
        "new_spacing": {key: new[key] for key in publication.NEW_SPACING},
        "justified_spacing": {key: list(map(str, values)) for key, values in publication.JUSTIFIED_SPACING.items()},
        "text_defaults": text_defaults,
        "heading_per_10000": list(publication.HEADINGS),
        "heading_leading_per_10000": publication.HEADING_LEADING,
        "fonts": [{"id": key, "label": label} for key, label in publication.FONT_LABELS.items()],
        "list_presets": {name: [row.to_editor() for row in levels] for name, levels in _LIST_PRESETS.items()},
        "default_list_preset": publication.DEFAULT_LIST_PRESET,
        "default_article_expr": publication.DEFAULT_ARTICLE_EXPR,
        "running_defaults": running,
    }
