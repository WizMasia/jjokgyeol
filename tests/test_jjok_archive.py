"""Exercise the shared classic scripts offline, with Python as the independent ZIP reader/writer."""
import io
import json
import re
import stat
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

import pytest
from playwright.sync_api import Error as BrowserError, expect
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject, NullObject

from test_editor import browser
from test_slice import server_url

STATIC = Path(__file__).resolve().parents[1] / "app/static"
SOURCE = "\ufeff# 원고\r\n빈 줄\n\r\n끝\r"
STYLE = json.loads((STATIC / "general-report.json").read_text(encoding="utf-8"))
LIMITS = {"request": 8192, "file": 131072}


@pytest.fixture
def codec(browser, request):
    scripts = ("zip.min.js", "jjok-archive.js")
    unexpected = []
    profile = getattr(request, "param", None)
    old_webkit = profile == "old-webkit"
    disable_compression = "globalThis.CompressionStream = globalThis.DecompressionStream = undefined;"
    old_globals = "delete Promise.withResolvers; " + disable_compression

    def serve(route):
        name = route.request.url.rsplit("/", 1)[1]
        if name == "":
            html = "".join(f'<script src="/{script}"></script>' for script in scripts)
            workers = "'self'" if old_webkit else "'none'"
            route.fulfill(content_type="text/html; charset=utf-8", body=html, headers={
                "Content-Security-Policy": f"default-src 'none'; script-src 'self'; worker-src {workers}",
            })
        elif name == "pdf.worker.min.mjs" and old_webkit:
            # A main-thread fallback must not hide a failure to start the legacy worker.
            worker_only = "if (typeof document !== 'undefined') throw Error('expected a dedicated worker');"
            route.fulfill(content_type="text/javascript", body=worker_only + old_globals + (STATIC / name).read_text())
        elif name in (*scripts, "pdf.min.mjs", "pdf.worker.min.mjs") and (STATIC / name).exists():
            route.fulfill(path=STATIC / name)
        else:
            unexpected.append(name)
            route.abort()

    with browser.new_context() as context:
        page = context.new_page()
        if profile == "fallback":
            page.add_init_script(disable_compression)
        elif old_webkit:
            page.add_init_script(old_globals)
        page.route("**/*", serve)
        page.goto("http://127.0.0.1/")
        yield page
        assert not unexpected, unexpected


@pytest.fixture
def pdf():
    writer = PdfWriter()
    writer.add_blank_page(width=320, height=430)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def manifest(include=True, proof=None):
    values = {"format": "jjokgyeol", "version": 1, "includeProof": include}
    if proof is not None:
        values["proof"] = proof
    return values


def entries(metadata=None, pdf=None):
    files = {
        "manuscript.md": SOURCE.encode(),
        "style.json": json.dumps(STYLE, ensure_ascii=False).encode(),
        "manifest.json": json.dumps(manifest() if metadata is None else metadata).encode(),
    }
    if pdf is not None:
        files["proof.pdf"] = pdf
    return files


def archive(files, compression=ZIP_DEFLATED):
    out = io.BytesIO()
    with ZipFile(out, "w", compression) as zipped:
        for name, data in files.items():
            zipped.writestr(name, data)
    return out.getvalue()


def unpack(page, data, limits=LIMITS):
    return page.evaluate("""async ([bytes, limits]) => {
        const doc = await JjokArchive.unpack(new File([new Uint8Array(bytes)], "문서.jjok"), limits);
        if (doc.proof) {
            const {blob, ...info} = doc.proof;
            if (!(blob instanceof Blob) || blob.type !== "application/pdf") throw Error("expected PDF Blob");
            doc.proof = {...info, blob: Array.from(new Uint8Array(await blob.arrayBuffer()))};
        }
        return doc;
    }""", [list(data), limits])


def pack(page, *, include=True, pdf=None, meta=None, source=SOURCE, style=STYLE, limits=LIMITS):
    doc = {"source": source, "style": style, "includeProof": include, "proof": None}
    if pdf is not None:
        doc["proof"] = {**(meta or {"state": "stale", "mode": "model"}), "blob": list(pdf)}
    return bytes(page.evaluate("""async ([doc, limits]) => {
        if (doc.proof) doc.proof.blob = new Blob([new Uint8Array(doc.proof.blob)]);
        const file = await JjokArchive.pack(doc, limits);
        if (!(file instanceof Blob)) throw Error("expected ZIP Blob");
        return Array.from(new Uint8Array(await file.arrayBuffer()));
    }""", [doc, limits]))


@pytest.mark.parametrize("include,has_pdf,state,mode", [
    (False, True, "current", "rule"), (True, False, None, None),
    (True, True, "stale", "model"), (True, True, "current", "mixed"),
])
def test_pack_is_a_standard_zip(codec, pdf, include, has_pdf, state, mode):
    info = {"state": state, "mode": mode}
    data = pack(codec, include=include, pdf=pdf if has_pdf else None, meta=info)
    with ZipFile(io.BytesIO(data)) as zipped:
        has_proof = include and has_pdf
        assert set(zipped.namelist()) == set(entries(pdf=pdf if has_proof else None))
        assert zipped.testzip() is None
        assert all(e.compress_type in (ZIP_STORED, ZIP_DEFLATED) for e in zipped.infolist())
        assert zipped.read("manuscript.md") == SOURCE.encode()
        assert json.loads(zipped.read("style.json")) == STYLE
        assert json.loads(zipped.read("manifest.json")) == manifest(include, info if has_proof else None)
        if has_proof:
            exported = zipped.read("proof.pdf")
            assert exported == pdf
            assert PdfReader(io.BytesIO(exported), strict=True).pages[0].mediabox.width == 320


@pytest.mark.parametrize("compression", [ZIP_STORED, ZIP_DEFLATED])
@pytest.mark.parametrize("codec,include,mode", [
    ("platform", True, "rule"), ("fallback", True, "model"),
    ("platform", True, None), ("platform", False, None),
], indirect=["codec"])
def test_open_python_zip(codec, pdf, compression, include, mode):
    info = {"state": "stale" if mode == "model" else "current", "mode": mode} if mode else None
    data = archive(entries(manifest(include, info), pdf if mode else None), compression)
    assert unpack(codec, data) == {"source": SOURCE, "style": STYLE, "includeProof": include,
                                   "proof": {**info, "blob": list(pdf)} if mode else None}


@pytest.mark.parametrize("codec", ["fallback"], indirect=True)
def test_pack_and_reopen_without_platform_compression(codec, pdf):
    assert codec.evaluate("zip.VERSION") == "2.25.0"
    data = pack(codec, pdf=pdf)
    assert unpack(codec, data)["proof"] == {"blob": list(pdf), "state": "stale", "mode": "model"}


@pytest.mark.parametrize("name", ["manifest.json", "style.json", "manuscript.md"])
def test_missing_required_entry(codec, name):
    files = entries()
    del files[name]
    with pytest.raises(BrowserError, match="[가-힣]"):
        unpack(codec, archive(files))


@pytest.mark.parametrize("extra", ["aside.md", "notes/manuscript.md", "notes/"])
def test_unexpected_entry(codec, extra):
    with pytest.raises(BrowserError, match="[가-힣]"):
        unpack(codec, archive({**entries(), extra: b""}))


def test_duplicate_and_nonregular_entry(codec):
    out = io.BytesIO(archive(entries()))
    with ZipFile(out, "a") as zipped, pytest.warns(UserWarning, match="Duplicate"):
        zipped.writestr("manuscript.md", "")
    with pytest.raises(BrowserError, match="[가-힣]"):
        unpack(codec, out.getvalue())

    link = ZipInfo("manuscript.md")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    files = entries()
    files[link] = files.pop("manuscript.md")
    with pytest.raises(BrowserError, match="[가-힣]"):
        unpack(codec, archive(files))


@pytest.mark.parametrize("metadata,with_pdf", [
    ({**manifest(), "version": 2}, False),
    ({**manifest(), "format": "other"}, False),
    ({"format": "jjokgyeol", "version": 1}, False),
    (manifest(include="false"), False),
    (manifest(), True),
    (manifest(proof={"state": "current", "mode": "rule"}), False),
    (manifest(False, {"state": "stale", "mode": "model"}), True),
    (manifest(proof={"state": "draft", "mode": "model"}), True),
    (manifest(proof={"state": "stale", "mode": "other"}), True),
])
def test_manifest_must_describe_the_contents(codec, pdf, metadata, with_pdf):
    with pytest.raises(BrowserError, match="[가-힣]"):
        unpack(codec, archive(entries(metadata, pdf if with_pdf else None)))


@pytest.mark.parametrize("name,data", [
    ("style.json", b"[]"), ("style.json", b'{"body_sp":1.5}'), ("style.json", b"{"),
    ("manifest.json", b"null"), ("manuscript.md", b"\xff"),
])
def test_invalid_member_contents(codec, name, data):
    with pytest.raises(BrowserError, match="[가-힣]"):
        unpack(codec, archive({**entries(), name: data}))


def test_reject_damaged_zip_or_member_checksum(codec):
    original = archive(entries(), ZIP_STORED)
    for damaged in (original[:-20], original.replace(SOURCE.encode(), b"?" * len(SOURCE.encode()), 1)):
        with pytest.raises(BrowserError, match="[가-힣]"):
            unpack(codec, damaged)


def test_pdf_validation_in_both_directions(codec, pdf):
    info = {"state": "current", "mode": "rule"}
    for damaged in (b"%PDF-1.7\n%%EOF\n", re.sub(rb"startxref\s+\d+", b"startxref\n1", pdf)):
        with pytest.raises(BrowserError, match="PDF"):
            unpack(codec, archive(entries(manifest(proof=info), damaged)))
        with pytest.raises(BrowserError, match="PDF"):
            pack(codec, pdf=damaged)


def test_limits_cover_archive_text_and_total_inflated_bytes(codec):
    source = "결" * 2000
    files = {**entries(), "manuscript.md": source.encode()}
    data = archive(files)
    text_limit = len(source.encode())
    total = sum(map(len, files.values()))
    assert unpack(codec, data, {"request": text_limit, "file": total})["source"] == source
    for limits in ({"request": text_limit - 1, "file": total}, {"request": text_limit, "file": total - 1},
                   {"request": text_limit, "file": len(data) - 1}):
        with pytest.raises(BrowserError, match="[가-힣]"):
            unpack(codec, data, limits)
    with pytest.raises(BrowserError, match="[가-힣]"):
        pack(codec, source=source, limits={"request": text_limit - 1, "file": total * 2})


def test_packed_zip_must_fit_the_file_limit(codec):
    data = pack(codec, source="", style={})
    with pytest.raises(BrowserError, match="[가-힣]"):
        pack(codec, source="", style={}, limits={"request": 1000, "file": len(data) - 1})


def test_cross_reference_table_and_stream_need_their_trailers(codec, pdf):
    # pypdf writes a cross-reference stream for an incremental revision.
    writer = PdfWriter(io.BytesIO(pdf), incremental=True)
    writer.add_metadata({"/Subject": "재교정"})
    output = io.BytesIO()
    writer.write(output)
    revision = output.getvalue()
    assert PdfReader(io.BytesIO(revision), strict=True).metadata.subject == "재교정"
    assert bytes(unpack(codec, pack(codec, pdf=revision))["proof"]["blob"]) == revision

    for marked in (pdf.replace(b"startxref", b"% checked trailer\nstartxref"),
                   revision.replace(b"/Type /XRef", b"/Note (upstream) /Type /XRef")):
        assert PdfReader(io.BytesIO(marked), strict=True).pages[0].mediabox.width == 320
        assert bytes(unpack(codec, pack(codec, pdf=marked))["proof"]["blob"]) == marked

    for broken in (pdf.replace(b"trailer", b"missing"), revision.replace(b"endstream", b"badstream")):
        with pytest.raises(BrowserError, match="PDF"):
            pack(codec, pdf=broken)


def test_inflater_output_is_checked_while_reading(codec):
    # Keep the ZIP valid and have the platform inflater deliver one excess byte.
    codec.evaluate("""() => {
        zip.configure({DecompressionStream: class {
            constructor(format) {
                const native = new DecompressionStream(format);
                this.writable = native.writable;
                this.readable = native.readable.pipeThrough(new TransformStream({
                    transform(chunk, controller) {
                        controller.enqueue(chunk);
                        controller.enqueue(Uint8Array.of(0));
                        window.extraOutputDelivered = true;
                    },
                }));
            }
        }});
    }""")
    with pytest.raises(BrowserError, match="[가-힣]"):
        unpack(codec, archive(entries()))
    assert codec.evaluate("window.extraOutputDelivered")


def unreadable_proof(problem):
    writer = PdfWriter()
    if problem != "zero-pages":
        page = writer.add_blank_page(width=320, height=430)
        if problem == "missing-page":
            writer.root_object["/Pages"]["/Kids"][0] = NullObject()
        else:
            # The exporter omitted a resource that the page still uses.
            content = DecodedStreamObject()
            content.set_data({"missing-image": b"/MissingImage Do", "missing-shading": b"/Gradient sh"}[problem])
            page[NameObject("/Contents")] = writer._add_object(content)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


@pytest.mark.parametrize("problem", ["zero-pages", "missing-page", "missing-image", "missing-shading"])
def test_readable_first_page_required_on_open_and_save(codec, problem):
    pdf = unreadable_proof(problem)
    data = archive(entries(manifest(proof={"state": "current", "mode": "model"}), pdf))
    with pytest.raises(BrowserError, match="PDF"):
        unpack(codec, data)
    with pytest.raises(BrowserError, match="PDF"):
        pack(codec, pdf=pdf)


def test_first_page_in_compressed_object_stream(codec):
    pdf = (STATIC.parents[1] / "proofs/decision-model/model.pdf").read_bytes()
    reader = PdfReader(io.BytesIO(pdf), strict=True)
    assert reader.pages[0].indirect_reference.idnum in reader.xref_objStm
    assert bytes(unpack(codec, pack(codec, pdf=pdf))["proof"]["blob"]) == pdf


@pytest.mark.parametrize("problem", ["zero-pages", "missing-image", "missing-shading"])
def test_invalid_proof_keeps_the_open_manuscript(browser, server_url, pdf, problem):
    data = archive(entries(manifest(proof={"state": "current", "mode": "rule"}), unreadable_proof(problem)))
    with browser.new_page() as page:
        page.route("**/api/render", lambda r: r.fulfill(content_type="application/pdf", body=pdf))
        page.goto(server_url)
        expect(page.locator("#status")).to_have_text("최신 버전")
        old_preview = page.locator("#preview").get_attribute("src")
        original_source = page.locator("#source").input_value()
        page.locator("#source").fill("저장 전 원고")
        old_status = page.locator("#status").inner_text()
        prompts = []
        page.on("dialog", lambda d: (prompts.append(d.message), d.accept()))
        page.locator("#document-file").set_input_files({"name": "빈 지면.jjok", "mimeType": "application/zip", "buffer": data})
        expect(page.locator("#error")).to_contain_text("PDF")
        expect(page.locator("#source")).to_have_value("저장 전 원고")
        expect(page.locator("#status")).to_have_text(old_status)
        assert page.locator("#preview").get_attribute("src") == old_preview
        assert not prompts
        page.locator("#source").press("ControlOrMeta+z")
        expect(page.locator("#source")).to_have_value(original_source)


@pytest.mark.parametrize("codec", ["old-webkit"], indirect=True)
def test_offline_pdf_worker_and_zip_without_new_platform_apis(codec):
    assert codec.evaluate("typeof Promise.withResolvers === 'undefined' && typeof CompressionStream === 'undefined'"
                          " && typeof DecompressionStream === 'undefined'")
    assert unpack(codec, pack(codec, include=False))["source"] == SOURCE
    assert codec.evaluate("typeof Promise.withResolvers === 'undefined'")
    workers = []
    codec.on("worker", lambda worker: workers.append(worker.url))
    pdf = (STATIC.parents[1] / "proofs/decision-model/model.pdf").read_bytes()
    zipped = pack(codec, pdf=pdf)
    assert unpack(codec, zipped)["proof"] == {"blob": list(pdf), "state": "stale", "mode": "model"}
    assert len(workers) >= 2 and all(url.endswith("/pdf.worker.min.mjs") for url in workers)


def test_first_page_can_paint_a_gradient(codec):
    writer = PdfWriter()
    page = writer.add_blank_page(width=720, height=1440)
    page[NameObject("/Resources")] = DictionaryObject.read_from_stream(io.BytesIO(
        b"<< /Shading << /Gradient << /ShadingType 2 /ColorSpace /DeviceRGB /Coords [0 0 720 1440] "
        b"/Function << /FunctionType 2 /Domain [0 1] /C0 [1 1 1] /C1 [0 0 1] /N 1 >> >> >> >>"
    ), writer)
    content = DecodedStreamObject()
    content.set_data(b"/Gradient sh")
    page[NameObject("/Contents")] = writer._add_object(content)
    out = io.BytesIO()
    writer.write(out)
    pdf = out.getvalue()
    assert bytes(unpack(codec, pack(codec, pdf=pdf))["proof"]["blob"]) == pdf


@pytest.mark.parametrize("codec", ["old-webkit"], indirect=True)
def test_drawing_failure_releases_validation_resources_and_allows_retry(codec, pdf):
    codec.evaluate("""() => {
        const getContext = HTMLCanvasElement.prototype.getContext;
        window.drawTargets = [];
        HTMLCanvasElement.prototype.getContext = function(type, ...options) {
            if (type === "2d") drawTargets.push({canvas: this, width: this.width, height: this.height});
            return getContext.call(this, type, ...options);
        };
    }""")
    errors = []
    codec.on("pageerror", lambda error: errors.append(str(error)))
    with codec.expect_event("worker") as failed_worker, pytest.raises(BrowserError, match="PDF"):
        pack(codec, pdf=unreadable_proof("missing-shading"))
    assert bytes(unpack(codec, pack(codec, pdf=pdf))["proof"]["blob"]) == pdf
    assert failed_worker.value not in codec.workers
    assert not errors
    targets = codec.evaluate("""() => drawTargets.map(({canvas, width, height}) =>
        [width, height, canvas.width, canvas.height, canvas.isConnected])""")
    # Each validation must use a bounded detached bitmap and release it, including on failure.
    assert targets.count([64, 64, 0, 0, False]) == 3
