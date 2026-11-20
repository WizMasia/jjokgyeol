"""Read JSON owned by the installation, independently of a job's working directory."""
import json
from decimal import InvalidOperation
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
CONFIG_DIR = APP_DIR / "config"


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate field {key}")
        result[key] = value
    return result


def read_config(path: Path, *, parse_float=float):
    try:
        with path.open(encoding="utf-8") as stream:
            return json.load(stream, parse_float=parse_float, object_pairs_hook=_unique_fields)
    except (OSError, ValueError, InvalidOperation) as error:
        raise ValueError(f"{path}: {error}") from error
