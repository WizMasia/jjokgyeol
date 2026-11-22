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
    assert "표 3 2x2짜리표" in texts
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
    assert faces(parse_md(result.markdown).blocks[4].inlines) == [("가운데", True, False), ("끝", False, False)]


def test_docx_partial_bold_fields_and_picture(convert):
    parts = {"word/document.xml": """<document><body><p>
        <r><t>앞</t></r><r><rPr><b/></rPr><t>굵게</t></r><r><drawing/></r>
        <fldSimple instr=" PAGE "><r><t>901</t></r></fldSimple>
        <r><fldChar fldCharType="begin"/></r><r><instrText> PAGE </instrText></r>
        <r><fldChar fldCharType="separate"/></r><r><t>902</t></r><r><fldChar fldCharType="end"/></r>
        <r><t>끝</t></r></p></body></document>"""}
    result = convert("book.DOCX", package(parts))
    assert faces(parse_md(result.markdown).blocks[0].inlines) == [
        ("앞", False, False), ("굵게", True, False), ("끝", False, False)]
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


def faces(inlines, bold=False, italic=False):
    spans = []
    for inline in inlines:
        if inline.kind == "text":
            spans.append((inline.text, bold, italic))
        else:
            spans.extend(faces(inline.children, bold or inline.kind == "strong",
                               italic or inline.kind == "emphasis"))
    return spans


def word_body(xml, **parts):
    return package({"word/document.xml": f"<document><body>{xml}</body></document>", **parts})


def test_overlapping_faces_reopen(convert):
    runs = "<r><rPr><b/></rPr><t>관측</t></r><r><rPr><b/><i/></rPr><t>시각</t></r><r><rPr><i/></rPr><t>기록</t></r>"
    expected = [("관측", True, False), ("시각", True, True), ("기록", False, True)]
    html = "<p><b>관측</b><strong><em>시각</em></strong><i>기록</i></p>"
    for name, data in [("관측.docx", word_body("<p>"+runs+"</p>")), ("관측.html", html.encode())]:
        result = convert(name, data)
        block, = parse_md(result.markdown).blocks
        assert faces(block.inlines) == expected
        assert not result.warnings


@pytest.mark.parametrize("fmt", ["txt", "html", "docx"])
def test_source_prose_reopens_as_literal_paragraphs(fmt, convert):
    from html import escape
    paragraphs = [r"1. 관측 *별표*와 _밑줄_ \% & <b>글</b> &#35;", "# 제목 기호",
                  "- 목록 기호", "[^표]: 원문", r"기호 \*와 \\를 적었다.", "12) 닫는 괄호"]
    if fmt == "txt":
        data = "\n\n".join(paragraphs).encode()
    elif fmt == "html":
        data = "".join(f"<p>{escape(s)}</p>" for s in paragraphs).encode()
    else:
        data = word_body("".join(f"<p><r><t>{escape(s)}</t></r></p>" for s in paragraphs))
    doc = parse_md(convert(f"기호.{fmt}", data).markdown)
    assert [(b.kind, faces(b.inlines)) for b in doc.blocks] == [
        ("paragraph", [(s, False, False)]) for s in paragraphs]
    assert doc.footnotes == {}


def test_hwpx_caption_and_page_fields_in_same_run(convert):
    with ZipFile(SAMPLES / "ParaHead.hwpx") as z:
        parts = {n: z.read(n) for n in z.namelist()}
    parts["Contents/section0.xml"] = """<sec><p><run><t>표 </t><ctrl><autoNum num="3" numType="TABLE">
      <autoNumFormat type="DIGIT" prefixChar="(" suffixChar=")"/></autoNum></ctrl><t> 관측 2026</t>
      <ctrl><autoNum num="17" numType="PAGE"/></ctrl><t> 끝</t></run></p></sec>"""
    assert reopened(convert("관측.hwpx", package(parts))) == [("paragraph", None, "표 (3) 관측 2026 끝")]


def test_nested_html_list_continuations(convert):
    data = """<ol><li><p>망원경</p><p>준비</p><ul><li>눈금</li></ul>
    <p>다시 보관</p></li><li>카메라</li></ol>"""
    result = convert("도구.html", data.encode())
    blocks = parse_md(result.markdown).blocks
    assert [(visible(b.inlines), b.kind, b.level) for b in blocks] == [
        ("망원경", "list_item", 1), ("준비", "list_item", 1), ("눈금", "list_item", 2),
        ("다시 보관", "list_item", 1), ("카메라", "list_item", 1)]
    assert [(b.first, b.ordinal) for b in blocks] == [(True, 1), (False, 1), (True, 1), (False, 1), (True, 2)]
    assert blocks[2].parent_group == blocks[0].group == blocks[3].group == blocks[4].group
    assert not result.warnings


def test_word_list_instance_overrides_format(convert):
    numbering = """<numbering><abstractNum abstractNumId="1"><lvl ilvl="0"><numFmt val="bullet"/></lvl>
    </abstractNum><num numId="2"><abstractNumId val="1"/><lvlOverride ilvl="0">
    <lvl ilvl="0"><numFmt val="decimal"/></lvl></lvlOverride></num></numbering>"""
    data = word_body('<p><pPr><numPr><numId val="2"/></numPr></pPr><r><t>관측</t></r></p>',
                     **{"word/numbering.xml": numbering})
    item, = parse_md(convert("관측.docx", data).markdown).blocks
    assert (item.list_kind, item.level, visible(item.inlines)) == ("ordered", 1, "관측")


def test_empty_word_list_item_keeps_child_and_next_number(convert):
    def item(level, text):
        return (f'<p><pPr><numPr><ilvl val="{level}"/><numId val="2"/></numPr></pPr>'
                f'<r><t>{text}</t></r></p>')

    numbering = """<numbering><abstractNum abstractNumId="1">
      <lvl ilvl="0"><numFmt val="decimal"/></lvl><lvl ilvl="1"><numFmt val="decimal"/></lvl>
      </abstractNum><num numId="2"><abstractNumId val="1"/></num></numbering>"""
    data = word_body(item(0, "") + item(1, "세부 항목") + item(0, "다음 항목"),
                     **{"word/numbering.xml": numbering})
    result = convert("목록.docx", data)
    blocks = parse_md(result.markdown).blocks
    assert [(item.level, item.ordinal, visible(item.inlines)) for item in blocks] == [
        (1, 1, ""), (2, 1, "세부 항목"), (1, 2, "다음 항목")]
    assert not result.warnings


def test_source_loss_locations(convert):
    html = "<p>저녁<img src='moon.png'>달</p>\n<p>아침<img src='venus.png'>별</p>"
    result = convert("하늘.html", html.encode())
    assert reopened(result) == [("paragraph", None, "저녁달"), ("paragraph", None, "아침별")]
    assert len(result.warnings) == 2
    assert "1행" in result.warnings[0] and "2행" in result.warnings[1]


@pytest.mark.parametrize("tag", ["svg", "object"])
def test_nested_embed_is_omitted_as_one_element(convert, tag):
    markup = f"<p>앞<{tag}><{tag}>안쪽</{tag}>바깥 설명</{tag}>뒤</p>"
    result = convert("원고.html", markup.encode())
    assert reopened(result) == [("paragraph", None, "앞뒤")]
    assert result.warnings == [f"HTML 1행 5열의 {tag} 요소를 생략했습니다."]


def test_only_visible_word_tables_report_flattening(convert):
    table = "<tbl><tr><tc><p><r><t>도표</t></r></p></tc></tr></tbl>"
    text_box = "<p><r><t>별</t><pict><txbxContent>"+table+"</txbxContent></pict></r></p>"
    result = convert("하늘.docx", word_body(text_box))
    assert reopened(result) == [("paragraph", None, "별")]
    assert len(result.warnings) == 1 and "그림" in result.warnings[0] and "1" in result.warnings[0]
    result = convert("하늘.docx", word_body(text_box+table))
    assert reopened(result)[1] == ("paragraph", None, "도표")
    assert len(result.warnings) == 2 and any("셀" in w for w in result.warnings)


def test_hwp_caption_stays_with_its_parent_after_table_cells():
    from struct import pack
    from app.importers.common import Manuscript
    from app.importers.hwp import _section

    def record(tag, level, payload):
        return pack("<I", tag | level << 10 | len(payload) << 20) + payload

    def control(code, name):
        return pack("<H4s8xH", code, name[::-1], code)

    table = control(11, b"tbl ")
    number = control(18, b"atno")
    text = "달🌙".encode("utf-16le") + table + " 표 ".encode("utf-16le") + number + number
    header = bytes(22)
    section = b"".join([
        record(66, 0, header), record(67, 1, text), record(68, 1, pack("<IIII", 0, 0, 14, 1)),
        record(71, 1, b" lbt"), record(66, 2, header), record(67, 3, "셀".encode("utf-16le")),
        record(68, 3, pack("<II", 0, 0)),
        record(71, 1, b"onta" + pack("<I4H", 4, 3, 0, 0, 0)),
        record(71, 1, b"onta" + pack("<I4H", 0, 17, 0, 0, 0))])
    out = Manuscript()
    _section(section, [0], [0, 2], out)
    blocks = parse_md(out.finish().markdown).blocks
    assert faces(blocks[0].inlines) == [("달🌙 표 ", False, False), ("3", True, False)]
    assert visible(blocks[1].inlines) == "셀"


def test_unrepresentable_intraword_punctuation_keeps_text_and_reports_face_loss(convert):
    data = "<p>표본<b>「달」</b>기록과 <i>시각</i></p>"
    result = convert("관측.html", data.encode())
    block, = parse_md(result.markdown).blocks
    assert visible(block.inlines) == "표본「달」기록과 시각"
    assert faces(block.inlines)[-1] == ("시각", False, True)
    assert any("강조" in w and "1행" in w for w in result.warnings)


def test_real_empty_html_parent_owns_child_and_continuation(convert):
    result = convert("관측.html", "<ul><li><ol><li>달</li></ol>부모 설명</li></ul>".encode())
    parent, child, tail = parse_md(result.markdown).blocks
    assert (parent.first, parent.level, visible(parent.inlines)) == (True, 1, "")
    assert child.parent_group == parent.group == tail.group
    assert not tail.first and not result.warnings


def test_heading_keeps_literal_trailing_hash(convert):
    result = convert("관측.html", "<h2>분류 #</h2>".encode())
    assert reopened(result) == [("heading", 2, "분류 #")]


def test_office_objects_in_one_paragraph_have_distinct_loss_locations(convert):
    result = convert("별.docx", word_body("<p><r><drawing/><t>별</t><drawing/></r></p>"))
    assert reopened(result) == [("paragraph", None, "별")]
    assert len(result.warnings) == len(set(result.warnings)) == 2
    assert all("문단 1" in w for w in result.warnings)


def test_adjacent_faces_keep_representable_words_around_punctuation(convert):
    html = "<p>이름 <b>관측</b><em>!</em>완료</p>"
    result = convert("기록.html", html.encode())
    block, = parse_md(result.markdown).blocks
    assert visible(block.inlines) == "이름 관측!완료"
    assert ("관측", True, False) in faces(block.inlines)


def test_depth_warnings_identify_each_changed_hwpx_item(convert):
    result = original(convert, SAMPLES / "ParaHead.hwpx")
    changes = [w for w in result.warnings if "4단계" in w and "2단계" in w]
    assert len(changes) == 2
    assert "문단 14" in changes[0] and "문단 15" in changes[1]
