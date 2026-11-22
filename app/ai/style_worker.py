"""File boundary for an isolated style suggestion job."""
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sys

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.ai.style import SETTINGS, suggest_patch
from app.document import InputError
from app.jobs import INVALID_INPUT, LIMITS
from app.security import harden_worker
from app.style import from_editor_style


def worker():
    harden_worker(
        max_file_bytes=LIMITS.worker_file_bytes,
        memory_bytes=SETTINGS["worker_memory_bytes"],
    )
    try:
        data = json.loads(Path("request.bin").read_bytes().decode("utf-8"), parse_float=Decimal)
    except (ValueError, InvalidOperation, RecursionError):
        raise InputError({"request": "요청과 현재 양식을 JSON으로 보내세요."}) from None
    if not isinstance(data, dict) or data.keys() != {"request", "style"}:
        raise InputError({"request": "요청과 현재 양식을 함께 보내세요."})
    current = from_editor_style(data["style"])
    patch = suggest_patch(data["request"], current, draft=data["style"])
    Path("response.json").write_text(json.dumps({"patch": patch}, ensure_ascii=False, default=str), "utf-8")


if __name__ == "__main__":
    try:
        worker()
    except InputError as error:
        Path("invalid.json").write_text(json.dumps([error.field, error.message]), "utf-8")
        sys.exit(INVALID_INPUT)
