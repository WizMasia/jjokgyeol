"""End-to-end acceptance of the first editor, on a local service and a pinned image."""

import hashlib
import json
import os
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path

import httpx
import pytest

from settings_ui import close_settings, open_settings
from proof_response import selected_pdf

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).with_name("fixtures")
SAMPLE = ROOT / "proofs/engine-comparison/manuscript.md"
CASES = json.loads((FIXTURES / "layout-cases.json").read_text(encoding="utf-8"))
SOURCE = (FIXTURES / "page-edge.md").read_text(encoding="utf-8")


def render(url, source, style):
    response = httpx.post(
        url + "/api/render", json={"source": source, "style": style},
        timeout=45, trust_env=False,
    )
    assert response.status_code == 200, response.text[:300] if response.is_error else ""
    assert response.headers["content-type"].split(";")[0] == "application/pdf"
    assert response.headers["cache-control"] == "no-store"
    return response.content


@pytest.fixture(scope="module")
def server_url():
    import uvicorn
    from app.server import app

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            for _ in range(200):
                if server.started or not thread.is_alive():
                    break
                time.sleep(0.05)
            assert server.started, "로컬 웹 서버가 시작되지 않았습니다"
            yield f"http://127.0.0.1:{sock.getsockname()[1]}"
        finally:
            server.should_exit = True
            thread.join(35)
            assert not thread.is_alive()


def pdf_lines(data):
    from pdf_metrics import read_pages

    pages = []
    for page in read_pages(data):
        rows = [("".join(line.text.split()), line.chars[0].baseline, line.chars)
                for line in page.lines if line.chars]
        pages.append((page.width, page.height, rows))
    assert pages, "PDF has no pages"
    return pages


def page_for(pages, text):
    needle = "".join(text.split())
    return [
        n for n, (_, _, lines) in enumerate(pages, 1)
        for value, _, _ in lines if needle in value
    ]


def assert_note(pages, expected):
    assert page_for(pages, CASES["reference_text"]) == [expected]
    assert page_for(pages, CASES["footnote_text"]) == [expected]
    _, height, lines = pages[expected - 1]
    note_y, = [y for text, y, _ in lines if "물길을따라" in text]
    ref_y, = [y for text, y, _ in lines if CASES["reference_text"] in text]
    assert note_y < ref_y
    assert note_y < height / 2


def test_pdf_gate(tmp_path):
    from test_typeset import render_candidate_for_test

    latin_advances = {}
    for label, spec in CASES["candidates"].items():
        latin_advances[label] = []
        folder = tmp_path / label
        folder.mkdir()
        pages = pdf_lines(render_candidate_for_test(SOURCE, CASES, label, folder))
        # Footnote superscripts can be separate PDF spans at a different height.
        target_words = [line.split()[0] for line in spec["lines"]]
        target_rows = [
            (line.rstrip("0123456789¹²³"), chars)
            for _, _, rows in pages for line, _, chars in rows
            if any(line.startswith(word) for word in target_words)
        ]
        expected_lines = spec["lines"]
        assert [text for text, _ in target_rows] == ["".join(s.split()) for s in expected_lines]
        for (_, chars), expected in zip(target_rows, expected_lines):
            letters = [c for c in chars if not c.text.isspace()]
            words = expected.split()
            index = 0
            for n, word in enumerate(words):
                run = letters[index:index + len(word)]
                assert "".join(c.text for c in run) == word
                for left, right in zip(run, run[1:]):
                    if word in ("River", "Walk"):
                        latin_advances[label].append(right.x0 - left.x0)
                    if all("가" <= c.text <= "힣" for c in (left, right)):
                        gap = right.x0 - left.x0 - left.advance
                        assert gap == pytest.approx(left.size * spec["tracking_pct"] / 100, abs=0.05)
                index += len(word)
                if n + 1 < len(words):
                    # NanumMyeongjo's natural space is 307 font units per 1024 em.
                    last = run[-1]
                    gap = letters[index].x0 - last.x0 - last.advance
                    wanted = last.size * 307 / 1024 * spec["space_pct"] / 100
                    assert gap == pytest.approx(wanted, abs=0.05)
                left_edge = CASES["style"]["margins_mm"][3] * 72 / 25.4
                right_edge = (CASES["style"]["page_mm"][0] - CASES["style"]["margins_mm"][1]) * 72 / 25.4
                assert run[0].x0 >= left_edge - 0.05
                assert (run[-1].x0 + run[-1].advance) <= right_edge + 0.05
        assert_note(pages, spec["reference_page"])
    assert len(latin_advances["A"]) == 7
    assert latin_advances["B"] == pytest.approx(latin_advances["A"], abs=0.05)


def test_body_size_moves_note(server_url):
    # All prelude paragraphs are deliberately short, including at 16pt.
    prelude = "강을 따라 걸었다.\n\n" * 40
    for body_pt, leading_pt, expected in [(11, 17, 2), (16, 24, 3)]:
        data = render(server_url, prelude + SOURCE, {"body_pt": body_pt, "leading_pt": leading_pt})
        assert_note(pdf_lines(data), expected)


def test_two_real_clients(server_url):
    marks = ["물결과갈대", "연필과지도"]
    ready = threading.Barrier(len(marks))

    def send(mark):
        ready.wait(timeout=5)
        return pdf_lines(render(server_url, mark, {}))

    with ThreadPoolExecutor(max_workers=len(marks)) as pool:
        first, second = pool.map(send, marks)
    assert page_for(first, marks[0]) == [1] and not page_for(first, marks[1])
    assert page_for(second, marks[1]) == [1] and not page_for(second, marks[0])


def test_unplaceable_cluster_reports_width(server_url):
    response = httpx.post(server_url + "/api/render", json={
        "source": "강", "style": {"page_mm": [37, 210]},
    }, trust_env=False, timeout=45)
    assert response.status_code == 422
    assert response.json()["field"] == "style" and "줄 폭" in response.json()["message"]


def test_tracking_scales_with_printed_type(server_url):
    _, _, rows = pdf_lines(render(server_url, "# 서재\n\n문장", {"ko_tracking_pct": -7}))[0]
    for word in ("서재", "문장"):
        chars, = [chars for text, _, chars in rows if text == word]
        left, right = chars
        gap = right.x0 - left.x0 - left.advance
        assert gap == pytest.approx(-0.07 * left.size, abs=0.05)


def test_real_edit_export(server_url):
    from playwright.sync_api import expect, sync_playwright

    sample = SAMPLE.read_text(encoding="utf-8")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.set_default_timeout(45000)
            with page.expect_response(lambda r: r.url.endswith("/api/render")) as initial:
                page.goto(server_url)
            editor = page.get_by_role("textbox", name="마크다운 원고")
            expect(editor).to_have_value(sample)
            old_pdf = selected_pdf(initial.value, page)
            old_pages = pdf_lines(old_pdf)
            width, height, _ = old_pages[0]
            assert (width, height) == pytest.approx((419.53, 595.28), abs=0.15)
            assert page_for(old_pages, "종이 위에서 읽는 문장") == [1]
            revised = sample.replace("종이 위에서 읽는 문장", "고친 지도의 제목")
            editor.fill(revised)
            open_settings(page)
            page.get_by_label("본문 크기", exact=False).fill("16")
            close_settings(page)
            with page.expect_download() as downloaded:
                page.get_by_role("button", name="PDF 다운로드", exact=True).click()
            pdf = downloaded.value.path().read_bytes()
            viewer = page.locator("iframe").first
            preview_hash = viewer.evaluate("""async frame => {
                const bytes = await (await fetch(frame.src)).arrayBuffer();
                const hash = await crypto.subtle.digest('SHA-256', bytes);
                return Array.from(new Uint8Array(hash), n => n.toString(16).padStart(2, '0')).join('');
            }""")
            assert preview_hash == hashlib.sha256(pdf).hexdigest()
            new_pages = pdf_lines(pdf)
            assert page_for(new_pages, "고친 지도의 제목") == [1]
            assert not page_for(new_pages, "종이 위에서 읽는 문장")
            for pages, size in [(old_pages, 11), (new_pages, 16)]:
                body = next(chars for _, _, lines in pages for line, _, chars in lines if "오래된지도" in line)
                assert body[0].size == pytest.approx(size, abs=0.12)
            assert [line for line, _, _ in new_pages[0][2]][1:] != [
                line for line, _, _ in old_pages[0][2]
            ][1:]
        finally:
            browser.close()


def test_deployment_parity():
    image = os.environ.get("JJOKGYEOL_TEST_IMAGE")
    if not image:
        pytest.skip("배포 인수 시 빌드한 서비스 이미지 ID를 JJOKGYEOL_TEST_IMAGE에 지정")

    def docker(*args):
        return subprocess.check_output(["docker", *args], text=True, timeout=45).strip()

    # Resolve once, so a mutable tag cannot change between the two starts.
    image_id = docker("image", "inspect", "--format", "{{.Id}}", image)
    with ExitStack() as cleanup:
        signatures = []
        for _ in range(2):
            cid = docker("run", "--pull=never", "-d", "-p", "127.0.0.1::8000", image_id)
            cleanup.callback(docker, "rm", "-f", cid)
            host = docker("port", cid, "8000/tcp")
            assert host.startswith("127.0.0.1:")
            url = "http://" + host
            for _ in range(150):
                try:
                    response = httpx.get(url + "/", timeout=1, trust_env=False)
                    if response.status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.2)
            data = render(url, (SAMPLE.read_text(encoding="utf-8") + "\n\n") * 3, {})
            pages = pdf_lines(data)
            assert len(pages) >= 2
            signatures.append([
                (width, height, [
                    (line, round(y, 2), tuple((c.font, round(c.size, 2)) for c in chars))
                    for line, y, chars in rows
                ]) for width, height, rows in pages
            ])
        assert signatures[0] == signatures[1]
