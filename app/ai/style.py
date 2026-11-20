"""Ask the operator's local model for edits, then validate a sparse Style patch."""
from copy import deepcopy
import http.client
import json
import os
from urllib.parse import urlsplit

from app import publication
from app.configuration import CONFIG_DIR, read_config
from app.document import InputError
from app.running_heads import RUNNING_ROLES, SLOTS
from app.style import Style, apply_style

SETTINGS = read_config(CONFIG_DIR / "style-suggestion.json")
for key in ("max_request_bytes", "max_request_chars", "max_response_bytes", "max_tokens", "timeout_seconds",
            "worker_memory_bytes", "slots"):
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


def _infer(request, fields):
    url = urlsplit(os.environ.get("JJOKGYEOL_STYLE_URL", SETTINGS["url"]))
    model = os.environ.get("JJOKGYEOL_STYLE_MODEL", SETTINGS["model"])
    if (url.scheme not in ("http", "https") or not url.hostname or url.username or url.password
            or url.fragment or url.query or not model):
        raise ValueError("양식 모델 연결 설정을 확인하세요.")
    item = {"type": "object", "properties": {"field": {"type": "string", "enum": list(fields)},
                                             "value": {"type": "string"}},
            "required": ["field", "value"], "additionalProperties": False}
    schema = {"type": "object", "properties": {"edits": {"type": "array", "items": item}},
              "required": ["edits"], "additionalProperties": False}
    context = {"현재 양식": fields, "글꼴": publication.FONT_LABELS, "요청": request}
    payload = {"model": model, "temperature": 0, "max_tokens": SETTINGS["max_tokens"],
               "messages": [{"role": "system", "content": PROMPT},
                            {"role": "user", "content": json.dumps(context, ensure_ascii=False, default=str)}],
               "response_format": {"type": "json_schema", "json_schema": {"name": "style_edits", "schema": schema}}}
    cls = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
    conn = cls(url.hostname, url.port, timeout=SETTINGS["timeout_seconds"])
    try:
        conn.request("POST", url.path.rstrip("/") + "/chat/completions",
                     body=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        response = conn.getresponse()
        if response.status != 200:
            raise ValueError("양식 모델 연결 실패")
        body = response.read(SETTINGS["max_response_bytes"] + 1)
    finally:
        conn.close()
    if len(body) > SETTINGS["max_response_bytes"]:
        raise ValueError("양식 모델 응답 길이 초과")
    choice = json.loads(body)["choices"][0]
    if choice.get("finish_reason") == "length":
        raise ValueError("양식 모델 응답 길이 초과")
    return json.loads(choice["message"]["content"])


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


def suggest_patch(request: str, current: Style, *, draft: dict | None = None) -> dict:
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

    answer = _infer(request, fields)
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
