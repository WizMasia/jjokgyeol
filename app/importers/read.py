"""Import one in-memory manuscript without opening paths supplied by the document."""
from pathlib import PurePath
import struct
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile
from zlib import error as CompressionError

from app.security import SafeZipArchive
from . import docx, hwp, hwpx, text_html
from .common import ImportResult, LIMITS, Manuscript


def _office(data, reader, out):
    with SafeZipArchive(
        data,
        max_entries=LIMITS["max_entries"],
        max_uncompressed_bytes=LIMITS["max_uncompressed_bytes"],
        max_compression_ratio=LIMITS["max_compression_ratio"],
        limit_message="Office 파일의 압축 해제 한도를 넘었습니다.",
        duplicate_or_encrypted_message="중복 경로나 암호가 있는 파일입니다.",
        compression_message="지원하지 않는 Office 압축 형식입니다.",
    ) as z:
        reader(z, out)


def import_file(name: str, data: bytes) -> ImportResult:
    if not isinstance(name, str) or not isinstance(data, bytes) or not 0 < len(data) <= LIMITS["max_bytes"]:
        raise ValueError("빈 파일이거나 원본 파일 크기가 한도를 넘었습니다.")
    ext = PurePath(name).suffix.lower()
    out = Manuscript()
    try:
        if ext == ".txt":
            return text_html.plain(data)
        if ext in (".html", ".htm"):
            text_html.html(data, out)
        elif ext in (".docx", ".hwpx"):
            _office(data, docx.read if ext == ".docx" else hwpx.read, out)
        elif ext == ".hwp":
            hwp.read(data, out)
        else:
            raise ValueError("지원하지 않는 원고 형식입니다.")
        return out.finish()
    except (BadZipFile, ParseError, CompressionError, struct.error, EOFError, OSError,
            UnicodeError, LookupError, RecursionError, OverflowError) as error:
        raise ValueError("파일의 인코딩 또는 문서 구조가 올바르지 않습니다.") from error
