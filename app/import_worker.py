"""Convert one raw upload inside the supervisor's private working directory."""
import json
from pathlib import Path
import sys

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.jobs import INVALID_INPUT, LIMITS
from app.security import harden_worker, sanitize_user_error


def worker(name, memory_bytes):
    harden_worker(
        max_file_bytes=LIMITS.worker_file_bytes,
        memory_bytes=memory_bytes,
    )

    from app.importers.read import import_file

    request = Path("request.bin")
    data = request.read_bytes()
    request.unlink()
    try:
        result = import_file(name, data)
    except ValueError as error:
        message = sanitize_user_error(error, "원고 파일을 읽을 수 없습니다.")
        Path("invalid.json").write_text(json.dumps(["source", message]), encoding="utf-8")
        return INVALID_INPUT
    Path("response.json").write_text(json.dumps({
        "markdown": result.markdown, "warnings": result.warnings,
    }, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(worker(sys.argv[1], int(sys.argv[2])))
