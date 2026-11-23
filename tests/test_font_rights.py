"""Run identical, independently specified rights cases in Chromium and the render worker."""
import base64
import hashlib
import io
import json
import os
import struct
from pathlib import Path

import pytest
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.pens.t2CharStringPen import T2CharStringPen
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app/static"
LOCK = json.loads((ROOT / "runtime.lock").read_text())
OFL = "This Font Software is licensed under the SIL Open Font License, Version 1.1."
URL = "https://openfontlicense.org/"
HOLDER = "Copyright © 2026 가람, with Reserved Font Name Paper."


def font(flags=0, *, license="", url="", copyright=HOLDER, version=4, extra=(), cff=False, weight=700):
    fb = FontBuilder(1000, isTTF=not cff)
    glyphs = [".notdef", "A"]
    fb.setupGlyphOrder(glyphs)
    fb.setupCharacterMap({65: "A"})
    if cff:
        pen = T2CharStringPen(500, None)
        fb.setupCFF("Paper", {"FullName": "Paper Bold"}, {g: pen.getCharString() for g in glyphs}, {})
    else:
        pen = TTGlyphPen(None)
        pen.moveTo((0, 0))
        pen.lineTo((200, 300))
        pen.lineTo((400, 0))
        pen.closePath()
        fb.setupGlyf({g: pen.glyph() for g in glyphs})
    fb.setupHorizontalMetrics({g: (500, 0) for g in glyphs})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"fullName": "Paper Bold", "version": "Version 2.1"}, mac=False)
    names = fb.font["name"]
    for name_id, string in [(13, license), (14, url), (0, copyright)]:
        if string:
            names.setName(string, name_id, 3, 1, 0x409)
    for item in extra:
        names.setName(*item)
    fb.setupOS2(version=version, fsType=flags, usWeightClass=weight)
    fb.setupPost()
    out = io.BytesIO()
    fb.save(out)
    return out.getvalue()


@pytest.fixture(scope="module")
def page():
    with sync_playwright() as pw, pw.chromium.launch(channel=os.getenv("JJOKGYEOL_CHROMIUM_CHANNEL")) as browser:
        tab = browser.new_page()
        unexpected = []

        def serve(route):
            path = route.request.url
            if path == "http://localhost/":
                route.fulfill(content_type="text/html; charset=utf-8", body='<script src="/static/font-rights.js"></script>')
            elif path in ("http://localhost/static/font-rights.js", "http://localhost/static/ofl-1.1.txt"):
                asset = STATIC / path.rsplit("/", 1)[1]
                if asset.exists():
                    route.fulfill(path=asset)
                else:
                    route.fulfill(status=404)
            else:
                unexpected.append(path)
                route.abort()
        tab.route("**/*", serve)
        tab.goto("http://localhost/")
        yield tab
        assert not unexpected


def both(page, data, known=None):
    assert page.evaluate("typeof globalThis.FontRights?.inspect") == "function"
    from app.font_rights import inspect_face

    js = page.evaluate("""async ([data, known]) => {
      const bytes = Uint8Array.from(atob(data), c => c.charCodeAt(0));
      return FontRights.inspect(new Blob([bytes]), known);
    }""", [base64.b64encode(data).decode(), known or {}])
    assert js == inspect_face(data)
    assert js["hash"] == hashlib.sha256(data).hexdigest()
    assert js["reason"]
    for use in ("pdf", "archive", "remote"):
        assert js[use] is (js["status"][use] == "allowed")
    return js


@pytest.mark.parametrize("flags,options,pdf,archive", [
    (0, {}, "unknown", "unknown"),
    (8, {}, "allowed", "unknown"),
    (4, {}, "restricted", "restricted"),
    (2, {}, "restricted", "restricted"),
    (0x108, {}, "restricted", "unknown"),
    (0x208, {}, "restricted", "restricted"),
    (0x108, {"version": 1}, "allowed", "unknown"),
    (0x208, {"version": 0}, "allowed", "unknown"),
    (12, {"version": 2}, "allowed", "unknown"),
    (12, {"version": 3}, "unknown", "unknown"),
    (0x80 | 8, {}, "unknown", "unknown"),
    (0, {"license": OFL, "url": URL}, "allowed", "allowed"),
    (8, {"license": "OFL-1.1", "cff": True}, "allowed", "allowed"),
    (0, {"license": OFL, "copyright": ""}, "allowed", "unknown"),
    (0, {"license": OFL + " This license is available with a FAQ at: https://openfontlicense.org"},
     "allowed", "allowed"),
    (8, {"license": "See separate EULA."}, "unknown", "unknown"),
    (8, {"url": "https://example.com/eula"}, "unknown", "unknown"),
    (0, {"url": URL}, "unknown", "unknown"),
    (0, {"license": "SPDX-License-Identifier: OFL-1.1"}, "allowed", "allowed"),
    (0, {"license": "Licensed under the SIL Open Font License, v1.1."}, "allowed", "allowed"),
    (0, {"license": OFL, "copyright": "Copyright (c) <dates>, <Copyright Holder>."}, "allowed", "unknown"),
    (0, {"license": OFL, "copyright": HOLDER + " For personal use only."}, "unknown", "unknown"),
    (0, {"license": OFL, "extra": [("재배포 금지", 0, 3, 1, 0x412)]}, "unknown", "unknown"),
    (0, {"license": OFL, "url": "https://openfontlicense.org.example/eula"}, "unknown", "unknown"),
    (8, {"license": "Not licensed under the SIL Open Font License, Version 1.1."}, "unknown", "unknown"),
    (8, {"license": OFL + " Personal use only."}, "unknown", "unknown"),
    (0, {"license": OFL, "extra": [("별도 약관 적용", 13, 3, 1, 0x412)]}, "unknown", "unknown"),
    (0, {"license": OFL, "extra": [("https://example.org/terms", 14, 3, 1, 0x412)]}, "unknown", "unknown"),
    (0, {"license": OFL, "extra": [(b"\xff\xfe", 13, 1, 7, 0)]}, "unknown", "unknown"),
    (2, {"license": OFL}, "unknown", "unknown"),
    (4, {"license": OFL}, "unknown", "unknown"),
    (0x100, {"license": OFL}, "restricted", "allowed"),
])
def test_permissions(page, flags, options, pdf, archive):
    face = both(page, font(flags, **options))
    assert face["status"] == {"pdf": pdf, "archive": archive, "remote": archive}
    assert face["fsType"] == flags


def test_external_notice_is_reproducible_and_preserves_all_attributions(page):
    second = "Copyright (c) 2024 Second Studio."
    data = font(license=OFL, url=URL, extra=[
        (second, 0, 3, 1, 0x412), ("종이 굵게", 4, 3, 1, 0x412),
        (HOLDER, 0, 0, 4, 0), (OFL, 13, 0, 4, 0),
    ])
    face = both(page, data)
    assert face["name"] == "종이 굵게" and face["version"] == "Version 2.1" and face["weight"] == "bold"
    assert face["license"] == OFL and face["url"] == URL
    assert set(face["copyright"].splitlines()) == {HOLDER, second}
    notices = page.evaluate("""async face => Promise.all([1,2].map(async () => {
        const blob = await FontRights.notice(face);
        return blob instanceof Blob && await blob.text();
    }))""", face)
    expected = face["copyright"] + "\n\n" + (STATIC / "ofl-1.1.txt").read_text()
    assert notices == [expected, expected]
    assert expected.count(HOLDER) == expected.count(second) == 1
    assert "PERMISSION & CONDITIONS" in expected and "DISCLAIMER" in expected
    assert "<Copyright Holder>" not in expected


def test_unsafe_url_never_becomes_a_link(page):
    face = both(page, font(8, license=OFL, url="javascript:alert(1)"))
    assert not face["url"] and not face["pdf"] and not face["archive"]


def broken_faces():
    valid = font(license=OFL)
    tags = {valid[i:i+4]: i for i in range(12, 12 + 16 * struct.unpack_from(">H", valid, 4)[0], 16)}
    def change(offset, value):
        return valid[:offset] + value + valid[offset + len(value):]
    name_offset = struct.unpack_from(">I", valid, tags[b"name"] + 8)[0]
    return [
        b"", b"ttcf" + valid[4:], b"wOF2" + valid[4:], valid[:25],
        change(tags[b"post"], b"fvar"), change(tags[b"glyf"], b"junk"),
        change(tags[b"post"], b"name"), change(tags[b"name"] + 8, b"\xff" * 4),
        change(tags[b"post"] + 8, valid[tags[b"name"] + 8:tags[b"name"] + 12]),
        change(name_offset + 4, b"\x00\x01"), change(name_offset + 6 + 10, b"\xff\xff"),
        change(tags[b"OS/2"] + 12, b"\x00\x00\x00\x04"),
    ]


def test_damaged_and_unsupported_faces_cannot_be_used(page):
    for data in broken_faces():
        face = both(page, data)
        assert not any(face[use] for use in ("pdf", "archive", "remote"))


@pytest.mark.parametrize("family,weight", [(family, weight) for family in LOCK["fonts"] for weight in ("regular", "bold")])
def test_exact_locked_face_uses_pinned_notice(page, family, weight):
    record = LOCK["fonts"][family][weight]
    path = Path(record["path"])
    if not path.is_file():
        pytest.skip("requires fonts from runtime.lock")
    known = {"faces": {record["sha256"]: {"family": family, "weight": weight, "name": path.stem,
                                         "version": record["version"]}}, "notice": LOCK["font_license"]["sha256"]}
    face = both(page, path.read_bytes(), known)
    assert face["known"] and face["archive"] and face["remote"] and face["pdf"]
    assert face["version"] == record["version"] and face["weight"] == weight
    changed = both(page, path.read_bytes() + b"\x00", known)
    assert not changed["known"] and not changed["archive"]


def test_family_of_regular_and_bold_faces_is_the_same(page):
    for weight, full in [(400, "Paper Regular"), (700, "Paper Bold")]:
        names = [(full, 4, 3, 1, 0x409), ("Paper Classic", 1, 3, 1, 0x409), ("Paper", 16, 3, 1, 0x409)]
        face = both(page, font(8, weight=weight, extra=names))
        assert (face["name"], face.get("family"), face["weight"]) == (full, "Paper", "bold" if weight == 700 else "regular")
    assert both(page, font(extra=[("Paper Classic", 1, 3, 1, 0x409)]))["family"] == "Paper Classic"
    assert both(page, font())["family"] == "Paper Bold"


@pytest.mark.parametrize("variant,allowed", [
    ("canonical", True), ("wrapped", True), ("with-header", True),
    ("modified", False), ("additional-condition", False),
])
def test_full_ofl_text_in_license_record(page, variant, allowed):
    body = (STATIC / "ofl-1.1.txt").read_text()
    if variant == "wrapped":
        body = " ".join(body.split()).replace("-" * 59, "")
    if variant == "with-header":
        body = HOLDER + "\n" + OFL + "\n" + "-" * 59 + "\n" + body
    if variant == "modified":
        body = body.replace("Permission is hereby granted", "Permission is hereby denied")
    if variant == "additional-condition":
        body += "No commercial use."
    face = both(page, font(8, license=body, url="https://openfontlicense.org/open-font-license-official-text/"))
    assert face["pdf"] is face["archive"] is face["remote"] is allowed


@pytest.mark.parametrize("url", [
    "https://opensource.org/license/OFL-1.1",
    "http://scripts.sil.org/cms/scripts/page.php?item_id=OFL_web",
    "http://dev.naver.com/wiki/nanumfont/index.php/OpenFontLicense",
])
def test_equivalent_ofl_links_still_require_an_explicit_license(page, url):
    assert both(page, font(license=OFL, url=url))["archive"]
    assert not both(page, font(url=url))["archive"]


def test_external_nanum_gothic_coding(page):
    path = Path(LOCK["fonts"]["nanum-gothic"]["regular"]["path"]).with_name("NanumGothicCoding.ttf")
    if not path.is_file():
        pytest.skip("requires NanumGothicCoding in the runtime image")
    face = both(page, path.read_bytes())
    assert face["pdf"] and face["archive"] and face["remote"] and not face["known"]


def test_notice_rejects_an_incomplete_bundled_license(page):
    face = both(page, font(license=OFL))
    page.route("**/ofl-1.1.txt", lambda route: route.fulfill(body="Incomplete license", status=200))
    try:
        assert page.evaluate("""async face => {
            try { await FontRights.notice(face); return false; } catch { return true; }
        }""", face)
    finally:
        page.unroute("**/ofl-1.1.txt")


def test_local_inspection_without_webcrypto(page):
    # A LAN editor can read a user-selected file even when localFonts and SubtleCrypto are unavailable.
    page.route("**/sha256.min.js", lambda route: route.fulfill(path=STATIC / "sha256.min.js", content_type="text/javascript"))
    page.evaluate("Object.defineProperty(crypto, 'subtle', {value: undefined, configurable: true})")
    try:
        face = both(page, font(license=OFL))
        assert face["archive"] and face["pdf"]
    finally:
        page.evaluate("delete crypto.subtle")
        page.unroute("**/sha256.min.js")
