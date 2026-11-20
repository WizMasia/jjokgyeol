"""Office face recommendations use package settings and per-section references."""

import httpx
import pytest

from test_reference_office import R, W, analyze, make_docx, repack
from test_running_heads_office import running_hwpx


def duplex_docx(parity="<w:evenAndOddHeaders/>", *, next_title='<w:titlePg w:val="0"/>',
                first_header="<w:r><w:t>표지 전용</w:t></w:r>", first_footer=True):
    def change(parts):
        refs = [('header', 'default', 'rDefault', 'odd.xml'), ('header', 'first', 'rFirst', 'first.xml'),
                ('header', 'even', 'rEven', 'even.xml')]
        if first_footer:
            refs.append(('footer', 'first', 'rFoot', 'f-first.xml'))
        rels = 'word/_rels/document.xml.rels'
        entries = ''.join(f'<Relationship Id="{rid}" Type="{R}/{role}" Target="{name}"/>'
                          for role, _, rid, name in refs)
        entries += f'<Relationship Id="rSettings" Type="{R}/settings" Target="cfg/duplex.xml"/>'
        parts[rels] = parts[rels].replace(b'</Relationships>', (entries + '</Relationships>').encode())
        parts['word/cfg/duplex.xml'] = f'<w:settings xmlns:w="{W}">{parity}</w:settings>'.encode()
        for role, variant, _, name in refs:
            tag = "hdr" if role == "header" else "ftr"
            if role == "footer":
                contents = "<w:pPr/><w:r><w:t>   </w:t></w:r>"
            elif variant == "first":
                contents = first_header
            else:
                contents = f"<w:r><w:t>{variant}</w:t></w:r>"
            parts['word/' + name] = f'<w:{tag} xmlns:w="{W}"><w:p>{contents}</w:p></w:{tag}>'.encode()
        refs_xml = ''.join(f'<w:{role}Reference w:type="{variant}" r:id="{rid}"/>' for role, variant, rid, _ in refs)
        parts['word/document.xml'] = (f'<w:document xmlns:w="{W}" xmlns:r="{R}"><w:body>'
            f'<w:p><w:pPr><w:sectPr>{refs_xml}<w:titlePg/></w:sectPr></w:pPr></w:p>'
            f'<w:p><w:r><w:t>본문</w:t></w:r></w:p><w:sectPr>{next_title}</w:sectPr>'
            '</w:body></w:document>').encode()
    return repack(make_docx(), change)


def outside_hwpx():
    def change(parts):
        path = "Contents/section1.xml"
        parts[path] = parts[path].replace(b"BOTTOM_CENTER", b"OUTSIDE_BOTTOM")
    return repack(running_hwpx(), change)


def candidates(unit, role, variant):
    return [c for c in unit["candidates"] if c["role"] == role and c["variant"] == variant]


def test_docx_settings_and_section_activation_are_independent_of_inherited_parts():
    cover, body = analyze(duplex_docx(), "docx")["units"]
    assert cover["excerpt"] == ""
    for unit in (cover, body):
        for variant, face in (("default", "odd"), ("even", "even")):
            group = candidates(unit, "header", variant)
            assert group and {c["suggested_face"] for c in group} == {face}
    first, = (c for c in candidates(cover, "header", "first") if c["key"] == "left")
    inherited, = (c for c in candidates(body, "header", "first") if c["key"] == "left")
    assert (first["value"], first["suggested_face"]) == ("표지 전용", "first")
    assert (inherited["value"], inherited["suggested_face"], inherited["basis"]) == ("표지 전용", None, "confirm")
    assert "비활성" in inherited["evidence"] and "상속" in inherited["evidence"]
    assert first["source_group"] != inherited["source_group"]
    assert all("suggested_face" not in c for c in body["candidates"] if c["role"] == "body")


@pytest.mark.parametrize("parity", ["", '<w:evenAndOddHeaders w:val="off"/>'])
def test_docx_disabled_parity_keeps_even_source_for_manual_selection(parity):
    _, body = analyze(duplex_docx(parity, next_title=""), "docx")["units"]
    assert {c["suggested_face"] for c in candidates(body, "header", "default")} == {"default"}
    for variant in ("even", "first"):
        group = candidates(body, "header", variant)
        assert group and all(c["suggested_face"] is None and c["basis"] == "confirm" and
                             "비활성" in c["evidence"] for c in group)
    assert not candidates(body, "footer", "even")


@pytest.mark.parametrize("explicit", [False, True])
def test_docx_hides_only_empty_active_faces_after_resolving_inheritance(explicit):
    units = analyze(duplex_docx(first_footer=explicit, next_title="<w:titlePg/>"), "docx")["units"]
    for unit in units:
        for variant in ("first", "even"):
            hide, = candidates(unit, "footer", variant)
            assert (hide["key"], hide["value"], hide["suggested_face"], hide["basis"]) == (
                "visible", False, variant, "read")
        group = candidates(unit, "header", "first")
        assert any(c["value"] == "표지 전용" and c["suggested_face"] == "first" for c in group)
        assert all(c["key"] != "visible" for c in group)
    if explicit:
        assert "상속" in candidates(units[1], "footer", "first")[0]["evidence"]


@pytest.mark.parametrize("failure", ["picture", "missing", "wrong_root", "malformed", "symbol"])
def test_unreadable_or_nontext_header_never_recommends_hiding(failure):
    def change(parts):
        path = "word/first.xml"
        if failure == "missing":
            del parts[path]
        elif failure == "wrong_root":
            parts[path] = b"<unrelated/>"
        elif failure == "malformed":
            parts[path] = b"<broken"
    content = "<w:r><w:drawing/></w:r>" if failure == "picture" else '<w:r><w:sym w:char="0041"/></w:r>'
    data = repack(duplex_docx(first_header=content, next_title="<w:titlePg/>"), change)
    for unit in analyze(data, "docx")["units"]:
        group = candidates(unit, "header", "first")
        assert group and all(c["basis"] == "confirm" and c["suggested_face"] is None for c in group)
        assert all(c["key"] != "visible" for c in group)
        assert any("확인" in c["evidence"] for c in group)


def test_hwpx_maps_outside_number_and_explicit_header_faces():
    cover = analyze(outside_hwpx(), "hwpx")["units"][0]
    numbers = [c for c in cover["candidates"] if c["role"] == "footer" and "{현재쪽}" in str(c["value"])
               and c["value"].startswith("-")]
    assert {(c["variant"], c["suggested_face"], c["key"]) for c in numbers} == {
        ("odd", "odd", "right"), ("even", "even", "left")}
    assert {c["suggested_face"] for c in candidates(cover, "header", "odd")} == {"odd"}


def test_broken_settings_leave_parity_for_confirmation_but_keep_first_page():
    def change(parts):
        del parts["word/cfg/duplex.xml"]
    result = analyze(repack(duplex_docx(), change), "docx")
    cover = result["units"][0]
    for variant in ("default", "even"):
        assert all(c["suggested_face"] is None and c["basis"] == "confirm"
                   for c in candidates(cover, "header", variant))
    assert {c["suggested_face"] for c in candidates(cover, "header", "first")} == {"first"}
    assert not candidates(cover, "footer", "even")
    assert result["warnings"]


@pytest.mark.parametrize(("decoration", "clear"), [
    ('<w:pBdr><w:bottom w:val="single" w:color="008844"/></w:pBdr>',
     '<w:pBdr><w:bottom w:val="nil"/></w:pBdr>'),
    ('<w:shd w:val="clear" w:fill="008844"/>', '<w:shd w:val="nil"/>'),
])
def test_inherited_paragraph_decoration_requires_review_until_cleared(decoration, clear):
    def document(override):
        def change(parts):
            path = "word/styles.xml"
            styles = (f'<w:style w:type="paragraph" w:styleId="Paint"><w:pPr>{decoration}</w:pPr></w:style>'
                      '<w:style w:type="paragraph" w:styleId="Leaf"><w:basedOn w:val="Paint"/></w:style>')
            parts[path] = parts[path].replace(b"</w:styles>", styles.encode() + b"</w:styles>")
        properties = f'<w:pPr><w:pStyle w:val="Leaf"/>{override}</w:pPr>'
        return repack(duplex_docx(first_header=properties), change)

    decorated = candidates(analyze(document(""), "docx")["units"][0], "header", "first")
    assert decorated and all(c["key"] != "visible" and c["basis"] == "confirm"
                             and c["suggested_face"] is None for c in decorated)
    assert any("테두리" in c["evidence"] or "음영" in c["evidence"] for c in decorated)
    blank, = candidates(analyze(document(clear), "docx")["units"][0], "header", "first")
    assert (blank["key"], blank["value"], blank["suggested_face"]) == ("visible", False, "first")


@pytest.mark.anyio
@pytest.mark.parametrize("fmt", ["docx", "hwpx"])
async def test_duplex_office_analysis_api(fmt, anyio_backend):
    from app.server import app
    data = duplex_docx() if fmt == "docx" else outside_hwpx()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://local") as client:
        response = await client.post("/api/reference/analyze", params={"name": "proof." + fmt}, content=data)
    assert response.status_code == 200
    cover = response.json()["units"][0]
    assert all("suggested_face" in c for c in cover["candidates"] if c["role"] in ("header", "footer"))
    if fmt == "docx":
        assert any(c["key"] == "visible" and c["value"] is False and c["suggested_face"] == "first"
                   for c in candidates(cover, "footer", "first"))
    else:
        assert any(c["key"] == "right" and c["suggested_face"] == "odd" for c in candidates(cover, "footer", "odd"))
