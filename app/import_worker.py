"""Convert one raw upload inside the supervisor's private working directory."""
import json
import os
from pathlib import Path
import resource
import sys

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.jobs import INVALID_INPUT, limit_worker_files


def worker(name, memory_bytes):
    os.umask(0o077)
    limit_worker_files()
    if sys.platform == "linux":
        _, hard = resource.getrlimit(resource.RLIMIT_AS)
        limit = memory_bytes if hard == resource.RLIM_INFINITY else min(memory_bytes, hard)
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))

    from app.importers.read import import_file

    request = Path("request.bin")
    data = request.read_bytes()
    request.unlink()
    try:
        result = import_file(name, data)
    except ValueError as error:
        Path("invalid.json").write_text(json.dumps(["source", str(error)]), encoding="utf-8")
        return INVALID_INPUT
    Path("response.json").write_text(json.dumps({
        "markdown": result.markdown, "warnings": result.warnings,
    }, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(worker(sys.argv[1], int(sys.argv[2])))
