"""Read or edit top-level paragraph formatting in the original manuscript."""
import re
from decimal import InvalidOperation

from app.document import ALIGNMENT_MARKERS, InputError, paragraph_marker, paragraph_range
from app.style import SP_PER_PT, SPACING_FIELDS, leading_sp, paragraph_spacing_sp


def _replace_markers(source, target, changes):
    bom = "\ufeff" if source.startswith("\ufeff") else ""
    parts = re.split(r"(\r\n|\r|\n)", source[len(bom):])
    start, stop = 2 * target.marker_start, 2 * target.start
    before, after = bom + "".join(parts[:start]), "".join(parts[stop:])
    previous_eol = parts[start - 1] if start else ""
    if start != stop:
        eol = parts[stop - 1]
    else:
        eol = parts[stop + 1] if stop + 1 < len(parts) else previous_eol or "\n"

    replacements = {}
    for key, value in changes.items():
        if value is None:
            replacements[key] = None
        elif key == "align":
            replacements[key] = ALIGNMENT_MARKERS[value]
        else:
            replacements[key] = f"<!-- 쪽결 {key}={value} -->"

    rows = []
    for i in range(stop - 2, start - 1, -2):
        key, _ = paragraph_marker(parts[i])
        marker = replacements.get(key, parts[i])
        if marker is not None:
            rows.append(marker + parts[i + 1])
        if key in replacements:
            replacements[key] = None  # Remove earlier duplicates of the edited field.
    rows.reverse()
    rows.extend(marker + eol for marker in replacements.values() if marker is not None)
    if not rows and previous_eol and parts[start - 2].strip():
        # Keep a separator only if removing the markers would change the target's block.
        row = target.marker_start
        unmarked = paragraph_range(before + after, row, row)
        if unmarked is None or unmarked.start != row or unmarked.end - row != target.end - target.start:
            rows.append(previous_eol)
    return before + "".join(rows) + after, target.marker_start + len(rows)


def edit_alignment(payload: dict) -> dict:
    required = {"source", "first_line", "last_line"}
    allowed = required | {"align", "spacing", "leading", "leading_pt", *SPACING_FIELDS}
    if not isinstance(payload, dict) or not required <= payload.keys() <= allowed:
        raise InputError({"source": "원고와 선택한 줄을 확인하세요."})
    source, first, last = payload["source"], payload["first_line"], payload["last_line"]
    if type(first) is not int or type(last) is not int or not 0 <= first <= last:
        raise InputError({"source": "선택한 줄의 범위를 확인하세요."})
    if type(payload.get("spacing", False)) is not bool:
        raise InputError({"spacing": "문단 여백 조회 여부를 확인하세요."})
    if type(payload.get("leading", False)) is not bool:
        raise InputError({"leading": "줄 간격 조회 여부를 확인하세요."})

    updates = {}
    if "align" in payload:
        align = payload["align"]
        if align is not None and (not isinstance(align, str) or align not in ALIGNMENT_MARKERS):
            raise InputError({"align": "문단 정렬값을 확인하세요."})
        updates["align"] = align
    for key, stored in SPACING_FIELDS.items():
        if key in payload:
            try:
                updates[stored] = None if payload[key] is None else paragraph_spacing_sp(payload[key])
            except (ValueError, InvalidOperation) as error:
                raise InputError({key: str(error) or "문단 여백의 단위와 범위를 확인하세요."}) from None
    if "leading_pt" in payload:
        try:
            value = payload["leading_pt"]
            updates["leading_sp"] = None if value is None else "auto" if value == "auto" else leading_sp(value)
        except (ValueError, InvalidOperation) as error:
            raise InputError({"leading_pt": str(error) or "줄 간격의 단위와 범위를 확인하세요."}) from None

    target = paragraph_range(source, first, last)
    values = {key: getattr(target, key, None) for key in ("align", *SPACING_FIELDS.values(), "leading_sp")}
    start_line = None
    if target:
        start_line = target.start
        changes = {key: value for key, value in updates.items() if values[key] != value}
        if changes:
            source, start_line = _replace_markers(source, target, changes)
            values.update(changes)

    result = dict(source=source, align=values["align"],
                  supported=target is not None, message="" if target else "최상위 본문이나 제목 한 곳을 선택하세요.")
    if payload.get("spacing") or SPACING_FIELDS.keys() & payload.keys() or "leading_pt" in payload or payload.get("leading"):
        result["start_line"] = start_line
        for key, stored in SPACING_FIELDS.items():
            value = values[stored]
            result[key] = value / SP_PER_PT if isinstance(value, int) else value
        if payload.get("leading") or "leading_pt" in payload:
            leading = values["leading_sp"]
            result["leading_pt"] = leading / SP_PER_PT if isinstance(leading, int) else leading
    return result
