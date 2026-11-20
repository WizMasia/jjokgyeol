"""Self-authored sample archives; QA can import make_docx() and make_hwpx()."""
import io
from zipfile import ZipFile, ZIP_DEFLATED

import pytest

LIMITS = dict(max_bytes=1_000_000, max_uncompressed_bytes=2_000_000, max_entries=100, max_units=100)
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
H = "http://www.hancom.co.kr/hwpml/2011/"


def archive(parts):
    out = io.BytesIO()
    with ZipFile(out, "w", ZIP_DEFLATED) as package:
        for path, content in parts.items():
            package.writestr(path, content)
    return out.getvalue()


def make_docx():
    def rels(items):
        return f'<Relationships xmlns="{PKG}">' + ''.join(
            f'<Relationship Id="{name}" Type="{R}/{kind}" Target="{path}"/>' for name, kind, path in items
        ) + '</Relationships>'

    return archive({
        "[Content_Types].xml": '''<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
          <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
          <Default Extension="xml" ContentType="application/xml"/>
          <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
          <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
          <Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
          <Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/></Types>''',
        "_rels/.rels": rels([("rDoc", "officeDocument", "word/document.xml")]),
        "word/_rels/document.xml.rels": rels([("rS", "styles", "styles.xml"), ("rN", "numbering", "numbering.xml"),
                                              ("rH", "header", "header1.xml")]),
        "word/styles.xml": f'''<w:styles xmlns:w="{W}">
          <w:docDefaults><w:rPrDefault><w:rPr><w:sz w:val="18"/></w:rPr></w:rPrDefault></w:docDefaults>
          <w:style w:type="paragraph" w:default="1" w:styleId="Normal">
            <w:rPr><w:rFonts w:eastAsia="나눔명조" w:ascii="Example Roman"/><w:sz w:val="22"/></w:rPr></w:style>
          <w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/>
            <w:basedOn w:val="Normal"/><w:rPr><w:sz w:val="40"/></w:rPr></w:style>
          <w:style w:type="character" w:styleId="Marked"><w:rPr><w:color w:val="009966"/><w:sz w:val="26"/></w:rPr></w:style>
          </w:styles>''',
        "word/numbering.xml": f'''<w:numbering xmlns:w="{W}"><w:abstractNum w:abstractNumId="3"><w:lvl w:ilvl="0">
          <w:numFmt w:val="decimal"/><w:suff w:val="nothing"/><w:lvlText w:val="%1."/>
          <w:pPr><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl></w:abstractNum>
          <w:num w:numId="7"><w:abstractNumId w:val="3"/></w:num></w:numbering>''',
        "word/header1.xml": f'<w:hdr xmlns:w="{W}"><w:p><w:r><w:t>머리글 예시</w:t></w:r></w:p></w:hdr>',
        "word/document.xml": f'''<w:document xmlns:w="{W}" xmlns:r="{R}"><w:body>
          <w:p><w:pPr><w:pStyle w:val="Title"/><w:sectPr><w:pgSz w:w="7200" w:h="14400"/></w:sectPr></w:pPr>
            <w:r><w:rPr><w:sz w:val="48"/></w:rPr><w:t>표지 제목</w:t></w:r></w:p>
          <w:p><w:pPr><w:spacing w:line="340" w:lineRule="exact"/></w:pPr>
            <w:r><w:t>본문 기본</w:t></w:r><w:r><w:rPr><w:rStyle w:val="Marked"/><w:sz w:val="28"/></w:rPr><w:t>강조 ABC</w:t></w:r></w:p>
          <w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="7"/></w:numPr></w:pPr><w:r><w:t>목록 항목</w:t></w:r></w:p>
          <w:sectPr><w:headerReference w:type="default" r:id="rH"/><w:pgSz w:w="14400" w:h="14400"/>
            <w:pgMar w:top="720" w:right="360" w:bottom="1080" w:left="1440"/></w:sectPr>
          </w:body></w:document>''',
    })


def make_hwpx():
    manifest = '<opf:package xmlns:opf="http://www.idpf.org/2007/opf"><opf:manifest>' + ''.join(
        f'<opf:item id="{k}" href="Contents/{v}.xml" media-type="application/xml"/>'
        for k, v in [("h", "header"), ("a", "section1"), ("b", "section0")]
    ) + '</opf:manifest><opf:spine><opf:itemref idref="h"/><opf:itemref idref="a"/><opf:itemref idref="b"/></opf:spine></opf:package>'
    start = f'<hs:sec xmlns:hs="{H}section" xmlns:hp="{H}paragraph">'
    return archive({
        'mimetype': 'application/hwp+zip', 'Contents/content.hpf': manifest,
        'Contents/header.xml': f'''<hh:head xmlns:hh="{H}head" xmlns:hc="{H}core"><hh:refList>
          <hh:fontfaces><hh:fontface lang="HANGUL"><hh:font id="0" face="나눔명조"/></hh:fontface>
           <hh:fontface lang="LATIN"><hh:font id="0" face="Example Roman"/></hh:fontface></hh:fontfaces>
          <hh:charProperties><hh:charPr id="0" height="1100" textColor="#111111"><hh:fontRef hangul="0" latin="0"/></hh:charPr>
            <hh:charPr id="2" height="2400" textColor="#125634"><hh:fontRef hangul="0"/></hh:charPr>
            <hh:charPr id="3" height="1400" textColor="#009966"><hh:fontRef hangul="0" latin="0"/></hh:charPr></hh:charProperties>
          <hh:paraProperties><hh:paraPr id="0"><hh:lineSpacing type="FIXED" value="1700" unit="HWPUNIT"/></hh:paraPr>
            <hh:paraPr id="4"><hh:heading type="NUMBER" idRef="2" level="0"/></hh:paraPr></hh:paraProperties>
          <hh:styles><hh:style id="0" type="PARA" name="바탕글" paraPrIDRef="0" charPrIDRef="0"/>
            <hh:style id="1" type="PARA" name="표제" paraPrIDRef="0" charPrIDRef="0"/></hh:styles>
          <hh:numberings><hh:numbering id="2"><hh:paraHead level="1" numFormat="DIGIT" textOffsetType="HWPUNIT" textOffset="720">^1.</hh:paraHead>
          </hh:numbering></hh:numberings></hh:refList></hh:head>''',
        'Contents/section1.xml': start + '''<hp:p styleIDRef="1" paraPrIDRef="0"><hp:run charPrIDRef="2">
          <hp:secPr><hp:pagePr width="36000" height="72000"><hp:margin top="3600" right="1800" bottom="5400" left="7200"/>
          </hp:pagePr></hp:secPr><hp:t>표지 제목</hp:t></hp:run></hp:p></hs:sec>''',
        'Contents/section0.xml': start + '''<hp:p styleIDRef="0"><hp:run><hp:t>본문 기본</hp:t></hp:run>
          <hp:run charPrIDRef="3"><hp:t>강조 ABC</hp:t></hp:run></hp:p>
          <hp:p styleIDRef="0" paraPrIDRef="4"><hp:run charPrIDRef="0"><hp:t>목록 항목</hp:t></hp:run></hp:p></hs:sec>''',
    })


def analyze(data, fmt, **limits):
    from app.reference_office import analyze_office
    return analyze_office(data, fmt, {**LIMITS, **limits})


def candidates(unit, role, key):
    return [c for c in unit["candidates"] if c["key"] == key and c["role"] == role]


@pytest.mark.parametrize(("fmt", "make"), [("docx", make_docx), ("hwpx", make_hwpx)])
def test_effective_properties_and_range_evidence(fmt, make):
    result = analyze(make(), fmt)
    cover, body, item = result["units"]
    assert all(isinstance(w, str) for w in result["warnings"])
    assert [u["excerpt"] for u in result["units"]] == ["표지 제목", "본문 기본강조 ABC", "목록 항목"]
    assert cover["section"] != body["section"] and body["section"] == item["section"]
    assert all("구역" in u["location"] and "문단" in u["location"] and "쪽" not in u["location"] for u in result["units"])
    assert [c["value"] for c in candidates(cover, "heading", "font_pt")] == [24]
    sizes = candidates(body, "body", "font_pt")
    assert [c["value"] for c in sizes] == [11, 14]
    assert sizes[0]["evidence"] != sizes[1]["evidence"] and all(c["basis"] == "read" for c in sizes)
    assert 17 in [c["value"] for c in candidates(body, "body", "leading_pt")]
    assert "#009966" in [c["value"] for c in candidates(body, "body", "color")]
    fonts = candidates(body, "body", "font")
    assert ("nanum-myeongjo", "read") in [(c["value"], c["basis"]) for c in fonts]
    assert ("Example Roman", "confirm") in [(c["value"], c["basis"]) for c in fonts]
    assert [c["value"] for c in candidates(item, "list", "levels.1.expr")] == ["{숫자}."]
    assert candidates(cover, "document", "page_mm")[0]["value"] == [127, 254]


def test_docx_geometry_follows_section_ending_and_numbering_level():
    _, body, item = analyze(make_docx(), "docx")["units"]
    assert candidates(body, "document", "margins_mm")[0]["value"] == [12.7, 6.35, 19.05, 25.4]
    assert candidates(body, "header", "left")[0]["value"] == "머리글 예시"
    assert candidates(item, "list", "levels.1.indent_mm")[0]["value"] == 6.35
    assert candidates(item, "list", "rest_indent_mm")[0]["value"] == 12.7
    assert candidates(item, "list", "levels.1.gap_mm")[0]["value"] == 0


def test_hwpx_uses_spine_and_explicit_marker_gap():
    cover, body, item = analyze(make_hwpx(), "hwpx")["units"]
    assert "1" in cover["location"] and "2" in body["location"]
    assert candidates(item, "list", "levels.1.gap_mm")[0]["value"] == 2.54


@pytest.mark.parametrize("limits", [dict(max_bytes=10), dict(max_entries=2), dict(max_uncompressed_bytes=50), dict(max_units=2)])
def test_limits(limits):
    with pytest.raises(ValueError, match="[가-힣]"):
        analyze(make_docx(), "docx", **limits)


@pytest.mark.parametrize(("fmt", "data"), [
    ("docx", b"not an archive"), ("hwpx", archive({"Contents/section0.xml": "<bad"})),
    ("docx", archive({"word/document.xml": "<house/>"})),
    ("docx", archive({"word/document.xml": '<!DOCTYPE doc [<!ENTITY x "text">]><doc>&x;</doc>'})),
])
def test_damaged_or_unexpected_documents(fmt, data):
    with pytest.raises(ValueError, match="[가-힣]"):
        analyze(data, fmt)


def repack(data, transform):
    with ZipFile(io.BytesIO(data)) as z:
        parts = {n: z.read(n) for n in z.namelist()}
    transform(parts)
    return archive(parts)


def test_toc_includes_following_entries_until_body_heading():
    for fmt in ("docx", "hwpx"):
        def contents(parts):
            if fmt == "docx":
                path = "word/document.xml"
                p = '<w:p><w:r><w:t>차례</w:t></w:r></w:p><w:p><w:r><w:t>일정과 방법</w:t></w:r></w:p>'
                p += '<w:p><w:pPr><w:outlineLvl w:val="0"/></w:pPr><w:r><w:t>연구 본문</w:t></w:r></w:p>'
                parts[path] = parts[path].replace(b"<w:body>", ("<w:body>" + p).encode())
            else:
                path = "Contents/section0.xml"
                p = ''.join(f'<hp:p styleIDRef="{s}"><hp:run><hp:t>{t}</hp:t></hp:run></hp:p>'
                            for s, t in [("0", "차례"), ("0", "일정과 방법"), ("2", "연구 본문")])
                parts[path] = parts[path].replace(b"<hp:p ", p.encode() + b"<hp:p ", 1)
                parts["Contents/header.xml"] = parts["Contents/header.xml"].replace(
                    b"</hh:styles>", '<hh:style id="2" name="개요 1"/></hh:styles>'.encode())
        units = analyze(repack(make_docx() if fmt == "docx" else make_hwpx(), contents), fmt)["units"]
        assert [u["section"] for u in units if u["excerpt"] in ("차례", "일정과 방법", "연구 본문")] == ["목차", "목차", "본문"]


def test_docx_theme_and_numbering_override():
    def customize(parts):
        parts["word/styles.xml"] = parts["word/styles.xml"].replace(b'w:ascii="Example Roman"', b'w:asciiTheme="minorAscii"')
        parts["word/document.xml"] = parts["word/document.xml"].replace(b'w:sz w:val="28"',
            b'w:color w:themeColor="accent1"/><w:sz w:val="28"')
        parts["word/theme/theme1.xml"] = b'''<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
          <a:themeElements><a:clrScheme><a:accent1><a:srgbClr val="6699CC"/></a:accent1></a:clrScheme>
          <a:fontScheme><a:minorFont><a:latin typeface="NanumGothic"/></a:minorFont></a:fontScheme></a:themeElements></a:theme>'''
        path = "word/numbering.xml"
        parts[path] = parts[path].replace(b"</w:num>", b'''<w:lvlOverride w:ilvl="0"><w:lvl w:ilvl="0">
          <w:numFmt w:val="bullet"/><w:lvlText w:val="*"/><w:pPr><w:ind w:start="1440" w:hanging="720"/></w:pPr>
          </w:lvl></w:lvlOverride></w:num>''')
    _, body, item = analyze(repack(make_docx(), customize), "docx")["units"]
    assert "#6699CC" in [c["value"] for c in candidates(body, "body", "color")]
    assert ("nanum-gothic", "read") in [(c["value"], c["basis"]) for c in candidates(body, "body", "font")]
    assert candidates(item, "list", "levels.1.expr")[0]["value"] == "*"
    assert candidates(item, "list", "levels.1.indent_mm")[0]["value"] == 12.7


def test_only_active_word_runs_contribute_to_evidence():
    def customize(parts):
        path = "word/document.xml"
        parts[path] = parts[path].replace(b"</w:r></w:p>", b'''</w:r><w:r><w:drawing><w:txbxContent>
          <w:p><w:r><w:t>Box</w:t></w:r></w:p></w:txbxContent></w:drawing></w:r>
          <w:del><w:r><w:t>Deleted</w:t></w:r></w:del></w:p>''', 1)
    result = analyze(repack(make_docx(), customize), "docx")
    assert [u["excerpt"] for u in result["units"]] == ["표지 제목", "본문 기본강조 ABC", "목록 항목"]


def test_no_repeated_hwpx_spine_parts():
    def repeat(parts):
        path = "Contents/content.hpf"
        parts[path] = parts[path].replace(b'</opf:spine>', b'<opf:itemref idref="a"/></opf:spine>')
    with pytest.raises(ValueError, match="[가-힣]"):
        analyze(repack(make_hwpx(), repeat), "hwpx")


def test_preview_is_only_attached_to_first_unit():
    import base64
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC")
    def preview(parts):
        parts["Preview/PrvImage.png"] = png
    result = analyze(repack(make_hwpx(), preview), "hwpx", raster_width=128, max_pixels=1024)
    cover, *rest = result["units"]
    assert cover["preview"].startswith("data:image/")
    assert all("preview" not in u for u in rest)


def test_word_page_evidence_requires_consistent_saved_pagination():
    def cached(parts):
        path = "word/document.xml"
        parts[path] = parts[path].replace("<w:t>본문 기본".encode(), "<w:lastRenderedPageBreak/><w:t>본문 기본".encode())
        parts["docProps/app.xml"] = b'<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"><Pages>2</Pages></Properties>'
    data = repack(make_docx(), cached)
    units = analyze(data, "docx")["units"]
    assert ["1쪽" in units[0]["location"], "2쪽" in units[1]["location"]] == [True, True]
    stale = repack(data, lambda parts: parts.update({"docProps/app.xml": b"<Properties><Pages>8</Pages></Properties>"}))
    assert all("쪽" not in u["location"] for u in analyze(stale, "docx")["units"])


def test_relative_list_gaps_require_confirmation_and_parent_paths_are_unsupported():
    def different(parts):
        path = "Contents/header.xml"
        parts[path] = parts[path].replace(b'textOffsetType="HWPUNIT" textOffset="720"',
                                          b'textOffsetType="PERCENT" textOffset="50"').replace(b">^1.", b">^n")
    item = analyze(repack(make_hwpx(), different), "hwpx")["units"][-1]
    expr, = candidates(item, "list", "levels.1.expr")
    assert expr["basis"] == "unsupported"
    gap, = candidates(item, "list", "levels.1.gap_mm")
    assert gap["basis"] == "confirm" and "50%" in gap["evidence"]


def test_inherited_header_reference_stays_out_of_the_body_excerpt():
    def header(parts):
        path = "word/document.xml"
        ref = b'<w:headerReference w:type="default" r:id="rH"/>'
        parts[path] = parts[path].replace(ref, b"").replace(b"<w:sectPr>", b"<w:sectPr>"+ref, 1)
    _, body, _ = analyze(repack(make_docx(), header), "docx")["units"]
    candidate, = candidates(body, "header", "left")
    assert (candidate["value"], candidate["variant"], candidate["basis"]) == ("머리글 예시", "default", "read")
    assert "상속" in candidate["evidence"] and "머리글 예시" not in body["excerpt"]


def test_style_cycle_rejected():
    def cyclic(parts):
        path = "word/styles.xml"
        parts[path] = parts[path].replace(b'<w:basedOn w:val="Normal"/>', b'<w:basedOn w:val="Title"/>')
    with pytest.raises(ValueError, match="순환"):
        analyze(repack(make_docx(), cyclic), "docx")


def test_nested_list_marker_uses_parent_text_origin():
    def nested(parts):
        n = "word/numbering.xml"
        parts[n] = parts[n].replace(b"</w:abstractNum>", b'''<w:lvl w:ilvl="1"><w:numFmt w:val="ganada"/>
          <w:lvlText w:val="(%2)"/><w:pPr><w:ind w:left="1440" w:hanging="360"/></w:pPr></w:lvl></w:abstractNum>''')
        p = "word/document.xml"
        child = '<w:p><w:pPr><w:numPr><w:ilvl w:val="1"/><w:numId w:val="7"/></w:numPr></w:pPr><w:r><w:t>아래 항목</w:t></w:r></w:p>'
        parts[p] = parts[p].replace(b'</w:p>\n          <w:sectPr>', ('</w:p>' + child + '<w:sectPr>').encode())
    child = analyze(repack(make_docx(), nested), "docx")["units"][-1]
    assert candidates(child, "list", "levels.2.indent_mm")[0]["value"] == 6.35
    assert candidates(child, "list", "levels.2.expr")[0]["value"] == "({한글})"


def test_complex_script_font_and_size_are_not_taken_from_latin_run_defaults():
    def different(parts):
        path = "word/document.xml"
        p = '<w:p><w:r><w:rPr><w:rFonts w:cs="Arabic example"/><w:szCs w:val="32"/></w:rPr><w:t>سلام</w:t></w:r></w:p>'
        parts[path] = parts[path].replace(b"<w:body>", ("<w:body>" + p).encode())
    unit = analyze(repack(make_docx(), different), "docx")["units"][0]
    assert [(c["value"], c["basis"]) for c in candidates(unit, "body", "font")] == [("Arabic example", "confirm")]
    assert [c["value"] for c in candidates(unit, "body", "font_pt")] == [16]


def test_word_character_unit_indent_is_not_reported_as_exact_twips():
    def char_indent(parts):
        p = "word/document.xml"
        parts[p] = parts[p].replace(b'<w:spacing w:line="340"', b'<w:ind w:start="720" w:startChars="100"/><w:spacing w:line="340"')
    unit = analyze(repack(make_docx(), char_indent), "docx")["units"][1]
    assert candidates(unit, "body", "rest_indent_mm")[0]["basis"] == "confirm"


@pytest.mark.parametrize("fmt", ["docx", "hwpx"])
def test_bounds_before_accumulating_different_runs(fmt, monkeypatch):
    from app import reference_office as office
    if fmt == "docx":
        def many(parts):
            runs = ''.join(f'<w:r><w:rPr><w:sz w:val="{n}"/></w:rPr><w:t>본문 글자</w:t></w:r>' for n in range(1, 401))
            parts["word/document.xml"] = f'<w:document xmlns:w="{W}"><w:body><w:p>{runs}</w:p></w:body></w:document>'.encode()
        data = repack(make_docx(), many)
        fn = "_word_chars"
    else:
        def many(parts):
            path = "Contents/header.xml"
            chars = ''.join(f'<hh:charPr id="{n}" height="{10*n}"/>' for n in range(11, 411))
            parts[path] = parts[path].replace(b'</hh:charProperties>', (chars + '</hh:charProperties>').encode())
            path = "Contents/section0.xml"
            runs = ''.join(f'<hp:run charPrIDRef="{n}"><hp:t>본문 글자</hp:t></hp:run>' for n in range(11, 411))
            parts[path] = (f'<hs:sec xmlns:hs="{H}section" xmlns:hp="{H}paragraph"><hp:p>{runs}</hp:p></hs:sec>').encode()
        data = repack(make_hwpx(), many)
        fn = "_hwp_chars"
    called = []
    original = getattr(office, fn)
    def count(*args):
        called.append(1)
        return original(*args)
    monkeypatch.setattr(office, fn, count)
    result = analyze(data, fmt, max_candidates_per_unit=6, max_evidence_chars=20, max_excerpt_chars=12)
    unit = result["units"][-1]
    assert len(unit["candidates"]) == 6 and len(unit["excerpt"]) == 12
    assert all(0 < len(c["evidence"]) <= 20 for c in unit["candidates"])
    assert 0 < len(called) < 12
    assert any("후보" in warning for warning in result["warnings"])
    assert any("발췌" in warning for warning in result["warnings"])
