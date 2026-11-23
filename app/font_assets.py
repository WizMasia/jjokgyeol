"""Installed face catalog and byte limits shared by the editor and render workers."""
from pathlib import Path

from app.configuration import APP_DIR, CONFIG_DIR, read_config
from app.operations import LIMITS
from app.publication import FONT_LABELS


def _load_limits():
    values = read_config(CONFIG_DIR / "document-fonts.json")
    if (not isinstance(values, dict) or set(values) != {"file_bytes", "total_bytes", "count", "archive_bytes"}
            or any(type(value) is not int or value <= 0 for value in values.values())
            or not values["file_bytes"] <= values["total_bytes"] <= values["archive_bytes"]):
        raise ValueError("document-fonts.json: invalid font limits")
    return values


FONT_LIMITS = _load_limits()
FRAME_LIMIT = 4 + LIMITS.request_body_bytes + FONT_LIMITS["total_bytes"]


def document_limits():
    return {"request": LIMITS.request_body_bytes, "pdf": LIMITS.worker_file_bytes,
            "file": FONT_LIMITS["archive_bytes"], "font_count": FONT_LIMITS["count"],
            "font_file": FONT_LIMITS["file_bytes"], "font_total": FONT_LIMITS["total_bytes"]}


def _catalog():
    lock = read_config(APP_DIR.parent / "runtime.lock")
    faces = {}
    paths = {}
    for family, records in lock["fonts"].items():
        for weight, record in records.items():
            digest = record["sha256"]
            faces[digest] = {"family": family, "name": FONT_LABELS[family],
                             "weight": weight, "version": record["version"]}
            paths[digest] = Path(record["path"])
    notice = lock["font_license"]
    paths[notice["sha256"]] = Path(notice["path"])
    return {"faces": faces, "notice": notice["sha256"]}, paths


FONT_ASSETS, LOCKED_ASSET_PATHS = _catalog()
