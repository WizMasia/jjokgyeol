"""Server and isolated worker limits, loaded once from this installation."""
from dataclasses import dataclass, fields
from pathlib import Path
import sys

from app.configuration import CONFIG_DIR, read_config


@dataclass(frozen=True)
class Operations:
    request_body_bytes: int  # Maximum body on render and style-file requests.
    render_slots: int  # Concurrent renders per web process, including delivery.
    timeout_seconds: float  # Render deadline and style-file body read timeout.
    worker_file_bytes: int  # Maximum size of any one file written by a worker.


def load_operations(path: Path = CONFIG_DIR / "operations.json") -> Operations:
    values = read_config(path)
    if not isinstance(values, dict):
        raise ValueError(f"{path}: expected an object of limits")

    names = {field.name for field in fields(Operations)}
    if unknown := values.keys() - names:
        raise ValueError(f"{path}: unknown limit {sorted(unknown)[0]}")
    for name in sorted(names):
        value = values.get(name)
        if name == "timeout_seconds":
            valid = type(value) in (int, float) and 0 < value <= sys.float_info.max
        else:
            valid = type(value) is int and value > 0
        if not valid:
            raise ValueError(f"{path}: invalid or missing {name}")
    if values["worker_file_bytes"] > sys.maxsize:
        raise ValueError(f"{path}: worker_file_bytes exceeds the platform file-size limit")
    return Operations(**values)


LIMITS = load_operations()
