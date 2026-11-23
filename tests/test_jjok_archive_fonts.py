"""Interoperate with a foreign ZIP producer and recheck real SFNT bytes under the local rights policy."""
import hashlib
import io
import json
from zipfile import ZipFile

import pytest
from playwright.sync_api import Error as BrowserError

from test_font_rights import HOLDER, OFL as LICENSE, font
from test_jjok_archive import LIMITS, STATIC, archive, browser, codec, entries, manifest, pack, pdf, unpack

pytestmark = pytest.mark.parametrize("codec", ["fonts"], indirect=True)


def digest(data):
    return hashlib.sha256(data).hexdigest()


OFL = HOLDER.encode() + b"\n\n" + (STATIC / "ofl-1.1.txt").read_bytes()
LOCKED = b"Notice from the installed bundle"
# A real SFNT larger than the JSON limit, using a font description in the name table.
REGULAR = font(license=LICENSE, weight=400, extra=[("A" * 4096, 10, 3, 1, 0x409)])
BOLD = font(license=LICENSE, cff=True)
RESTRICTED = font(8)
FONT_LIMITS = {**LIMITS, "font_count": 4, "font_file": len(REGULAR), "font_total": len(REGULAR) + len(BOLD)}


def ref(data, weight="regular"):
    return {"hash": digest(data), "name": "Requested family", "version": "1.0", "weight": weight}


def included(data, notice=OFL, weight="regular"):
    return {**ref(data, weight), "file": list(data), "notice": list(notice)}


def declared(data, notice=OFL, weight="regular"):
    return {**ref(data, weight), "file": f"fonts/{digest(data)}", "notice": f"notices/{digest(notice)}.txt"}


def members(records, assets=None):
    return {**entries({**manifest(), "version": 2, "fonts": records}), **(assets or {})}


def asset_entries(data, notice=OFL):
    record = declared(data, notice)
    return {record["file"]: data, record["notice"]: notice}


@pytest.mark.parametrize("include", [False, True])
def test_exact_assets_survive_roundtrip_with_shared_notice_and_optional_proof(codec, pdf, include):
    regular = included(REGULAR)
    bold = included(BOLD, weight="bold")
    missing = ref(RESTRICTED)
    style = {"body_font": f"font-{regular['hash']}-{bold['hash']}"}
    data = pack(codec, limits=FONT_LIMITS, fonts=[regular, regular, bold, missing], pdf=pdf,
                include=include, style=style)
    with ZipFile(io.BytesIO(data)) as zipped:
        assert json.loads(zipped.read("manifest.json"))["fonts"] == [
            declared(REGULAR), declared(BOLD, weight="bold"), missing,
        ]
        assets = {**asset_entries(REGULAR), **asset_entries(BOLD)}
        assert set(zipped.namelist()) == set(entries(pdf=pdf if include else None)) | assets.keys()
        for name, content in assets.items():
            assert zipped.read(name) == content
    doc = unpack(codec, data, FONT_LIMITS)
    assert doc["fonts"] == [regular, bold, missing]
    assert doc["style"] == style
    assert (bytes(doc["proof"]["blob"]) if include else doc["proof"]) == (pdf if include else None)


def test_open_rechecks_permission_and_resave_keeps_only_original_reference(codec):
    record = {**declared(RESTRICTED), "archive": True, "pdf": True}
    foreign = archive(members([record], asset_entries(RESTRICTED)))
    doc = unpack(codec, foreign, FONT_LIMITS)
    assert doc["fonts"] == [ref(RESTRICTED)]
    for faces in (doc["fonts"], [{**included(RESTRICTED), "archive": True}]):
        with ZipFile(io.BytesIO(pack(codec, fonts=faces, limits=FONT_LIMITS))) as zipped:
            assert set(zipped.namelist()) == set(entries())
            assert json.loads(zipped.read("manifest.json"))["fonts"] == [ref(RESTRICTED)]


def test_bundled_notice_is_checked_against_the_injected_catalog(codec):
    known = {"faces": {digest(RESTRICTED): ref(RESTRICTED)}, "notice": digest(LOCKED)}
    original = included(RESTRICTED, LOCKED)
    data = pack(codec, fonts=[original], limits=FONT_LIMITS, known=known)
    assert unpack(codec, data, FONT_LIMITS, known)["fonts"] == [original]
    assert unpack(codec, data, FONT_LIMITS)["fonts"] == [ref(RESTRICTED)]
    forged = archive(members([declared(RESTRICTED)], asset_entries(RESTRICTED)))
    with pytest.raises(BrowserError, match="[가-힣]"):
        unpack(codec, forged, FONT_LIMITS, known)
    with pytest.raises(BrowserError, match="[가-힣]"):
        pack(codec, fonts=[included(RESTRICTED)], limits=FONT_LIMITS, known=known)


def test_external_notice_must_match_the_local_authors_notice_even_when_hashes_agree(codec):
    foreign_notice = b"Some other author\n" + OFL
    foreign = archive(members([declared(REGULAR, foreign_notice)], asset_entries(REGULAR, foreign_notice)))
    with pytest.raises(BrowserError, match="[가-힣]"):
        unpack(codec, foreign, FONT_LIMITS)
    with pytest.raises(BrowserError, match="[가-힣]"):
        pack(codec, fonts=[included(REGULAR, foreign_notice)], limits=FONT_LIMITS)
    data = pack(codec, fonts=[{**ref(REGULAR), "file": list(REGULAR)}], limits=FONT_LIMITS)
    assert unpack(codec, data, FONT_LIMITS)["fonts"] == [included(REGULAR)]


@pytest.mark.parametrize("change", [
    {"hash": "../bad"}, {"weight": "heavy"}, {"name": []}, {"version": 1},
    {"file": "../original.ttf"}, {"file": f"fonts/{digest(BOLD)}"}, {"notice": "notices/terms.txt"},
    {"file": None}, {"notice": None},
])
def test_malformed_manifest_references_are_rejected(codec, change):
    record = {**declared(REGULAR), **change}
    with pytest.raises(BrowserError, match="[가-힣]"):
        unpack(codec, archive(members([record], asset_entries(REGULAR))), FONT_LIMITS)


def test_reject_damaged_bytes_missing_or_unclaimed_entries_and_duplicate_declarations(codec):
    record = declared(REGULAR)
    assets = asset_entries(REGULAR)
    for files in (
        members([record], {**assets, record["file"]: BOLD}),
        members([record], {**assets, record["notice"]: b"damaged"}),
        members([record], {record["notice"]: OFL}),
        members([record], {record["file"]: REGULAR}),
        members([record, record], assets),
        members([], assets),
        {**entries(), **assets},
    ):
        with pytest.raises(BrowserError, match="[가-힣]"):
            unpack(codec, archive(files), FONT_LIMITS)
    with pytest.raises(BrowserError, match="[가-힣]"):
        pack(codec, fonts=[{**included(REGULAR), "file": list(BOLD)}], limits=FONT_LIMITS)


@pytest.mark.parametrize("key,limit", [("font_count", 1), ("font_file", len(REGULAR) - 1),
                                       ("font_total", len(REGULAR) + len(BOLD) - 1), ("pdf", 100)])
def test_font_and_proof_limits_apply_on_save_and_reopen(codec, pdf, key, limit):
    faces = [included(REGULAR), included(BOLD, weight="bold")]
    full = pack(codec, fonts=faces, limits=FONT_LIMITS, pdf=pdf)
    short = {**FONT_LIMITS, key: limit}
    with pytest.raises(BrowserError, match="[가-힣]"):
        pack(codec, fonts=faces, limits=short, pdf=pdf)
    with pytest.raises(BrowserError, match="[가-힣]"):
        unpack(codec, full, short)


def test_shared_binary_counts_as_one_face_and_is_inspected_once(codec):
    count = codec.evaluate("""async ([bytes, ref, limits]) => {
        const file = new Blob([new Uint8Array(bytes)]);
        const inspect = FontRights.inspect;
        let count = 0;
        FontRights.inspect = (...args) => { count++; return inspect(...args); };
        try {
            await JjokArchive.pack({source: "", style: {}, includeProof: false,
                                    fonts: [{...ref, file}, {...ref, file}]}, limits);
        } finally {
            FontRights.inspect = inspect;
        }
        return count;
    }""", [list(REGULAR), ref(REGULAR), {**FONT_LIMITS, "font_count": 1}])
    assert count == 1


PAIR = f"font-{digest(REGULAR)}-{digest(BOLD)}"


@pytest.mark.parametrize("applied", [None, {"bad family": []}, {PAIR: [digest(REGULAR), "../font"]}])
def test_invalid_applied_faces_rejected_on_save_and_open(codec, pdf, applied):
    proof = {"state": "current", "mode": "rule", "appliedFonts": applied}
    with pytest.raises(BrowserError, match="글꼴"):
        pack(codec, pdf=pdf, meta=proof, limits=FONT_LIMITS)
    doc = entries({**manifest(proof=proof), "version": 2, "fonts": []}, pdf)
    with pytest.raises(BrowserError, match="글꼴"):
        unpack(codec, archive(doc), FONT_LIMITS)


@pytest.mark.parametrize("style", [
    {"body_font": PAIR},
    {"heading_font": PAIR},
    {"text_styles": {"note": {}, "heading2": {"font": PAIR}}},
    {"list_levels": [{}, {"font": PAIR}]},
    {"section_rules": [{"path": [], "role": "footer", "values": {"font": PAIR}}]},
])
@pytest.mark.parametrize("operation", ["pack", "unpack"])
def test_style_requires_manifest_record_for_both_weights(codec, style, operation):
    for incomplete in ([ref(REGULAR)], [ref(BOLD, "bold")]):
        with pytest.raises(BrowserError, match="글꼴"):
            if operation == "pack":
                pack(codec, fonts=incomplete, style=style, limits=FONT_LIMITS)
            else:
                unpack(codec, archive({**members(incomplete), "style.json": json.dumps(style)}), FONT_LIMITS)


def test_pack_checks_the_style_snapshot_written_before_font_inspection(codec):
    with pytest.raises(BrowserError, match="글꼴"):
        codec.evaluate("""async ([family, limits]) => {
            const style = {body_font: family};
            const pending = JjokArchive.pack({source: "", style, includeProof: false, fonts: []}, limits);
            style.body_font = "nanum-myeongjo";
            await pending;
        }""", [PAIR, FONT_LIMITS])
