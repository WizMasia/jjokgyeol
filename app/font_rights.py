"""Inspect static sfnt metadata locally. Keep decisions in sync with font-rights.js."""
import hashlib
import re
import struct

from app.font_assets import FONT_ASSETS

_OFL_URL = (r"https?://(?:(?:www\.)?openfontlicense\.org(?:/open-font-license-official-text|/documents/OFL\.txt)?"
            r"|scripts\.sil\.org/(?:OFL|cms/scripts/page\.php\?item_id=OFL_web)"
            r"|(?:www\.)?spdx\.org/licenses/OFL-1\.1\.html|opensource\.org/licenses?/OFL-1\.1"
            r"|dev\.naver\.com/wiki/nanumfont/index\.php/OpenFontLicense)/?")
_OFL = re.compile(
    r"(?:(?:this font(?: software)? is )?licen[sc]ed under (?:the )?)?"
    r"(?:SIL Open Font License,? (?:Version |v)?1\.1|(?:SPDX-License-Identifier:\s*)?OFL-1\.1)\.?"
    r"(?: This license is (?:copied below,? and is also )?available with a FAQ at:? " + _OFL_URL + r"\.?)?",
    re.IGNORECASE | re.ASCII)
_OFL_HEADING = "SIL OPEN FONT LICENSE Version 1.1 - 26 February 2007"
# SHA-256 of the bundled official text after dropping decorative rules and folding whitespace.
_OFL_TEXT_HASH = "70006bea3c19ca31505acc2ab97296f48cccbf972825a66d81321f1474e30ebf"
_NAME_IDS = {0, 1, 4, 5, 13, 14, 16}
# Name records may point to the same bytes repeatedly. Bound decoded work as well as file ranges.
_MAX_NAME_BYTES = 1 << 20
_OS2_LENGTH = (68, 86, 96, 96, 96, 100)
_BAD = "글꼴 테이블이 손상되었거나 지원 범위를 벗어납니다."


def _require(ok, reason=_BAD):
    if not ok:
        raise ValueError(reason)


def _u16(data, offset):
    return struct.unpack_from(">H", data, offset)[0]


def _tables(data):
    _require(data[:4] in (b"OTTO", b"\x00\x01\x00\x00"), "정적 TTF·OTF 단일 파일만 지원합니다.")
    _require(len(data) >= 12)
    count = _u16(data, 4)
    end = 12 + count * 16
    _require(0 < count <= 4095 and end <= len(data))
    tables, ranges = {}, []
    for tag, _, offset, size in struct.iter_unpack(">4sIII", data[12:end]):
        _require(tag not in tables and end <= offset <= len(data) - size and offset % 4 == 0)
        tables[tag] = memoryview(data)[offset:offset + size]
        if size:
            ranges.append((offset, offset + size))
    ranges.sort()
    _require(all(a[1] <= b[0] for a, b in zip(ranges, ranges[1:])))
    _require(not ({b"fvar", b"gvar", b"CFF2"} & tables.keys()), "가변 글꼴은 현재 조판할 수 없습니다.")
    outlines = {b"CFF ": 4} if data[:4] == b"OTTO" else {b"loca": 4, b"glyf": 1}
    for tag, minimum in {b"head": 54, b"maxp": 6, b"hhea": 36, b"hmtx": 4, b"cmap": 4, **outlines}.items():
        _require(len(tables.get(tag, ())) >= minimum, "정적 윤곽선 조판에 필요한 테이블을 확인할 수 없습니다.")
    return tables


def _encoding(platform, enc):
    if platform == 0 or (platform == 3 and enc in (0, 1, 10)):
        return "utf-16-be"
    if platform == 1 and enc == 0:
        return "mac_roman"
    return None


def _names(table):
    _require(len(table) >= 6)
    fmt, count, storage = struct.unpack_from(">HHH", table)
    end = 6 + 12 * count
    _require(fmt in (0, 1) and count <= 4096 and end <= storage <= len(table))
    if fmt == 1:
        _require(end + 2 <= storage)
        languages = _u16(table, end)
        _require(end + 2 + 4 * languages <= storage)
        for size, offset in struct.iter_unpack(">HH", table[end + 2:end + 2 + 4 * languages]):
            _require(storage + offset + size <= len(table))
    values = {key: {} for key in _NAME_IDS}
    undecoded = set()
    total = 0
    for platform, enc, lang, key, size, offset in struct.iter_unpack(">6H", table[6:end]):
        start = storage + offset
        _require(start + size <= len(table))
        if key not in values or not size:
            continue
        total += size
        _require(total <= _MAX_NAME_BYTES)
        raw = bytes(table[start:start + size])
        codec = _encoding(platform, enc)
        if codec is None and platform == 1 and enc in (1, 2, 3, 25) and raw.isascii():
            codec = "ascii"
        if codec is None:
            undecoded.add(key)
            continue
        try:
            value = raw.decode(codec)
        except UnicodeError:
            undecoded.add(key)
            continue
        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", value):
            undecoded.add(key)
            continue
        if value.strip():
            rank = 0 if (platform == 3 and lang == 0x412) or (platform == 1 and lang == 23) else 1
            values[key][value] = min(rank, values[key].get(value, rank))
    return {key: sorted(records, key=records.get) for key, records in values.items()}, undecoded


def _normalized(text):
    return " ".join(word for word in re.split(r"[ \t\r\n]+", text) if word and not re.fullmatch("-{3,}", word))


def _is_ofl(text, holders):
    text = _normalized(text)
    if _OFL.fullmatch(text):
        return True
    prefix, heading, body = text.partition(_OFL_HEADING)
    if not heading or hashlib.sha256((heading + body).encode()).hexdigest() != _OFL_TEXT_HASH:
        return False
    prefix = prefix.strip()
    if not prefix or prefix in holders:
        return True
    grant = _OFL.search(prefix)
    return bool(grant and grant.end() == len(prefix) and prefix[:grant.start()].strip() in holders | {""})


def _decide(face, names, undecoded):
    flags, version = face["fsType"], face["os2Version"]
    effective = flags & 15 if version < 2 else flags
    mode = effective & 14
    if effective & ~0x30e or (version >= 3 and mode not in (0, 2, 4, 8)):
        return "unknown", "unknown", "OS/2 내장 권한 비트의 의미가 불분명합니다."
    # In OS/2 v0–2, the least restrictive of the embedding permissions takes precedence.
    mode = 8 if mode & 8 else 4 if mode & 4 else mode
    if face["known"]:
        return "allowed", "allowed", "설치된 배포본의 SHA-256과 일치합니다. 원본에는 해당 배포본의 OFL 고지를 첨부합니다."
    holders = {_normalized(s) for s in names[0]}
    ofl = bool(names[13]) and all(_is_ofl(s, holders) for s in names[13])
    if ofl and any(re.search(r"personal use only|non[- ]commercial use only|no redistribution|재배포\s*금지|상업적?\s*이용\s*금지", s, re.I) for s in holders):
        return "unknown", "unknown", "저작권 표기에 OFL 허용 범위와 충돌하는 제한이 있습니다."
    if (undecoded & {13, 14} or (names[13] and not ofl)
            or (names[14] and (not ofl or not all(re.fullmatch(_OFL_URL, s, re.I | re.ASCII) for s in names[14])))):
        return "unknown", "unknown", "파일에 표시된 약관 전체의 적용 범위를 확인할 수 없습니다."
    if ofl and (mode in (2, 4) or effective & 0x200):
        return "unknown", "unknown", "OFL 약관과 OS/2 내장 제한이 충돌합니다."
    if effective & 0x200:
        return "restricted", "restricted", "비트맵만 내장할 수 있어 윤곽선 글꼴을 사용할 수 없습니다."
    if mode == 2:
        return "restricted", "restricted", "OS/2에 내장 제한이 명시되어 있습니다."
    if mode == 4:
        return "restricted", "restricted", "미리보기·인쇄 전용입니다. 현재 PDF는 읽기 전용을 보장하지 않습니다."
    attributed = bool(holders) and all(not re.search(r"<(?:dates?|copyright holder)>", s, re.I) for s in holders)
    archive = "allowed" if ofl and attributed and 0 not in undecoded else "unknown"
    if effective & 0x100:
        return "restricted", archive, "부분 내장이 금지되어 있습니다. 현재 PDF 조판은 전체 내장을 보장하지 않습니다."
    if ofl:
        if archive == "allowed":
            return "allowed", archive, "파일에 SIL OFL 1.1이 명시되어 있습니다. 저작권 표기와 라이선스 전문을 동봉합니다."
        return "allowed", archive, "SIL OFL 1.1이 명시되었으나 원본에 동봉할 저작권 표기를 확인할 수 없습니다."
    if mode == 8:
        return "allowed", archive, "OS/2가 편집 가능한 문서 내장을 허용합니다. 원본 재배포 근거는 없습니다."
    return "unknown", "unknown", "설치 가능 표시만으로는 PDF 내장과 원본 재배포 권한을 확인할 수 없습니다."


def inspect_face(data: bytes) -> dict:
    digest = hashlib.sha256(data).hexdigest()
    known = FONT_ASSETS["faces"].get(digest)
    face = dict(hash=digest, name="", family="", version="", weight="regular", copyright="", license="", url="",
                known=bool(known), supported=False, fsType=None, os2Version=None)
    pdf = archive = "unknown"
    try:
        tables = _tables(data)
        names, undecoded = _names(tables.get(b"name", b""))
        urls = [s for s in names[14] if re.fullmatch(r"https?://[A-Za-z0-9.-]+(?::[0-9]+)?(?:[/?#][^\s\\]*)?", s, re.I | re.ASCII)]
        face.update(name=next(iter(names[4] or names[16] or names[1]), ""),
                    version=next(iter(names[5]), ""), copyright="\n".join(names[0]),
                    license="\n".join(names[13]), url=next(iter(urls), ""))
        face["family"] = next(iter(names[16] or names[1]), face["name"])
        metrics = tables.get(b"OS/2", b"")
        _require(len(metrics) >= 10)
        version = _u16(metrics, 0)
        _require(version < len(_OS2_LENGTH) and len(metrics) >= _OS2_LENGTH[version])
        face.update(supported=True, os2Version=version, fsType=_u16(metrics, 8),
                    weight="bold" if _u16(metrics, 4) >= 600 else "regular")
        if known:
            face.update(weight=known["weight"], version=known["version"])
        pdf, archive, reason = _decide(face, names, undecoded)
    except ValueError as error:
        reason = str(error)
    status = {"pdf": pdf, "archive": archive, "remote": archive}
    return {**face, **{key: value == "allowed" for key, value in status.items()}, "status": status, "reason": reason}
