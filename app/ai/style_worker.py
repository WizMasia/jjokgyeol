"""Read a session connection from stdin and write only the validated style patch."""
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sys

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.ai.providers import ProviderError, parse_connection
from app.ai.style import SETTINGS, suggest_patch
from app.configuration import unique_fields
from app.document import InputError
from app.jobs import INVALID_INPUT, LIMITS, JobError, connection_error
from app.security import harden_worker
from app.style import from_editor_style


def worker():
    harden_worker(
        max_file_bytes=LIMITS.worker_file_bytes,
        memory_bytes=SETTINGS["worker_memory_bytes"],
    )
    raw = sys.stdin.buffer.read(SETTINGS["max_request_bytes"] + 1)
    if len(raw) > SETTINGS["max_request_bytes"]:
        raise JobError(413)
    try:
        data = json.loads(raw.decode("utf-8"), parse_float=Decimal, object_pairs_hook=unique_fields)
    except (ValueError, InvalidOperation, RecursionError):
        raise InputError({"request": "요청과 현재 양식을 JSON으로 보내세요."}) from None
    if (not isinstance(data, dict) or not {"request", "style"}.issubset(data)
            or data.keys() - {"request", "style", "connection"}):
        raise InputError({"request": "요청과 현재 양식을 함께 보내세요."})
    current = from_editor_style(data["style"])
    raw_connection = data.get("connection")
    if not isinstance(raw_connection, dict) or raw_connection.get("provider") in ("rule", "builtin"):
        raise connection_error("invalid_connection", "설정에서 외부 API 연결을 적용하세요.")
    if "--keyless" in sys.argv and raw_connection.get("api_key"):
        raise connection_error("address", "API 키를 사용하려면 HTTPS 또는 루프백 주소로 편집기를 여세요.")
    try:
        connection = parse_connection(raw_connection)
        patch = suggest_patch(data["request"], current, connection=connection, draft=data["style"])
    except ProviderError as error:
        raise connection_error(error.code) from None
    Path("response.json").write_text(json.dumps({"patch": patch}, ensure_ascii=False, default=str), "utf-8")


if __name__ == "__main__":
    try:
        worker()
    except JobError as error:
        print(json.dumps([error.status, error.field, error.message, error.code]))
        sys.exit(INVALID_INPUT)
    except InputError as error:
        Path("invalid.json").write_text(json.dumps([error.field, error.message]), "utf-8")
        sys.exit(INVALID_INPUT)
