"""Read or edit one top-level paragraph's alignment in the original manuscript."""
import re

from app.document import ALIGNMENT_MARKERS, InputError, paragraph_range


def edit_alignment(payload: dict) -> dict:
    required = {"source", "first_line", "last_line"}
    if not isinstance(payload, dict) or not required <= payload.keys() <= required | {"align"}:
        raise InputError({"source": "원고와 선택한 줄을 확인하세요."})
    source, first, last = payload["source"], payload["first_line"], payload["last_line"]
    if type(first) is not int or type(last) is not int or not 0 <= first <= last:
        raise InputError({"source": "선택한 줄의 범위를 확인하세요."})
    align = payload.get("align")
    if align is not None and (not isinstance(align, str) or align not in ALIGNMENT_MARKERS):
        raise InputError({"align": "문단 정렬값을 확인하세요."})

    target = paragraph_range(source, first, last)
    if target is None:
        return dict(source=source, align=None, supported=False, message="최상위 본문이나 제목 한 곳을 선택하세요.")
    if "align" not in payload or align == target.align:
        return dict(source=source, align=target.align, supported=True, message="")

    bom = "\ufeff" if source.startswith("\ufeff") else ""
    parts = re.split(r"(\r\n|\r|\n)", source[len(bom):])
    start, stop = 2 * target.marker_start, 2 * target.start
    before, after = bom + "".join(parts[:start]), "".join(parts[stop:])
    previous_eol = parts[start - 1] if start else ""
    if start != stop:
        eol = parts[stop - 1]
    else:
        eol = parts[stop + 1] if stop + 1 < len(parts) else previous_eol or "\n"
    if align:
        middle = ALIGNMENT_MARKERS[align] + eol
    else:
        middle = ""
        if previous_eol and parts[start - 2].strip():
            # Keep a separator only if removing the marker would change the target's block.
            row = target.marker_start
            unmarked = paragraph_range(before + after, row, row)
            if unmarked is None or unmarked.start != row or unmarked.end - row != target.end - target.start:
                middle = previous_eol
    return dict(source=before + middle + after, align=align, supported=True, message="")
