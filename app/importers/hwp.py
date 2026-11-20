"""HWP 5 DocInfo and BodyText records inside an OLE compound file."""
from io import BytesIO
import re
from struct import pack, iter_unpack, unpack_from
import zlib

from .common import LIMITS, styled


CHAR_SHAPE, PARA_SHAPE = 21, 25
PARA_HEADER, PARA_TEXT, PARA_CHAR_SHAPE, CTRL_HEADER = 66, 67, 68, 71
HWP_SIGNATURE = b"HWP Document File".ljust(32, b"\0")


def _records(data, out):
    pos = 0
    while pos < len(data):
        out.count()
        header, = unpack_from("<I", data, pos)
        pos += 4
        size = header >> 20
        if size == 0xfff:
            size, = unpack_from("<I", data, pos)
            pos += 4
        if size > len(data) - pos:
            raise ValueError("HWP 레코드 길이가 올바르지 않습니다.")
        yield header & 0x3ff, (header >> 10) & 0x3ff, memoryview(data)[pos:pos + size]
        pos += size


def _text(raw):
    """Control codes occupy one or eight UTF-16 units, including their payload."""
    pieces = []
    start = pos = 0
    while pos < len(raw):
        code, = unpack_from("<H", raw, pos)
        if code >= 32:
            pos += 2
            continue
        pieces.append(bytes(raw[start:pos]).decode("utf-16le"))
        if code in (9, 10, 30, 31):
            pieces.append(" ")
        elif code == 24:
            pieces.append("-")
        pos += 16 if (1 <= code <= 23 and code not in (10, 13)) else 2
        if pos > len(raw):
            raise ValueError("HWP 문단 제어 문자가 잘렸습니다.")
        start = pos
    pieces.append(bytes(raw[start:]).decode("utf-16le"))
    return "".join(pieces)


def _paragraph(raw, ranges, chars):
    if not raw:
        return ""
    if len(raw) % 2 or not ranges or ranges[0][0] != 0:
        raise ValueError("HWP 본문과 글자 모양의 범위가 올바르지 않습니다.")
    runs = []
    for i, (start, shape) in enumerate(ranges):
        end = ranges[i + 1][0] if i + 1 < len(ranges) else len(raw) // 2
        if not start <= end <= len(raw) // 2:
            raise ValueError("HWP 글자 모양 위치가 본문 밖입니다.")
        text = _text(raw[start * 2:end * 2])
        runs.append((text, bool(chars[shape] & 2), bool(chars[shape] & 1)))
    return styled(runs)


def _section(data, paras, chars, out):
    paragraph = None
    raw, ranges = b"", []
    excluded = None
    for tag, level, payload in _records(data, out):
        if excluded is not None:
            if level > excluded:
                continue
            excluded = None
        if tag == CTRL_HEADER:
            control = bytes(payload[:4])[::-1]
            if control in (b"head", b"foot", b"fn  ", b"en  ", b"gso "):
                excluded = level
                if control == b"gso ":
                    out.warn("HWP 본문의 그림·도형을 생략했습니다.")
                elif control in (b"fn  ", b"en  "):
                    out.warn("HWP 각주·미주를 생략했습니다.")
            elif control == b"tbl ":
                out.warn("HWP 표의 셀을 문단으로 옮겼습니다.")
        elif tag == PARA_HEADER:
            if paragraph:
                out.block(_paragraph(raw, ranges, chars), *paragraph)
            shape, = unpack_from("<H", payload, 8)
            flags = paras[shape]
            kind = ("body", "heading", "ordered", "bullet")[(flags >> 23) & 3]
            paragraph = kind, 1 + ((flags >> 25) & 7)
            raw, ranges = b"", []
        elif tag == PARA_TEXT:
            raw = payload
        elif tag == PARA_CHAR_SHAPE:
            ranges = list(iter_unpack("<II", payload))
    if paragraph:
        out.block(_paragraph(raw, ranges, chars), *paragraph)


def read(data, out):
    import olefile

    with olefile.OleFileIO(BytesIO(data), raise_defects=olefile.DEFECT_INCORRECT) as ole:
        if ole.get_size("FileHeader") != 256:
            raise ValueError("HWP 5 파일 헤더 크기가 올바르지 않습니다.")
        header = ole.openstream("FileHeader").read()
        if header[:32] != HWP_SIGNATURE or header[35] != 5:
            raise ValueError("HWP 5 바이너리 원고를 선택하세요.")
        flags, = unpack_from("<I", header, 36)
        if flags & (2 | 4 | 16 | 256 | 512 | 1024):
            raise ValueError("암호화·배포 제한된 HWP는 읽을 수 없습니다.")
        remaining = LIMITS["max_uncompressed_bytes"]

        def stream(name):
            nonlocal remaining
            if ole.get_size(name) > LIMITS["max_bytes"]:
                raise ValueError("HWP 스트림 크기가 한도를 넘었습니다.")
            raw = ole.openstream(name).read()
            if flags & 1:
                budget = min(remaining, len(raw) * LIMITS["max_compression_ratio"])
                inflater = zlib.decompressobj(-15)
                raw = inflater.decompress(raw, budget + 1)
                if not inflater.eof or len(raw) > budget:
                    raise ValueError("HWP 압축 스트림이 손상되었거나 한도를 넘었습니다.")
                # Some producers append the gzip CRC32 and size after raw DEFLATE.
                if inflater.unused_data and inflater.unused_data != pack("<II", zlib.crc32(raw), len(raw)):
                    raise ValueError("HWP 압축 스트림이 손상되었거나 한도를 넘었습니다.")
            remaining -= len(raw)
            if remaining < 0:
                raise ValueError("HWP 압축 해제 한도를 넘었습니다.")
            return raw

        entries = ole.listdir()
        if len(entries) > LIMITS["max_entries"]:
            raise ValueError("HWP 스트림 수가 한도를 넘었습니다.")
        sections = sorted(int(path[1][7:]) for path in entries if len(path) == 2
                          and path[0] == "BodyText" and re.fullmatch(r"Section[0-9]+", path[1]))
        if not sections or sections != list(range(len(sections))):
            raise ValueError("HWP 본문 구역을 찾을 수 없습니다.")
        chars, paras = [], []
        for tag, _, payload in _records(stream("DocInfo"), out):
            if tag == CHAR_SHAPE:
                # Seven fonts (uint16), four arrays of seven bytes, then base size (int32).
                chars.append(unpack_from("<I", payload, 46)[0])
            elif tag == PARA_SHAPE:
                paras.append(unpack_from("<I", payload)[0])
        for i in sections:
            _section(stream(f"BodyText/Section{i}"), paras, chars, out)
