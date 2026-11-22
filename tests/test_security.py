"""보안 회귀 테스트 / Security regression tests for web app, workers, and parsers."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from threading import BoundedSemaphore

import httpx
import pytest

from app import jobs, server

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(server.app),
        base_url="http://127.0.0.1:8000",
    ) as api:
        yield api


async def test_rejects_disallowed_host_header(client: httpx.AsyncClient) -> None:
    response = await client.get("/", headers={"Host": "evil.example:8000"})
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


async def test_allows_explicitly_configured_host(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JJOKGYEOL_ALLOWED_HOSTS", "editor.internal,10.0.0.5:8080")
    ok = await client.get("/api/reference/settings", headers={"Host": "editor.internal:8000"})
    assert ok.status_code == 200
    ok_port = await client.get("/api/reference/settings", headers={"Host": "10.0.0.5:8080"})
    assert ok_port.status_code == 200
    wrong_port = await client.get("/api/reference/settings", headers={"Host": "10.0.0.5:9999"})
    assert wrong_port.status_code == 403


async def test_security_headers_present_on_html_and_api(client: httpx.AsyncClient) -> None:
    html = await client.get("/")
    assert html.status_code == 200
    assert html.headers["x-content-type-options"] == "nosniff"
    assert html.headers["x-frame-options"] == "SAMEORIGIN"
    assert html.headers["referrer-policy"] == "no-referrer"
    csp = html.headers["content-security-policy"]
    assert "script-src 'self'" in csp
    assert "base-uri 'none'" in csp

    api_res = await client.get("/api/reference/settings")
    assert api_res.status_code == 200
    assert api_res.headers["x-content-type-options"] == "nosniff"
    assert api_res.headers["x-frame-options"] == "SAMEORIGIN"
    assert api_res.headers["referrer-policy"] == "no-referrer"


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example"},
        {"Origin": "http://127.0.0.1:9999"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
        {"Content-Type": "application/x-www-form-urlencoded"},
        {"Content-Type": "text/plain; charset=utf-8"},
        {"Content-Type": "multipart/form-data; boundary=abc"},
    ],
)
async def test_post_api_blocks_csrf_headers_and_simple_forms(
    client: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    response = await client.post("/api/style/export", content=b"{}", headers=headers)
    assert response.status_code in (403, 415)


async def test_post_api_allows_same_origin_request(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/style/export",
        json={},
        headers={
            "Origin": "http://127.0.0.1:8000",
            "Sec-Fetch-Site": "same-origin",
        },
    )
    assert response.status_code == 200


async def test_style_file_uses_slot_and_offloads_off_event_loop(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    slots = BoundedSemaphore(1)
    monkeypatch.setattr(server, "STYLE_SLOTS", slots)
    assert slots.acquire(blocking=False)
    try:
        busy = await client.post("/api/style/export", json={})
        assert busy.status_code == 429
    finally:
        slots.release()
    ok = await client.post("/api/style/export", json={})
    assert ok.status_code == 200


async def test_article_pin_acquires_slot_before_reading_body(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    limiter = server.PIN_WORKERS
    borrowers = [object() for _ in range(limiter.total_tokens)]
    for b in borrowers:
        limiter.acquire_on_behalf_of_nowait(b)
    read_started = False

    async def body_stream():
        nonlocal read_started
        read_started = True
        yield b'{"source":"# \xec\xa0\x9c1\xec\xa1\xb0","first_line":0,"last_line":0}'

    try:
        with monkeypatch.context() as patch:
            patch.setattr(jobs, "TIMEOUT", 0.05)
            response = await client.post(
                "/api/article/pin",
                content=body_stream(),
                headers={"Content-Type": "application/json"},
            )
            assert response.status_code == 504
            assert not read_started
    finally:
        for b in borrowers:
            limiter.release_on_behalf_of(b)


def test_style_rejects_oversized_decimal_and_collections() -> None:
    from app.document import InputError
    from app.style import from_editor_style

    with pytest.raises(InputError):
        from_editor_style({"body_pt": "1" + "0" * 140})
    with pytest.raises(InputError):
        from_editor_style({"body_pt": "11." + "5" * 140})

    too_many_levels = [{"expr": "{숫자}.", "indent_mm": "0", "gap_mm": "2"}] * 33
    with pytest.raises(InputError):
        from_editor_style({"list_levels": too_many_levels})

    too_many_rules = [
        {"path": [f"제{i}장"], "role": "body", "values": {"font_pt": "10"}}
        for i in range(257)
    ]
    with pytest.raises(InputError):
        from_editor_style({"section_rules": too_many_rules})


def test_worker_env_strips_secrets_and_tex_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.security import worker_env

    monkeypatch.setenv("JJOKGYEOL_VISION_TOKEN", "super-secret-token")
    monkeypatch.setenv("TEXINPUTS", "/tmp/evil:")
    monkeypatch.setenv("LUAINPUTS", "/tmp/evil:")
    monkeypatch.setenv("LD_PRELOAD", "/tmp/evil.so")

    for role in ("engine", "poppler", "import", "pin", "render", "suggest"):
        env = worker_env(role)
        assert "JJOKGYEOL_VISION_TOKEN" not in env
        assert "TEXINPUTS" not in env
        assert "LUAINPUTS" not in env
        assert "LD_PRELOAD" not in env

    ref_env = worker_env("reference")
    assert ref_env.get("JJOKGYEOL_VISION_TOKEN") == "super-secret-token"
    assert "TEXINPUTS" not in ref_env
    assert "LD_PRELOAD" not in ref_env


def test_typeset_and_poppler_pass_sanitized_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess
    from app import reference_media, typeset
    from app.document import parse_md
    from app.style import Style

    monkeypatch.setenv("JJOKGYEOL_VISION_TOKEN", "top-secret-vision-key")
    monkeypatch.setenv("TEXINPUTS", "/tmp/untrusted:")

    captured: list[dict[str, str] | None] = []

    def fake_run(args, **kwargs):
        captured.append(kwargs.get("env"))
        if args[0] == "lualatex":
            (Path(kwargs["cwd"]) / "book.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
        else:
            kwargs["stdout"].write(b"ok")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    tex_dir = tmp_path / "texjob"
    tex_dir.mkdir()
    doc = parse_md("본문 문단")
    typeset.typeset(doc, Style(), tex_dir)
    assert captured and captured[-1] is not None
    assert "JJOKGYEOL_VISION_TOKEN" not in captured[-1]
    assert "TEXINPUTS" not in captured[-1]
    assert "LUAINPUTS" in captured[-1]

    poppler_dir = tmp_path / "poppler"
    poppler_dir.mkdir()
    reference_media._run(["pdftohtml", "-v"], poppler_dir, 1e18, 1024)
    assert captured[-1] is not None
    assert "JJOKGYEOL_VISION_TOKEN" not in captured[-1]
    assert "TEXINPUTS" not in captured[-1]


def test_outbound_url_enforces_https_for_remote_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    from app import reference
    from app.ai import style as ai_style

    monkeypatch.setenv("JJOKGYEOL_VISION_MODEL", "vision-test")
    monkeypatch.setenv("JJOKGYEOL_VISION_TOKEN", "bearer-secret")
    monkeypatch.setenv("JJOKGYEOL_VISION_URL", "http://remote.example/v1")
    with pytest.raises(ValueError, match="JJOKGYEOL_VISION_URL"):
        reference.load_settings()

    monkeypatch.setenv("JJOKGYEOL_VISION_URL", "http://host.docker.internal:1234/v1")
    with pytest.raises(ValueError, match="JJOKGYEOL_VISION_URL"):
        reference.load_settings()

    monkeypatch.setenv("JJOKGYEOL_VISION_TOKEN", "")
    settings = reference.load_settings()
    assert settings["vision_url"] == "http://host.docker.internal:1234/v1"

    monkeypatch.setenv("JJOKGYEOL_VISION_TOKEN", "bearer-secret")
    monkeypatch.setenv("JJOKGYEOL_VISION_URL", "https://remote.example/v1")
    assert reference.load_settings()["vision_url"] == "https://remote.example/v1"

    monkeypatch.setenv("JJOKGYEOL_STYLE_URL", "http://remote.example/v1")
    monkeypatch.setenv("JJOKGYEOL_STYLE_MODEL", "style-test")
    with pytest.raises(ValueError):
        ai_style._infer("본문 크기를 키워줘", {"body_pt": "11"})


def test_workers_sanitize_internal_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess
    import sys

    # Verify import_worker strips English internal ValueError details.
    imp_dir = tmp_path / "imp"
    imp_dir.mkdir()
    (imp_dir / "request.bin").write_bytes(b"hello")
    code = (
        "import sys; from app import import_worker; from app.importers import read; "
        "read.import_file = lambda n, d: (_ for _ in ()).throw(ValueError('secret /etc/passwd detail')); "
        "sys.exit(import_worker.worker('a.txt', 256 * 1024 * 1024))"
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=imp_dir)
    assert proc.returncode == jobs.INVALID_INPUT
    field, message = json.loads((imp_dir / "invalid.json").read_text("utf-8"))
    assert field == "source"
    assert "/etc/passwd" not in message and "secret" not in message

    # Verify reference worker strips JJOKGYEOL_ env names / internal errors.
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    (ref_dir / "request.bin").write_bytes(b"x")
    env = dict(os.environ, JJOKGYEOL_VISION_URL="http://remote.example/v1",
               JJOKGYEOL_VISION_MODEL="m", JJOKGYEOL_VISION_TOKEN="tok")
    proc2 = subprocess.run(
        [sys.executable, "-IB", str(Path(__file__).resolve().parents[1] / "app/reference.py"), "png", "0", "9999999999"],
        cwd=ref_dir, env=env,
    )
    assert proc2.returncode == jobs.INVALID_INPUT
    _, msg2 = json.loads((ref_dir / "invalid.json").read_text("utf-8"))
    assert "JJOKGYEOL_" not in msg2


def _make_spoofed_compress_size_zip(member: str, payload: bytes) -> bytes:
    """중앙 디렉터리의 compress_size를 위조해 압축률 검사를 우회하려는 ZIP을 생성합니다.

    Build a ZIP whose Central Directory compress_size is inflated to spoof a 1:1 ratio.
    """
    from io import BytesIO
    import struct
    from zipfile import ZIP_DEFLATED, ZipFile

    buf = BytesIO()
    with ZipFile(buf, "w", compression=ZIP_DEFLATED, compresslevel=9) as zf:
        zf.writestr(member, payload)
    raw = bytearray(buf.getvalue())
    cd_pos = raw.index(b"PK\x01\x02")
    # Central directory compressed size is at +20..+24; set it equal to len(payload).
    raw[cd_pos + 20 : cd_pos + 24] = struct.pack("<I", len(payload))
    return bytes(raw)


def test_spoofed_zip_header_rejected_in_import_and_reference() -> None:
    from app.importers.read import import_file
    from app.reference import load_settings
    from app.reference_office import analyze_office
    from app.security import SafeZipArchive

    # 300 KiB of spaces inside XML compresses to < 400 bytes (> 750x ratio), with spoofed compress_size.
    payload = b"<w:document>" + (b" " * 300_000) + b"</w:document>"
    spoofed = _make_spoofed_compress_size_zip("word/document.xml", payload)

    with pytest.raises(ValueError, match="압축 해제 한도"):
        import_file("bomb.docx", spoofed)

    settings = load_settings()
    with pytest.raises(ValueError, match="압축 해제 한도"):
        analyze_office(spoofed, "docx", settings)

    # Also verify streaming read enforces cumulative byte budget even if initial header checks passed.
    from io import BytesIO
    from zipfile import ZIP_DEFLATED, ZipFile

    buf = BytesIO()
    with ZipFile(buf, "w", compression=ZIP_DEFLATED) as zf:
        zf.writestr("a.xml", b"x" * 2000)
    with SafeZipArchive(
        buf.getvalue(),
        max_entries=10,
        max_uncompressed_bytes=4000,
        max_compression_ratio=250,
        limit_message="압축 해제 한도",
        duplicate_or_encrypted_message="중복",
        compression_message="압축 형식",
    ) as archive:
        archive._max_uncompressed_bytes = 500
        with pytest.raises(ValueError, match="압축 해제 한도"):
            archive.read("a.xml")


def test_reference_office_rejects_unsupported_zip_compression() -> None:
    from io import BytesIO
    from zipfile import ZIP_BZIP2, ZipFile
    from app.reference import load_settings
    from app.reference_office import analyze_office

    buf = BytesIO()
    with ZipFile(buf, "w", compression=ZIP_BZIP2) as zf:
        zf.writestr("word/document.xml", b"<document><body/></document>")
    settings = load_settings()
    with pytest.raises(ValueError, match="압축 형식"):
        analyze_office(buf.getvalue(), "docx", settings)


def test_html_importer_nested_skip_and_href_escape() -> None:
    from app.importers.read import import_file

    nested = b"<p>\xec\x95\x9e</p><svg><svg>inner</svg>leaked</svg><p>\xeb\x92\xa4</p>"
    result = import_file("nested.html", nested)
    assert "leaked" not in result.markdown
    assert "inner" not in result.markdown
    assert "앞" in result.markdown and "뒤" in result.markdown

    link = b'<p><a href="https://example.invalid/a*b_c[d]">\xeb\xa7\x81\xed\x81\xac</a></p>'
    link_res = import_file("link.html", link)
    assert r"a\*b\_c\[d\]" in link_res.markdown


def test_hwp_importer_out_of_bounds_shape_raises_value_error() -> None:
    import struct
    from app.importers.common import Manuscript
    from app.importers import hwp

    # Out-of-bounds char shape in _paragraph.
    raw = "가".encode("utf-16le")
    with pytest.raises(ValueError):
        hwp._paragraph(raw, [(0, 99)], [0], iter(()), lambda _m: None)

    # Out-of-bounds para shape in _section.
    payload = struct.pack("<IIH", 0, 0, 99) + b"\x00" * 12
    rec_header = hwp.PARA_HEADER | (0 << 10) | (len(payload) << 20)
    section_data = struct.pack("<I", rec_header) + payload
    with pytest.raises(ValueError):
        hwp._section(section_data, [0], [0], Manuscript())


def test_evaluation_blind_pdf_enforces_size_and_page_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    from io import BytesIO
    from pypdf import PdfWriter
    from evaluation import packet

    # Size bound rejects oversized payload before touching PdfReader/pdftoppm.
    monkeypatch.setattr(packet, "MAX_PDF_BYTES", 32)
    with pytest.raises(ValueError, match="size limit"):
        packet.blind_pdf(b"%PDF-1.4" + b"0" * 40)

    # Page bound rejects PDFs exceeding MAX_PDF_PAGES.
    monkeypatch.setattr(packet, "MAX_PDF_BYTES", 1024 * 1024)
    monkeypatch.setattr(packet, "MAX_PDF_PAGES", 2)
    writer = PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=72, height=72)
    buf = BytesIO()
    writer.write(buf)
    with pytest.raises(ValueError, match="page count"):
        packet.blind_pdf(buf.getvalue())



