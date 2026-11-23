"""Round-trip a working document through a running web editor and the built desktop UI."""
import base64
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.request import urlopen
from zipfile import ZipFile

from playwright.sync_api import expect, sync_playwright

from editor_server import EditorServer

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def convert_style(action, text):
    result = subprocess.run([sys.executable, "-m", "desktop.worker.worker", "--style-" + action],
                            input=text.encode(), stdout=subprocess.PIPE, check=True, cwd=ROOT, timeout=30)
    return json.loads(result.stdout)


@unittest.skipUnless(os.getenv("JJOKGYEOL_REFERENCE_URL"), "requires a running web editor")
class DocumentInteropTest(unittest.TestCase):
    def test_web_to_desktop_to_web(self):
        self.exchange()

    @unittest.skipUnless(os.getenv("JJOKGYEOL_NATIVE_RUNTIME"), "requires the packaged native typesetter")
    def test_portable_face_retypesets_in_native_worker(self):
        self.exchange(external=True)

    @unittest.skipUnless(os.getenv("JJOKGYEOL_NATIVE_RUNTIME"), "requires the packaged native typesetter")
    def test_declined_web_face_makes_saved_proof_stale_on_native_reopen(self):
        self.exchange(external=True, consent=False)

    def exchange(self, external=False, consent=True):
        server = EditorServer()
        self.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        pdf = (ROOT / "proofs/numbering/odd-even.pdf").read_bytes()
        with sync_playwright() as p, p.chromium.launch(executable_path=os.getenv("CHROME_PATH")) as browser:
            web = browser.new_page()
            web.goto(os.environ["JJOKGYEOL_REFERENCE_URL"])
            expect(web.locator("#status")).to_have_text("최신 버전", timeout=45000)
            line = "| 구분 | 주요 내용 |\n| --- | --- |\n| 10.16. | Abel 첫 문단 |\n" if external else "첫 문단"
            source = b"\xef\xbb\xbf# " + f"원고\r\n\r\n{line}\n끝\r".encode()
            expected = f"# 원고\n\n{line}\n끝\n"
            web.locator("#file").set_input_files({"name": "초고.md", "mimeType": "text/markdown", "buffer": source})
            expect(web.locator("#filename")).to_have_text("초고.md")
            if external:
                web.locator("#open-fonts").click()
                web.locator("#font-files").set_input_files(ROOT / "tests/fixtures/fonts/abel/Abel-Regular.ttf")
                option = web.locator("#font-family option", has_text="Abel")
                expect(option).to_have_count(1)
                web.locator("#font-family").select_option(option.get_attribute("value"))
                web.locator("#apply-font").click()
                web.once("dialog", lambda dialog: dialog.accept() if consent else dialog.dismiss())
            web.locator("#refresh").click()
            expect(web.locator("#status")).to_have_text("최신 버전", timeout=45000)
            with web.expect_download() as download:
                web.locator("#save-document").click()
            archived = download.value.path()
            with ZipFile(archived) as document:
                self.assertEqual(document.read("manuscript.md"), source)
                stored = json.loads(document.read("style.json"))
                manifest = json.loads(document.read("manifest.json"))
                lock = json.loads((ROOT / "runtime.lock").read_text())
                default_hashes = {row["sha256"] for family in lock["fonts"].values() for row in family.values()}
                embedded_hashes = {row["hash"] for row in manifest["fonts"] if "file" in row}
                self.assertTrue(default_hashes <= embedded_hashes)
                self.assertEqual(len(embedded_hashes), len(default_hashes) + int(external))
                confirmed_pdf = document.read("proof.pdf")
                if external:
                    from desktop.qa.compare_pdfs import compare, read_pages
                    glyphs = [char for page in read_pages(confirmed_pdf) for row in page.lines for char in row.chars]
                    self.assertFalse(any(char.text == "|" for char in glyphs))
                    date = next(char for char in glyphs if char.text == "1")
                    cell = next(char for char in glyphs if char.text == "A")
                    self.assertAlmostEqual(date.baseline, cell.baseline, delta=.05)
                    self.assertGreater(cell.x0, date.x1)
                    latin_fonts = {char.font for char in glyphs if char.text == "A"}
                    self.assertTrue(latin_fonts)
                    self.assertEqual(any("Abel" in name for name in latin_fonts), consent, latin_fonts)
                    font = (ROOT / "tests/fixtures/fonts/abel/Abel-Regular.ttf").read_bytes()
                    self.assertTrue(any(document.read(i) == font for i in document.infolist() if i.file_size == len(font)))

            if external:
                with web.expect_download() as exported:
                    web.locator("#export").click()
                self.assertEqual(exported.value.path().read_bytes(), confirmed_pdf)

            native = browser.new_page()
            native.expose_function("convertStyle", convert_style)
            def builtin(digest):
                with urlopen(os.environ["JJOKGYEOL_REFERENCE_URL"].rstrip("/") + "/api/document-fonts/" + digest) as r:
                    return base64.b64encode(r.read()).decode()
            native.expose_function("readBuiltin", builtin)
            regenerated = []
            def render_frame(encoded):
                runtime = Path(os.environ["JJOKGYEOL_NATIVE_RUNTIME"]).resolve(strict=True)
                with tempfile.TemporaryDirectory() as temp:
                    subprocess.run([runtime / "worker/jjokgyeol-worker", "--model-proof", runtime],
                                   cwd=temp, input=base64.b64decode(encoded), check=True, timeout=45)
                    data = (Path(temp) / "measure/book.pdf").read_bytes()
                    regenerated.append(data)
                    header = json.dumps({"decision": "rule", "reason": None, "rule_bytes": len(data)}).encode()
                    return base64.b64encode(len(header).to_bytes(4, "big") + header + data).decode()
            native.expose_function("renderFrame", render_frame)
            native.add_init_script("""
                const decode = b64 => Uint8Array.from(atob(b64), c => c.charCodeAt(0)).buffer;
                window.encodeBytes = bytes => new Promise(resolve => {
                    const reader = new FileReader();
                    reader.onload = () => resolve(reader.result.split(",")[1]);
                    reader.readAsDataURL(new Blob([bytes]));
                });
                window.__TAURI__ = {core: {invoke: async (command, args) => {
                    if (command === 'style_convert') return convertStyle(args.action, args.body);
                    if (command === 'render_proof') return decode(PDF);
                    if (command === 'builtin_font') return decode(await readBuiltin(args.hash));
                    if (command === 'render_font_proof') return decode(await renderFrame(await encodeBytes(args)));
                    if (command === 'save_bytes') window.savedBytes = args;
                    return true;
                }}};
            """.replace("PDF", json.dumps(base64.b64encode(pdf).decode())))
            native.goto(f"http://127.0.0.1:{server.server_port}/index.html")
            expect(native.locator("#status")).to_have_text("최신 버전", timeout=45000)
            pages = native.locator("#desktop-pages canvas")
            expect(pages).to_have_count(2)
            native.locator("#document-file").set_input_files(
                {"name": "초고.jjok", "mimeType": "application/octet-stream", "buffer": archived.read_bytes()})
            expect(native.locator("#source")).to_have_value(expected, timeout=45000)
            expect(pages).to_have_count(1)
            expect(pages.first).to_be_visible()
            expect(native.locator("#status")).to_have_text("최신 버전" if consent else "수정 전 교정본 · 갱신 필요")
            native.locator("#save-document").click()
            native.wait_for_function("window.savedBytes instanceof Uint8Array")
            with ZipFile(BytesIO(base64.b64decode(native.evaluate("encodeBytes(window.savedBytes)")))) as document:
                self.assertEqual(document.read("proof.pdf"), confirmed_pdf)
            if external:
                native.locator("#refresh").click()
                expect(native.locator("#status")).to_have_text("최신 버전", timeout=45000)
                self.assertTrue(regenerated)
                if consent:
                    self.assertIsNone(compare(confirmed_pdf, regenerated[-1]))
                else:
                    self.assertTrue(any("Abel" in char.font for page in read_pages(regenerated[-1])
                                        for row in page.lines for char in row.chars if char.text == "A"))
            native.evaluate("delete window.savedBytes")
            native.locator("#include-proof").uncheck()
            native.locator("#save-document").click()
            native.wait_for_function("window.savedBytes instanceof Uint8Array")
            returned = base64.b64decode(native.evaluate("encodeBytes(window.savedBytes)"))
            with ZipFile(BytesIO(returned)) as document:
                self.assertEqual(document.read("manuscript.md"), source)
                self.assertEqual(json.loads(document.read("style.json")), stored)
                self.assertNotIn("proof.pdf", document.namelist())
            web.once("dialog", lambda dialog: dialog.accept())
            web.locator("#document-file").set_input_files(
                {"name": "돌아온 문서.jjok", "mimeType": "application/octet-stream", "buffer": returned})
            expect(web.locator("#include-proof")).not_to_be_checked()
            expect(web.locator("#source")).to_have_value(expected)
