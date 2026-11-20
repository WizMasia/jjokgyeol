"""Host-run release checks. Set JJOKGYEOL_TEST_IMAGE to the built service image."""

import hashlib
import json
import os
import re
import subprocess
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import expect, sync_playwright

from pdf_metrics import read_pages
from test_slice import render


PROOFS = Path(__file__).resolve().parents[1] / "proofs/web-editor"
PUBLICATION = "/opt/jjokgyeol/app/config/publication.json"
# First hash is the approved design signature. Second includes the full PDF
# bounding boxes and extracted line text from the same pre-refactor PDF.
BASELINES = {
    "default": ("28208635caf3ebe7f6ff314c0e8019bd897a60b88fa1428870cb2ec748db9327",
                "8cdd1eb9c972f883f717498005e5b6753500cba9f1ba9e4807dcc930b353ebff"),
    "saved": ("de354c6cbea36e9045f358525a607a25054ac212a99bf60fb446aeb4f6a4be79",
              "1666f2dce73aafcff1dc098a2ed421053ed35ba6bb0b567c36ed16d0f13de38d"),
}


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True, timeout=45).strip()


@pytest.fixture(scope="module")
def image_id():
    image = os.environ.get("JJOKGYEOL_TEST_IMAGE")
    if not image:
        pytest.skip("host acceptance requires JJOKGYEOL_TEST_IMAGE")
    platform, image_id = docker("image", "inspect", "--format",
                                "{{.Os}}/{{.Architecture}} {{.Id}}", image).split()
    assert platform == "linux/arm64"
    print(f"service {platform} {image_id}")
    return image_id


@contextmanager
def service(image_id, config=None):
    mount = [] if config is None else ["--mount", f"type=bind,src={config},dst={PUBLICATION},readonly"]
    cid = docker("run", "--pull=never", "-d", "-p", "127.0.0.1::8000", *mount, image_id)
    try:
        host = docker("port", cid, "8000/tcp")
        assert host.startswith("127.0.0.1:")
        url = "http://" + host
        for _ in range(100):
            try:
                httpx.get(url + "/", trust_env=False, timeout=1).raise_for_status()
                break
            except httpx.TransportError:
                time.sleep(0.1)
        else:
            pytest.fail(docker("logs", cid))
        yield cid, url
    finally:
        docker("rm", "-f", cid)


@pytest.fixture(scope="module")
def stock(image_id):
    with service(image_id) as running:
        yield running


def style_request(url, action, payload):
    response = httpx.post(f"{url}/api/style/{action}", json=payload, trust_env=False, timeout=15)
    response.raise_for_status()
    return response.json()


def compact_json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


@pytest.mark.parametrize("name", BASELINES)
def test_prechange_geometry(stock, name, tmp_path):
    _, url = stock
    style = {"space_pct": [100, 100]}  # Explicit original fixed-space layout.
    if name == "saved":
        saved = json.loads((PROOFS / "fixed-point-style.json").read_bytes())
        style = style_request(url, "open", saved)
        assert style_request(url, "export", style) == saved

    pdf = render(url, (PROOFS / "manuscript.md").read_text(encoding="utf-8"), style)
    (tmp_path / f"{name}.pdf").write_bytes(pdf)
    pages = read_pages(pdf)
    geometry = [
        {"size": [page.width, page.height], "lines": [
            [[c.text, c.font, c.size, c.x0, c.baseline, c.advance] for c in line.chars]
            for line in page.lines]}
        for page in pages
    ]
    encoded = compact_json(geometry)
    (tmp_path / f"{name}.json").write_bytes(encoded)
    signatures = (hashlib.sha256(encoded).hexdigest(),
                  hashlib.sha256(compact_json([asdict(p) for p in pages])).hexdigest())
    assert signatures == BASELINES[name]
    counts = [(len(p.lines), sum(len(line.chars) for line in p.lines)) for p in pages]
    print(f"{name} (lines, glyphs) per page {counts}, geometry {signatures}")


def test_saved_proof_layout_is_opt_in(stock):
    _, url = stock
    source = (PROOFS / "manuscript.md").read_text(encoding="utf-8")
    saved = json.loads((PROOFS / "fixed-point-style.json").read_bytes())
    legacy = style_request(url, "open", saved)
    proof = read_pages((PROOFS / "fixed-point.pdf").read_bytes())
    first = read_pages(render(url, source, legacy))[0].lines[1].text
    assert first == proof[0].lines[1].text
    assert first.endswith("눈에 들어오는")
    assert style_request(url, "export", legacy) == saved

    balanced = style_request(url, "open", dict(saved, paragraph_layout="balanced"))
    revised = read_pages(render(url, source, balanced))[0].lines[1].text
    assert revised == first.removesuffix(" 들어오는")


def check_defaults(url, size_pt, folder):
    stored = style_request(url, "export", {})
    assert stored["body_sp"] == size_pt * 65536
    assert style_request(url, "open", stored)["body_pt"] == str(size_pt)
    pdf = render(url, "본문", {})
    (folder / f"body-{size_pt}.pdf").write_bytes(pdf)
    page, = read_pages(pdf)
    line, = page.lines
    assert line.text == "본문"
    for char in line.chars:
        assert char.size == pytest.approx(size_pt * 72 / 72.27, abs=0.001)
    print(f"default {size_pt} TeX pt, export {stored['body_sp']} sp, glyph {line.chars[0].size} PDF pt")

    with sync_playwright() as playwright, playwright.chromium.launch() as browser:
        tab = browser.new_page()
        tab.set_default_timeout(45000)
        with tab.expect_response(lambda r: r.url == url + "/api/render") as initial:
            first_get = tab.goto(url)
        settings = tab.evaluate("""html => {
            const doc = new DOMParser().parseFromString(html, 'text/html');
            return doc.querySelector('script#editor-settings[type="application/json"]')?.textContent;
        }""", first_get.text())
        assert settings, "first HTML response must include publication defaults"
        assert str(json.loads(settings)["defaults"]["body_pt"]) == str(size_pt)
        expect(tab.locator("#body_pt")).to_have_value(str(size_pt))
        frame = tab.locator("#preview")
        expect(frame).to_have_attribute("src", re.compile(r"^blob:"))
        with tab.expect_download() as download:
            tab.get_by_role("button", name="PDF 다운로드", exact=True).click()
        path = folder / f"browser-{size_pt}.pdf"
        download.value.save_as(path)
        digest = frame.evaluate("""async frame => {
            const bytes = await (await fetch(frame.src)).arrayBuffer();
            const hash = await crypto.subtle.digest('SHA-256', bytes);
            return Array.from(new Uint8Array(hash), n => n.toString(16).padStart(2, '0')).join('');
        }""")
        assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == hashlib.sha256(initial.value.body()).hexdigest()
        body = next(line for p in read_pages(path.read_bytes()) for line in p.lines
                    if "오래된지도" in "".join(line.text.split()))
        assert body.chars[0].size == pytest.approx(size_pt * 72 / 72.27, abs=0.001)
        print(f"browser {size_pt} TeX pt, response/preview/download SHA-256 {digest}")


def test_installed_default_reaches_editor_style_and_pdf(stock, image_id, tmp_path):
    cid, url = stock
    check_defaults(url, 11, tmp_path)
    config = tmp_path / "publication.json"
    docker("cp", f"{cid}:{PUBLICATION}", str(config))
    original = config.read_bytes()
    publication = json.loads(original)
    old_value = publication["defaults"]["body_pt"]
    assert str(old_value) == "11"
    publication["defaults"]["body_pt"] = type(old_value)(15)
    changed, edits = re.subn(rb'("body_pt"\s*:\s*"?)11\b', rb'\g<1>15', original, count=1)
    assert edits == 1 and json.loads(changed) == publication
    config.write_bytes(changed)
    with service(image_id, config) as (_, changed_url):
        check_defaults(changed_url, 15, tmp_path)
        saved = json.loads((PROOFS / "fixed-point-style.json").read_bytes())
        reopened = style_request(changed_url, "open", saved)
        assert reopened["body_pt"] == "14"
        assert style_request(changed_url, "export", reopened) == saved


def test_running_size_from_one_installed_setting(stock, image_id, tmp_path):
    cid, _ = stock
    config = tmp_path / "publication.json"
    docker("cp", f"{cid}:{PUBLICATION}", str(config))
    publication = json.loads(config.read_bytes())
    assert publication["running_defaults"]["font_pt"] == 9
    publication["running_defaults"]["font_pt"] = 12.5
    config.write_text(json.dumps(publication, ensure_ascii=False), encoding="utf-8")

    rules = [{"path": [], "role": "header", "values": {"visible": True, "center": "조사기관"}}]
    with service(image_id, config) as (_, url):
        with sync_playwright() as playwright, playwright.chromium.launch() as browser:
            tab = browser.new_page()
            tab.goto(url)
            tab.get_by_role("button", name="머리글·바닥글 편집", exact=True).click()
            expect(tab.locator("#running-font_pt")).to_have_value("12.5")
        saved = style_request(url, "export", {"section_rules": rules})
        reopened = style_request(url, "open", saved)
        assert reopened["section_rules"] == rules
        page, = read_pages(render(url, "본문", reopened))
        header, body = sorted(page.lines, key=lambda line: -line.chars[0].baseline)
        assert header.text == "조사기관" and body.text == "본문"
        assert all(c.size == pytest.approx(12.5 * 72 / 72.27, abs=.001) for c in header.chars)
        assert all(c.size == pytest.approx(11 * 72 / 72.27, abs=.001) for c in body.chars)
