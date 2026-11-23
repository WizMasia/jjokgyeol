"""Bounded JSON transport for the web and native paragraph editor."""
import json
from pathlib import Path
import sys

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.configuration import unique_fields
from app.document import InputError
from app.operations import LIMITS
from app.paragraph_edit import edit_alignment


def alignment_response(stream):
    raw = stream.read(LIMITS.request_body_bytes + 1)
    if len(raw) > LIMITS.request_body_bytes:
        raise InputError({"request": "요청 크기가 너무 큽니다."})
    try:
        payload = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique_fields)
    except (ValueError, RecursionError):
        raise InputError({"request": "원고와 선택한 줄을 확인하세요."}) from None

    output = json.dumps(edit_alignment(payload), allow_nan=False).encode("ascii")
    if len(output) > LIMITS.worker_file_bytes:
        raise ValueError("paragraph response exceeds worker output limit")
    return output


if __name__ == "__main__":
    from app.jobs import INVALID_INPUT
    from app.security import harden_worker

    harden_worker(max_file_bytes=LIMITS.worker_file_bytes)
    try:
        with Path("request.bin").open("rb") as stream:
            result = alignment_response(stream)
    except InputError as error:
        Path("invalid.json").write_text(json.dumps([error.field, error.message]), "ascii")
        sys.exit(INVALID_INPUT)
    Path("response.json").write_bytes(result)
