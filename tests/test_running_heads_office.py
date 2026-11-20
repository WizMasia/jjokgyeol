"""Self-authored header and footer parts for the Office reference reader."""

import pytest

from test_reference_office import R, W, analyze, make_docx, make_hwpx, repack


def running_docx():
    def parts(doc):
        def paragraph(contents, align):
            return f'<w:p><w:pPr><w:jc w:val="{align}"/></w:pPr>{contents}</w:p>'

        def text(value):
            return f'<w:r><w:rPr><w:sz w:val="20"/><w:color w:val="123456"/></w:rPr><w:t>{value}</w:t></w:r>'

        complex_field = ('<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
                         '<w:r><w:instrText> PA</w:instrText></w:r><w:r><w:instrText>GE </w:instrText></w:r>'
                         '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
                         + text("17") + '<w:r><w:fldChar w:fldCharType="end"/></w:r>')
        simple = '<w:fldSimple w:instr=" PAGE ">' + text("17") + '</w:fldSimple>'
        for name, tag, contents, slot in [
                ("header1", "hdr", text("2026 보고서 · ") + complex_field, "center"),
                ("header2", "hdr", text("2026 보고서 · ") + simple, "center"),
                ("header3", "hdr", text("원문 {현재쪽} 2026"), "right"),
                ("footer1", "ftr", text("－ ") + simple + text(" －"), "center")]:
            doc[f"word/{name}.xml"] = (f'<w:{tag} xmlns:w="{W}">' +
                                       paragraph(contents, slot) + f'</w:{tag}>').encode()
        rels = "word/_rels/document.xml.rels"
        doc[rels] = doc[rels].replace(b'</Relationships>', (f'<Relationship Id="rFirst" Type="{R}/header" Target="header2.xml"/>'
            f'<Relationship Id="rEven" Type="{R}/header" Target="header3.xml"/>'
            f'<Relationship Id="rFoot" Type="{R}/footer" Target="footer1.xml"/></Relationships>').encode())
        p = "word/document.xml"
        refs = ('<w:headerReference w:type="default" r:id="rH"/><w:headerReference w:type="first" r:id="rFirst"/>'
                '<w:headerReference w:type="even" r:id="rEven"/><w:footerReference w:type="default" r:id="rFoot"/>'
                '<w:pgMar w:header="720" w:footer="360"/>').encode()
        doc[p] = doc[p].replace(b'<w:headerReference w:type="default" r:id="rH"/>', b'').replace(
            b"<w:sectPr>", b"<w:sectPr>" + refs, 1)
        doc[p] = doc[p].replace(b'<w:pgMar w:top=', b'<w:pgMar w:header="360" w:footer="360" w:top=')
    return repack(make_docx(), parts)


def running_hwpx():
    def parts(doc):
        section = "Contents/section1.xml"
        doc[section] = doc[section].replace(b'<hp:t>', '''<hp:ctrl><hp:header id="12" applyPageType="ODD">
          <hp:subList><hp:p styleIDRef="0" paraPrIDRef="8"><hp:run charPrIDRef="2"><hp:t>2026 보고서 · </hp:t>
            <hp:ctrl><hp:autoNum num="17" numType="PAGE"><hp:autoNumFormat type="DIGIT"/></hp:autoNum></hp:ctrl>
          </hp:run></hp:p></hp:subList></hp:header></hp:ctrl>
          <hp:ctrl><hp:footer applyPageType="EVEN"><hp:subList><hp:p styleIDRef="0">
          <hp:run charPrIDRef="0"><hp:t>원문 {현재쪽}</hp:t></hp:run></hp:p>
          </hp:subList></hp:footer></hp:ctrl><hp:ctrl><hp:pageNum pos="BOTTOM_CENTER" formatType="DIGIT" sideChar="-"/></hp:ctrl>
          <hp:t>'''.encode(), 1).replace(b'<hp:margin top=', b'<hp:margin header="3600" footer="1800" top=')
        header = "Contents/header.xml"
        doc[header] = doc[header].replace(b"</hh:paraProperties>", b'<hh:paraPr id="8"><hh:align horizontal="RIGHT"/></hh:paraPr></hh:paraProperties>')
    return repack(make_hwpx(), parts)


def area(unit, role, key):
    return [c for c in unit["candidates"] if (c["role"], c["key"]) == (role, key)]


def test_docx_page_fields_use_their_instruction_and_keep_variants_separate():
    data = running_docx()
    result = analyze(data, "docx")
    assert result == analyze(data, "docx")
    cover, body, _ = result["units"]
    for u in [cover, body]:
        same = area(u, "header", "center")
        assert sorted((c["variant"], c["value"], c["basis"]) for c in same) == [
            ("default", "2026 보고서 · {현재쪽}", "read"), ("first", "2026 보고서 · {현재쪽}", "confirm")]
        assert len({c["source_group"] for c in same}) == 2
        literal, = area(u, "header", "right")
        assert (literal["variant"], literal["value"]) == ("even", "원문 {{현재쪽}} 2026")
        assert area(u, "footer", "center")[0]["value"] == "－ {현재쪽} －"
        assert "보고서" not in u["excerpt"] and all("options" not in c for c in u["candidates"])
    # A section inherits the same part but has its own location and offsets.
    assert area(cover, "header", "center")[0]["source_group"] != area(body, "header", "center")[0]["source_group"]
    for u, offset in [(cover, 12.7), (body, 6.35)]:
        group = next(c["source_group"] for c in area(u, "header", "center") if c["variant"] == "default")
        attrs = {c["key"]: c["value"] for c in u["candidates"] if c.get("source_group") == group}
        assert attrs["font_pt"] == 10 and attrs["color"] == "#123456"
        assert attrs["font"] == "nanum-myeongjo" and attrs["offset_mm"] == offset


def test_hwpx_sublist_and_independent_page_number_position():
    cover = analyze(running_hwpx(), "hwpx")["units"][0]
    assert cover["excerpt"] == "표지 제목"
    header, = area(cover, "header", "right")
    assert (header["value"], header["variant"], header["basis"]) == ("2026 보고서 · {현재쪽}", "odd", "read")
    group = {c["key"]: c["value"] for c in cover["candidates"] if c.get("source_group") == header["source_group"]}
    assert (group["font_pt"], group["font"], group["color"], group["offset_mm"]) == (24,"nanum-myeongjo","#125634",12.7)
    footer, = area(cover, "footer", "left")
    assert (footer["value"],footer["variant"]) == ("원문 {{현재쪽}}", "even")
    position, = area(cover, "footer", "center")
    assert position["value"].replace(" ", "") == "-{현재쪽}-"
    assert position["source_group"] != footer["source_group"]


def test_complex_content_requires_review_and_cached_total_is_not_a_page_field():
    def complex_part(doc):
        doc["word/header1.xml"] = (f'<w:hdr xmlns:w="{W}"><w:tbl><w:tr><w:tc><w:p><w:r><w:t>합계 </w:t>'
            '<w:br/></w:r><w:fldSimple w:instr=" NUMPAGES "><w:r><w:t>32</w:t></w:r></w:fldSimple>'
            '</w:p></w:tc></w:tr></w:tbl></w:hdr>').encode()
    u = analyze(repack(make_docx(), complex_part), "docx")["units"][-1]
    line, = area(u, "header", "left")
    assert line["basis"] == "confirm" and "합계" in line["value"] and "{현재쪽}" not in line["value"]
    assert "NUMPAGES" in line["evidence"] or "전체 쪽" in line["evidence"]


@pytest.mark.parametrize("fmt", ["docx", "hwpx"])
def test_running_part_paragraphs_respect_the_analysis_limit(fmt):
    def add_paragraphs(parts):
        if fmt == "docx":
            path = "word/header1.xml"
            parts[path] = parts[path].replace(b"</w:hdr>", b"<w:p/>" * 4 + b"</w:hdr>")
        else:
            path = "Contents/section1.xml"
            parts[path] = parts[path].replace(b"</hp:subList>", b"<hp:p/>" * 4 + b"</hp:subList>", 1)

    sample = running_docx() if fmt == "docx" else running_hwpx()
    with pytest.raises(ValueError, match="문단.*한도"):
        analyze(repack(sample, add_paragraphs), fmt, max_units=4)


def test_hwpx_mirrored_page_position_and_unusable_position():
    def positions(parts):
        path = "Contents/section1.xml"
        parts[path] = parts[path].replace(b'pos="BOTTOM_CENTER"', b'pos="OUTSIDE_BOTTOM"').replace(
            b'</hp:secPr>', b'</hp:secPr><hp:ctrl><hp:pageNum pos="TOP_BOTTOM"/></hp:ctrl>')
    result = analyze(repack(running_hwpx(), positions), "hwpx")
    cover = result["units"][0]
    for variant, slot in [("even", "left"), ("odd", "right")]:
        assert any(c["variant"] == variant and "{현재쪽}" in c["value"] for c in area(cover, "footer", slot))
    assert any("위치" in warning for warning in result["warnings"])


@pytest.mark.parametrize("fmt", ["docx", "hwpx"])
def test_empty_cover_keeps_its_own_running_candidates(fmt):
    def empty_cover(parts):
        if fmt == "docx":
            path = "word/document.xml"
            # Move the header reference from the second section to the empty first one.
            ref = b'<w:headerReference w:type="default" r:id="rH"/>'
            parts[path] = parts[path].replace(ref, b"").replace(b"<w:sectPr>", b"<w:sectPr>" + ref, 1)
            # The following section explicitly replaces the cover's header.
            parts[path] = parts[path].replace(b"<w:sectPr><w:pgSz", b"<w:sectPr>" + ref.replace(b'rH"', b'rBody"') + b"<w:pgSz")
            rels = "word/_rels/document.xml.rels"
            parts[rels] = parts[rels].replace(b"</Relationships>", (
                f'<Relationship Id="rBody" Type="{R}/header" Target="header2.xml"/></Relationships>').encode())
            parts["word/header2.xml"] = parts["word/header1.xml"].replace("머리글 예시".encode(), "본문 머리글".encode())
        else:
            path = "Contents/section1.xml"
        parts[path] = parts[path].replace("표지 제목".encode(), b"  ")

    sample = make_docx() if fmt == "docx" else running_hwpx()
    units = analyze(repack(sample, empty_cover), fmt)["units"]
    assert [u["excerpt"] for u in units] == ["", "본문 기본강조 ABC", "목록 항목"]
    cover, *body = units
    assert cover["section"] == "표지" and "구역 1" in cover["location"]
    slot, text = ("left", "머리글 예시") if fmt == "docx" else ("right", "2026 보고서 · {현재쪽}")
    candidate, = area(cover, "header", slot)
    assert candidate["value"] == text
    assert all(c["value"] != text for u in body for c in area(u, "header", slot))


def test_docx_running_text_preserves_nonbreaking_hyphen():
    def hyphen(parts):
        path = "word/header1.xml"
        parts[path] = parts[path].replace("머리글 예시".encode(), "기관</w:t><w:noBreakHyphen/><w:t>서울".encode())

    _, body, _ = analyze(repack(make_docx(), hyphen), "docx")["units"]
    candidate, = area(body, "header", "left")
    assert candidate["value"] == "기관\u2011서울"
