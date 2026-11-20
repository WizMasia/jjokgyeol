"""Compare imported manuscripts against the source records, then reopen the Markdown."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import socket
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from app.document import parse_md


ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "tests/fixtures/imports"
SUMS = {ROOT / path: digest for digest, path in
        (line.split(None, 1) for line in (SAMPLES / "SHA256SUMS").read_text().splitlines())}


@pytest.fixture
def convert():
    from app.importers.read import import_file
    return import_file


def original(convert, path):
    data = path.read_bytes()
    assert sha256(data).hexdigest() == SUMS[path]
    result = convert(path.name, data)
    assert sha256(data).hexdigest() == sha256(path.read_bytes()).hexdigest() == SUMS[path]
    return result


def visible(inlines):
    return "".join(i.text if i.kind == "text" else visible(i.children) for i in inlines)


def reopened(result):
    return [(b.kind, b.level, visible(b.inlines)) for b in parse_md(result.markdown).blocks]


@pytest.mark.parametrize("fmt", ["txt", "html", "docx", "hwpx", "hwp"])
def test_format_sample(fmt, convert, monkeypatch):
    def no_network(*args):
        pytest.fail("가져오기가 외부 주소를 열었습니다.")
    monkeypatch.setattr(socket, "getaddrinfo", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    name = {"txt": "field.txt", "html": "field.html", "docx": "lists.docx",
            "hwpx": "ParaHead.hwpx", "hwp": "lists.hwp"}[fmt]
    result = original(convert, SAMPLES / name)
    blocks = reopened(result)
    if fmt == "txt":
        assert result.markdown == (SAMPLES / name).read_text()
        assert blocks == [("paragraph", None, "관측 별빛을 기록했다."), ("paragraph", None, "측정을 마쳤다.")]
    elif fmt == "html":
        assert result.markdown.strip() == ("## 관측\n\n다음 **표본**의 "
                                           "[자료 보기](https://example.invalid/data?q=moon&n=2).\n\n앞뒤")
        assert any("그림" in w for w in result.warnings)
    elif fmt == "docx":
        assert blocks[0] == ("heading", 2, "Some nested lists")
        assert blocks[6:9] == [("list_item", 1, "two"), ("list_item", 2, "three"), ("list_item", 3, "four")]
        assert parse_md(result.markdown).blocks[0].inlines[0].kind == "strong"
        assert blocks[9] == ("paragraph", None, "Sub paragraph")
    elif fmt == "hwpx":
        at = blocks.index(("list_item", 1, "번호3"))
        assert blocks[at+1:at+8] == [
            ("list_item", 2, "번호3-1"), ("list_item", 2, "번호3-2"), ("list_item", 1, "번호4"),
            ("list_item", 2, "번호-4-1-1-1"), ("list_item", 2, "번호-4-1-1-2"),
            ("list_item", 2, "번호-4-1-2"), ("list_item", 3, "번호-4-1-2-1")]
        assert blocks[-3:] == [("heading", 1, "개요2"), ("heading", 2, "개요2-1"), ("heading", 3, "개요2-1-1")]
        assert any("4단계" in w and "2단계" in w for w in result.warnings)
    else:
        # The upstream decoded HWP contains this label in paragraphs 18, 34, 45 and 61.
        assert [b for b in blocks if b[2] == "3-2-1"] == [
            ("list_item", 3, "3-2-1"), ("list_item", 3, "3-2-1"),
            ("heading", 3, "3-2-1"), ("heading", 3, "3-2-1")]
        i = blocks.index(("list_item", 3, "3-2-1"))
        assert blocks[i-3:i] == [("list_item", 1, "3"), ("list_item", 2, "3-1"), ("list_item", 2, "3-2")]


@pytest.mark.parametrize("path", [p.relative_to(ROOT) for p in SUMS if ROOT / "proofs" in p.parents])
def test_office_body_only(path, convert):
    result = original(convert, ROOT / path)
    prefix = "" if "reference-style" in str(path) else "ORBITONLY "
    assert reopened(result) == [
        ("heading", 1, "별자리관측소 자료집"), ("heading", 1, "차례"), ("paragraph", None, "관측 거리와 시각"),
        ("heading", 1, "표본 기록"), ("paragraph", None, prefix + "잔별수첩에는 구름이 걷힌 밤의 하늘을 적었다."),
        ("list_item", 1, "망원경의 눈금을 확인한다."), ("list_item", 1, "초점 조절 장치를 살핀다.")]


def test_binary_hwp_runs_controls_and_tables(convert):
    styled = original(convert, SAMPLES / "charshape.hwp")
    assert [(i.kind, visible(i.children)) for i in parse_md(styled.markdown).blocks[1].inlines] == [
        ("emphasis", "기울임"), ("strong", "진하게")]
    assert reopened(original(convert, SAMPLES / "headerfooter.hwp")) == [("paragraph", None, "첫 페이지")]
    mixed = original(convert, SAMPLES / "sample-5017.hwp")
    texts = [text for _, _, text in reopened(mixed)]
    assert texts[0] == "한글 2005 예제 파일입니다."
    assert texts[-1] == "다음 페이지"
    assert [s for s in texts if s in {"A0", "B0", "A1", "B10", "B11", "table2"}] == [
        "A0", "B0", "A1", "B10", "B11", "table2"]
    assert "표  2x2짜리표" in texts
    assert any("그림" in w for w in mixed.warnings)
    assert any("표" in w for w in mixed.warnings)


def package(parts):
    buf = BytesIO()
    with ZipFile(buf, "w", ZIP_DEFLATED) as z:
        for path, data in parts.items():
            z.writestr(path, data)
    return buf.getvalue()


def test_hwpx_partial_bold(convert):
    from xml.etree import ElementTree as ET
    with ZipFile(ROOT / "proofs/duplex-running-heads/outer.hwpx") as z:
        parts = {n: z.read(n) for n in z.namelist()}
    header = ET.fromstring(parts["Contents/header.xml"])
    shape = header.find(".//{*}charPr[@id='7']")
    ET.SubElement(shape, shape.tag.replace("charPr", "bold"))
    parts["Contents/header.xml"] = ET.tostring(header, encoding="utf-8")
    section = ET.fromstring(parts["Contents/section0.xml"])
    p = section.findall("{*}p")[4]
    run = p.find("{*}run")
    run.find("{*}t").text = "가운데"
    right = ET.SubElement(p, run.tag, charPrIDRef="0")
    ET.SubElement(right, run.find("{*}t").tag).text = "끝"
    parts["Contents/section0.xml"] = ET.tostring(section, encoding="utf-8")
    result = convert("복사본.hwpx", package(parts))
    assert "**가운데**끝" in result.markdown


def test_docx_partial_bold_fields_and_picture(convert):
    parts = {"word/document.xml": """<document><body><p>
        <r><t>앞</t></r><r><rPr><b/></rPr><t>굵게</t></r><r><drawing/></r>
        <fldSimple instr=" PAGE "><r><t>901</t></r></fldSimple>
        <r><fldChar fldCharType="begin"/></r><r><instrText> PAGE </instrText></r>
        <r><fldChar fldCharType="separate"/></r><r><t>902</t></r><r><fldChar fldCharType="end"/></r>
        <r><t>끝</t></r></p></body></document>"""}
    result = convert("book.DOCX", package(parts))
    assert result.markdown.strip() == "앞**굵게**끝"
    assert any("그림" in w for w in result.warnings)


@pytest.mark.parametrize("name,data", [
    ("x.txt", b"\xff"), ("x.txt", b"a\x00b"), ("x.odt", b"abcd"), ("x.hwp", b"fake hwp"),
    ("x.docx", b"PK"), ("x.hwpx", package({"x": "x"})), ("x.docx", package({"word/document.xml": "<"})),
    ("x.docx", package({"word/document.xml": '<!DOCTYPE x SYSTEM "file:///ignored"><document/>'})),
    ("x.docx", package({"word/document.xml": "a" * 2**20})),
    ("x.txt", b"x" * (16 * 1024 * 1024 + 1)),
])
def test_invalid_is_atomic(name, data, convert):
    with pytest.raises(ValueError):
        convert(name, data)
    assert convert("next.TXT", b"\xef\xbb\xbfshort\r\n\r\nnext").markdown == "short\n\nnext"
