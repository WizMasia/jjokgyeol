"""Operator settings and the file boundary for reference-style analysis."""
import json
import os
from pathlib import Path
import sys
from time import monotonic

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.configuration import CONFIG_DIR, read_config
from app.document import InputError
from app.jobs import INVALID_INPUT, LIMITS
from app.security import harden_worker, sanitize_user_error, validate_outbound_url


def load_settings():
    settings = read_config(CONFIG_DIR / "reference.json")
    for key in ("max_bytes", "max_uncompressed_bytes", "max_compression_ratio", "max_entries", "max_units", "max_pages",
                "max_pixels", "raster_width", "timeout_seconds", "vision_timeout_seconds",
                "worker_memory_bytes", "slots", "max_candidates_per_unit", "max_excerpt_chars",
                "max_evidence_chars", "max_vision_response_bytes", "vision_max_tokens"):
        if type(settings.get(key)) is not int or settings[key] <= 0:
            raise ValueError(f"reference.json: invalid {key}")
    for field in ("vision_url", "vision_model", "vision_token"):
        settings[field] = os.environ.get("JJOKGYEOL_" + field.upper(), settings.get(field, ""))
    if bool(settings["vision_url"]) != bool(settings["vision_model"]):
        raise ValueError("JJOKGYEOL_VISION_URL and JJOKGYEOL_VISION_MODEL must be configured together")
    if settings["vision_url"]:
        try:
            validate_outbound_url(settings["vision_url"], has_token=bool(settings["vision_token"]))
        except ValueError as error:
            raise ValueError(f"invalid JJOKGYEOL_VISION_URL: {error}") from None
    return settings


def analyze(data, fmt, page, settings):
    if not data or len(data) > settings["max_bytes"]:
        raise ValueError("빈 파일이거나 참고 파일 크기 한도를 넘었습니다.")
    if fmt in ("docx", "hwpx"):
        from app.reference_office import analyze_office
        return analyze_office(data, fmt, settings)
    from app.reference_media import analyze_media
    return analyze_media(data, fmt, {**settings, **({"page": page} if page else {})})


def worker(fmt, page, deadline):
    settings = load_settings()
    harden_worker(
        max_file_bytes=LIMITS.worker_file_bytes,
        memory_bytes=settings["worker_memory_bytes"],
    )
    data = Path("request.bin").read_bytes()
    Path("request.bin").unlink()
    if fmt == "targets":
        from app.reference_rules import targets
        try:
            request = json.loads(data.decode("utf-8-sig"))
            if not isinstance(request, dict) or request.keys() != {"source"} or not isinstance(request["source"], str):
                raise ValueError
        except (ValueError, RecursionError):
            raise InputError({"source": "대상을 확인할 마크다운 원고를 선택하세요."}) from None
        result = {"targets": targets(request["source"])}
    else:
        # Upload and process startup already consumed part of the supervisor's budget.
        settings["timeout_seconds"] = min(settings["timeout_seconds"], max(0, deadline - monotonic()))
        result = analyze(data, fmt, page, settings)
    Path("response.json").write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), "utf-8")


if __name__ == "__main__":
    try:
        worker(sys.argv[1], int(sys.argv[2]), float(sys.argv[3]))
    except (ValueError, RecursionError) as error:
        field = "source" if sys.argv[1] == "targets" else "reference"
        if isinstance(error, RecursionError):
            message = "참고 파일의 구조가 너무 깊습니다."
        else:
            message = sanitize_user_error(error, "참고 파일을 분석할 수 없습니다.")
        Path("invalid.json").write_text(json.dumps([field, message]), "utf-8")
        sys.exit(INVALID_INPUT)
