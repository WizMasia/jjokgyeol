"""Read a length-prefixed manuscript and its selected font files inside the worker."""
from decimal import Decimal
import hashlib
import json
import re

from app.configuration import unique_fields
from app.font_assets import FONT_LIMITS, FRAME_LIMIT
from app.font_rights import inspect_face
from app.operations import LIMITS


def unpack_frame(raw: bytes, *, remote=False):
    error = "글꼴 전송 형식이나 파일 크기를 확인하세요."
    if not 4 <= len(raw) <= FRAME_LIMIT:
        raise ValueError(error)
    head_size = int.from_bytes(raw[:4], "big")
    pos = 4 + head_size
    if not 0 < head_size <= LIMITS.request_body_bytes or pos > len(raw):
        raise ValueError(error)
    head = json.loads(raw[4:pos].decode("utf-8"), object_pairs_hook=unique_fields, parse_float=Decimal)
    if not isinstance(head, dict) or head.keys() != {"request", "faces"} or not isinstance(head["request"], dict):
        raise ValueError(error)
    faces = head["faces"]
    if (not isinstance(faces, list) or len(faces) > FONT_LIMITS["count"]
            or len(raw) - pos > FONT_LIMITS["total_bytes"]):
        raise ValueError(error)
    assets = {}
    for record in faces:
        if not isinstance(record, dict) or record.keys() != {"hash", "size"}:
            raise ValueError(error)
        digest, size = record["hash"], record["size"]
        if (not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest)
                or digest in assets or type(size) is not int or not 0 < size <= FONT_LIMITS["file_bytes"]
                or size > len(raw) - pos):
            raise ValueError(error)
        data = raw[pos:pos + size]
        pos += size
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("글꼴 파일 식별값이 일치하지 않습니다.")
        rights = inspect_face(data)
        if not rights["pdf"] or remote and not rights["remote"]:
            raise ValueError("이 조판기에 해당 글꼴을 포함할 권한을 확인할 수 없습니다.")
        assets[digest] = data
    if pos != len(raw):
        raise ValueError(error)
    return head["request"], assets
