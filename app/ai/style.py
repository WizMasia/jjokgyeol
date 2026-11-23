"""Validate edits from the selected external model as a sparse Style patch."""
from copy import deepcopy
from decimal import Decimal

from app import publication
from app.ai.providers import Connection
from app.configuration import CONFIG_DIR, read_config
from app.document import InputError
from app.running_heads import RUNNING_ROLES, SLOTS
from app.style import Style, apply_style

SETTINGS = read_config(CONFIG_DIR / "style-suggestion.json")
for key in ("max_request_bytes", "max_request_chars", "timeout_seconds", "worker_memory_bytes", "slots"):
    if type(SETTINGS.get(key)) is not int or SETTINGS[key] <= 0:
        raise ValueError(f"style-suggestion.json: invalid {key}")

SCALARS = ("body_pt", "leading_pt", "body_font", "heading_font", "first_indent_mm",
           "rest_indent_mm", "ko_tracking_pct", "article_expr", "gutter_mm")
ARRAYS = {"page_mm": ("page.width", "page.height"),
          "margins_mm": ("margin.top", "margin.right", "margin.bottom", "margin.left"),
          "space_pct": ("space.min", "space.max")}

PROMPT = """당신은 한국어 출판 양식 편집기입니다. 요청에서 명시한 변경만 edits에 넣으세요.
body_pt는 본문 크기(pt), leading_pt는 줄간격(pt)입니다. 숫자는 단위 없는 문자열입니다.
header는 머리글, footer는 바닥글입니다. 가운데 칸은 center이며 쪽 번호의 값은 정확히 {현재쪽}입니다.
칸을 채우면 자동 표시됩니다. 목록 단계는 1부터 시작하고 표지 토큰은 {숫자}, {한글}, {원숫자}입니다.
현재 값은 참고용입니다. 각각의 변경 필드를 한 번만 제안하세요. 지원할 수 없는 요청에는 빈 edits를 주세요."""


def _common(row, role):
    return row["role"] == role and not row["path"] and row.get("face", "default") == "default"


def _running(rules, role, slot, value):
    rows = [r for r in rules if _common(r, role)]
    if not rows:
        row = {"path": [], "role": role, "values": {}}
        rules.append(row)
        rows.append(row)
    changes = {slot: value}
    if value:
        changes["visible"] = True
    for key, v in changes.items():
        for row in [r for r in rows if key in r["values"]] or rows[:1]:
            row["values"][key] = v


def suggest_patch(request: str, current: Style, *, connection: Connection, draft: dict | None = None) -> dict:
    """Keep the HTTP editor's original decimals when available."""
    if not isinstance(request, str) or not request.strip() or len(request) > SETTINGS["max_request_chars"]:
        raise InputError({"request": f'요청을 1~{SETTINGS["max_request_chars"]}자로 입력하세요.'})
    before = current.to_editor()
    if "list_levels" not in before:
        before["list_levels"] = [row.to_editor() for row in current.resolved_list_levels()]
    editor = {**before, **(draft or {})}
    fields = {k: editor.get(k) for k in SCALARS}
    for key, names in ARRAYS.items():
        fields.update(zip(names, editor[key], strict=True))
    levels = deepcopy(editor["list_levels"])
    for i, level in enumerate(levels, 1):
        fields.update({f"list.{i}.{key}": level[key] for key in ("expr", "indent_mm", "gap_mm")})
    rules = deepcopy(editor.get("section_rules", []))
    for role in RUNNING_ROLES:
        fields.update({f"{role}.{slot}": None for slot in SLOTS})
        for row in rules:
            if _common(row, role):
                fields.update({f"{role}.{k}": v for k, v in row["values"].items() if k in SLOTS})

    context = {"요청": request, "글꼴": publication.FONT_LABELS,
               "현재 양식": {k: str(v) if isinstance(v, Decimal) else v for k, v in fields.items()}}
    item = {"type": "object", "properties": {"field": {"type": "string", "enum": list(fields)},
                                             "value": {"type": "string"}},
            "required": ["field", "value"], "additionalProperties": False}
    schema = {"type": "object", "properties": {"edits": {"type": "array", "items": item}},
              "required": ["edits"], "additionalProperties": False}
    answer = connection.generate_json(PROMPT, context, schema=schema, timeout=SETTINGS["timeout_seconds"])
    invalid = InputError({"patch": "모델이 제안한 변경 항목과 값을 확인하세요."})
    if not isinstance(answer, dict) or answer.keys() != {"edits"} or not isinstance(answer["edits"], list):
        raise invalid
    patch = {}
    seen = set()
    for edit in answer["edits"]:
        if not isinstance(edit, dict) or edit.keys() != {"field", "value"}:
            raise invalid
        field, value = edit["field"], edit["value"]
        if not isinstance(field, str) or field not in fields or field in seen or not isinstance(value, str):
            raise invalid
        seen.add(field)
        if field in SCALARS:
            patch[field] = value
        elif field.startswith("list."):
            _, index, attr = field.split(".")
            levels[int(index) - 1][attr] = value
            patch["list_levels"] = levels
        elif field.split(".")[0] in RUNNING_ROLES:
            role, slot = field.split(".")
            _running(rules, role, slot, value)
            patch["section_rules"] = rules
        else:
            for key, names in ARRAYS.items():
                if field in names:
                    patch.setdefault(key, list(editor[key]))[names.index(field)] = value
    after = apply_style(Style(), {**editor, **patch}).to_editor()
    changes = {key: value for key, value in patch.items() if after[key] != before.get(key)}
    if changes and len(changes) != len(patch):
        apply_style(Style(), {**editor, **changes})
    return changes
