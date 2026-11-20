"""File boundary for an isolated style suggestion job."""
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import resource
import sys

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.ai.style import SETTINGS, suggest_patch
from app.document import InputError
from app.jobs import INVALID_INPUT, limit_worker_files
from app.style import Style, apply_style


def worker():
    os.umask(0o077)
    limit_worker_files()
    if sys.platform == "linux":
        _, hard = resource.getrlimit(resource.RLIMIT_AS)
        limit = SETTINGS["worker_memory_bytes"]
        resource.setrlimit(resource.RLIMIT_AS, (limit if hard == resource.RLIM_INFINITY else min(hard, limit), hard))
    try:
        data = json.loads(Path("request.bin").read_bytes().decode("utf-8"), parse_float=Decimal)
    except (ValueError, InvalidOperation, RecursionError):
        raise InputError({"request": "요청과 현재 양식을 JSON으로 보내세요."}) from None
    if not isinstance(data, dict) or data.keys() != {"request", "style"}:
        raise InputError({"request": "요청과 현재 양식을 함께 보내세요."})
    current = apply_style(Style(), data["style"])
    patch = suggest_patch(data["request"], current)
    Path("response.json").write_text(json.dumps({"patch": patch}, ensure_ascii=False), "utf-8")


if __name__ == "__main__":
    try:
        worker()
    except InputError as error:
        Path("invalid.json").write_text(json.dumps([error.field, error.message]), "utf-8")
        sys.exit(INVALID_INPUT)
