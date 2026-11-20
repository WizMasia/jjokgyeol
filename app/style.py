"""Convert editor units once and validate the integer style consumed by LuaTeX."""

import re
from dataclasses import asdict, dataclass
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
ARRAY_LENGTHS = {"page_sp": 2, "margins_sp": 4, "space_per_10000": 2}
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

    def to_editor(self) -> dict:
        with localcontext() as context:
            context.prec = 40
            return {"expr": self.expr, "indent_mm": _mm(self.indent_sp, 0), "gap_mm": _mm(self.gap_sp, 0)}


def _list_levels(rows, *, stored, field="list_levels"):
    if not isinstance(rows, (list, tuple)) or not rows:
        raise InputError({field: "목록 양식에는 한 단계 이상이 필요합니다."})
    distance_keys = ("indent_sp", "gap_sp") if stored else ("indent_mm", "gap_mm")
    expected = ("expr", *distance_keys)
    maximum = _whole_mm(MAX_PAGE_MM) if stored else MAX_PAGE_MM
    levels = []
    with localcontext() as context:
        context.prec = 64
        context.Emax, context.Emin = MAX_EMAX, MIN_EMIN
        for index, row in enumerate(rows):
            path = f"{field}[{index}]"
            stage = f"목록 {index + 1}단계"
            if not isinstance(row, dict) or row.keys() - set(expected):
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
            levels.append(ListLevel(expr, *distances))
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
                value = format(Decimal(value) / SP_PER_PT, "f")
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
        ratio = max(abs(base["ko_tracking_per_10000"]), base["space_per_10000"][1])
        if amount * ratio > MAX_DIMENSION_PT * SP_PER_PT * publication.RATIO_UNIT * scale * scale:
            raise ValueError("구간 글자 크기와 자간·공백의 조합이 조판 범위를 벗어났습니다.")


def _check_existing_rules(base, scale, stored):
    for index, row in enumerate(base.get("section_rules") or ()):
        if row.role in RUNNING_ROLES:
            continue
        for key, amount in row.values:
            if key not in ("font_sp", "first_indent_sp", "rest_indent_sp"):
                continue
            try:
                _check_rule_fit(key, amount * scale, base, scale)
            except ValueError as error:
                field = key if stored else _editor_rule_key(key)
                raise InputError({f"section_rules[{index}].values.{field}": str(error)}) from None


def _rule_value(role, key, value, stored, context, base):
    if key == "font":
        if not isinstance(value, str) or value not in publication.FONT_LABELS:
            raise ValueError("지원하는 글꼴 ID를 선택하세요.")
        return key, value
    if key == "color":
        if not isinstance(value, str) or not _COLOR.fullmatch(value):
            raise ValueError("색상은 #RRGGBB로 입력하세요.")
        return key, value

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
        multiplier = SP_PER_PT * DENOMINATOR
        # Local font sizes are absolute; no heading multiplier is applied to them.
        max_sp = 2048 * SP_PER_PT - 1 if key.startswith("font_") else MAX_DIMENSION_PT * SP_PER_PT
        minimum, maximum = (1, max_sp) if stored else (DENOMINATOR, max_sp * DENOMINATOR)
    else:
        level = _LEVEL_KEY.fullmatch(key) if role == "list" else None
        if level and level[1] == "expr":
            format_mark(value, 1)
            return key, value
        indent = role in ("body", "list") and key in (f"first_indent_{mm_unit}", f"rest_indent_{mm_unit}")
        if not (running or indent or level and level[1] in (f"indent_{mm_unit}", f"gap_{mm_unit}")):
            raise ValueError("이 역할에서 적용할 수 없는 속성입니다.")
        multiplier = MM_NUMERATOR
        minimum, maximum = 0, _whole_mm(MAX_PAGE_MM) if stored else MAX_PAGE_MM * multiplier

    # Keep the exact common numerator until metric inputs and the page width
    # have been checked together. The caller rechecks the rounded geometry.
    amount = _number(value, stored, context) * (1 if stored else multiplier)
    if not minimum <= amount <= maximum:
        raise ValueError
    canonical = key.rsplit("_", 1)[0] + "_sp"
    if not running:
        _check_rule_fit(canonical, amount, base, 1 if stored else DENOMINATOR)
    return canonical, amount if stored else _rounded(amount, DENOMINATOR)


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
            location = f"{field}.values.{key}"
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


def _validate(values, scale, errors):
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

    if "body_sp" not in errors:
        unit = publication.RATIO_UNIT
        heading = _rounded(values["body_sp"] * max(publication.HEADINGS), unit * scale)
        leading = _rounded(heading * publication.HEADING_LEADING, unit)
        smallest = _rounded(values["body_sp"] * min(publication.HEADINGS), unit * scale)
        if leading > MAX_DIMENSION_PT * SP_PER_PT or _rounded(smallest * publication.HEADING_LEADING, unit) < 1:
            errors.setdefault("body_sp", "현재 제목 배율과 행간으로 조판할 수 없는 본문 크기입니다.")
        # Body, heading and fixed 8pt footnotes all participate in the tracking rule.
        largest = max(heading * scale, values["body_sp"], 8 * SP_PER_PT * scale)
        limit = MAX_DIMENSION_PT * SP_PER_PT * unit * scale * scale
        if largest * abs(values["ko_tracking_per_10000"]) > limit:
            errors.setdefault("ko_tracking_per_10000", "현재 본문 크기로 조판할 수 있는 자간 범위를 벗어났습니다.")
        if largest * high > limit:
            errors.setdefault("space_per_10000", "현재 본문 크기로 조판할 수 있는 공백 범위를 벗어났습니다.")


def _load(current, patch, *, stored):
    if not isinstance(patch, dict):
        raise InputError({"style": "양식은 필드와 값으로 된 객체여야 합니다."})
    values = current.copy()
    scale = 1 if stored else DENOMINATOR
    units = {key: (key, 1) for key, _ in EDITOR_UNITS.values()} if stored else EDITOR_UNITS
    gutter_field = "gutter_sp" if stored else "gutter_mm"
    units = {**units, gutter_field: ("gutter_sp", 1 if stored else MM_NUMERATOR)}
    names = {key: external for external, (key, _) in units.items()}
    errors = {}
    if patch.keys() - units.keys() - set(FONTS) - {"list_levels", "article_expr", "section_rules"}:
        errors["style"] = "지원하지 않는 양식 필드가 있습니다."
    if "article_expr" in patch:
        expr = patch["article_expr"]
        try:
            format_mark(expr, 1)
        except ValueError as error:
            errors["article_expr"] = str(error) if isinstance(expr, str) else "조 번호 표현식은 문자열이어야 합니다."
        else:
            values["article_expr"] = expr
    if "list_levels" in patch:
        try:
            values["list_levels"] = _list_levels(patch["list_levels"], stored=stored)
        except InputError as error:
            errors.update(error.errors)

    with localcontext() as context:
        context.prec = 64
        context.Emax, context.Emin = MAX_EMAX, MIN_EMIN
        for external, (key, multiplier) in units.items():
            if values.get(key) is not None:
                values[key] = _map_numbers(key, values[key], lambda n: n * scale)
            if external not in patch:
                if stored and key != "gutter_sp":
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
        _validate(values, scale, errors)
        if not errors:
            if "section_rules" in patch:
                values["section_rules"] = _section_rules(patch["section_rules"], stored, context, values)
            else:
                _check_existing_rules(values, scale, stored)
        if not stored:
            for key in names:
                if values.get(key) is not None:
                    values[key] = _map_numbers(key, values[key], lambda n: _rounded(n, scale))
            _validate(values, 1, errors)
            if not errors:
                _check_existing_rules(values, 1, stored=False)

    if errors:
        raise InputError({names.get(key, key): message for key, message in errors.items()})
    return values


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

try:
    _LIST_PRESETS = {name: _list_levels(rows, stored=False, field=f"list_presets.{name}")
                     for name, rows in publication.LIST_PRESETS.items()}
except InputError as error:
    raise ValueError(f"publication.json {error.field}: {error.message}") from error


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

    def resolved_list_levels(self) -> tuple[ListLevel, ...]:
        if self.list_levels is None:
            return _LIST_PRESETS[publication.DEFAULT_LIST_PRESET]
        return self.list_levels

    def to_stored(self) -> dict:
        values = {key: list(value) if isinstance(value, tuple) else value
                  for key, value in vars(self).items() if value is not None}
        if self.list_levels is not None:
            values["list_levels"] = [asdict(row) for row in self.list_levels]
        if self.section_rules:
            values["section_rules"] = [row.to_fields(stored=True) for row in self.section_rules]
        return values

    def to_editor(self) -> dict:
        with localcontext() as context:
            context.prec = 40
            values = _editor_values(self)
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
        "body_pt": format(Decimal(style.body_sp) / SP_PER_PT, "f"),
        "leading_pt": format(Decimal(style.leading_sp) / SP_PER_PT, "f"),
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


def from_stored_style(fields: dict) -> Style:
    """Read all fields of a canonical style without interpreting them as editor units."""
    return Style(**_load(_DEFAULT_FIELDS, fields, stored=True))


def editor_bootstrap() -> dict:
    with localcontext() as context:
        context.prec = 40
        running = _editor_rule_values(RUNNING_DEFAULTS)
    return {
        "defaults": Style().to_editor(),
        "fonts": [{"id": key, "label": label} for key, label in publication.FONT_LABELS.items()],
        "list_presets": {name: [row.to_editor() for row in levels] for name, levels in _LIST_PRESETS.items()},
        "default_list_preset": publication.DEFAULT_LIST_PRESET,
        "default_article_expr": publication.DEFAULT_ARTICLE_EXPR,
        "running_defaults": running,
    }
