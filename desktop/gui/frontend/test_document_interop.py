"""Round-trip a working document through a running web editor and the built desktop UI."""
import base64
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from zipfile import ZipFile

from playwright.sync_api import expect, sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def convert_style(action, text):
    result = subprocess.run([sys.executable, "-m", "desktop.worker.worker", "--style-" + action],
                            input=text.encode(), stdout=subprocess.PIPE, check=True, cwd=ROOT, timeout=30)
    return json.loads(result.stdout)


@unittest.skipUnless(os.getenv("JJOKGYEOL_REFERENCE_URL"), "requires a running web editor")
class DocumentInteropTest(unittest.TestCase):
    def test_web_to_desktop_to_web(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=HERE / "dist"))
        self.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        pdf = (ROOT / "proofs/numbering/odd-even.pdf").read_bytes()
        with sync_playwright() as p, p.chromium.launch(executable_path=os.getenv("CHROME_PATH")) as browser:
            web = browser.new_page()
            web.goto(os.environ["JJOKGYEOL_REFERENCE_URL"])
            expect(web.locator("#status")).to_have_text("최신 버전", timeout=45000)
            source = b"\xef\xbb\xbf# " + "원고\r\n\r\n첫 문단\n끝\r".encode()
            web.locator("#file").set_input_files({"name": "초고.md", "mimeType": "text/markdown", "buffer": source})
            expect(web.locator("#filename")).to_have_text("초고.md")
            web.locator("#refresh").click()
            expect(web.locator("#status")).to_have_text("최신 버전", timeout=45000)
            with web.expect_download() as download:
                web.locator("#save-document").click()
            archived = download.value.path()
            with ZipFile(archived) as document:
                self.assertEqual(document.read("manuscript.md"), source)
                stored = json.loads(document.read("style.json"))
                confirmed_pdf = document.read("proof.pdf")

            native = browser.new_page()
            native.expose_function("convertStyle", convert_style)
            native.add_init_script("""
                window.__TAURI__ = {core: {invoke: async (command, args) => {
                    if (command === 'style_convert') return convertStyle(args.action, args.body);
                    if (command === 'render_proof') return Uint8Array.from(atob(PDF), c => c.charCodeAt(0)).buffer;
                    if (command === 'save_bytes') window.savedBytes = Array.from(args);
                    return true;
                }}};
            """.replace("PDF", json.dumps(base64.b64encode(pdf).decode())))
            native.goto(f"http://127.0.0.1:{server.server_port}/index.html")
            expect(native.locator("#status")).to_have_text("최신 버전", timeout=45000)
            pages = native.locator("#desktop-pages canvas")
            expect(pages).to_have_count(2)
            native.locator("#document-file").set_input_files(
                {"name": "초고.jjok", "mimeType": "application/octet-stream", "buffer": archived.read_bytes()})
            expect(native.locator("#source")).to_have_value("# 원고\n\n첫 문단\n끝\n")
            expect(pages).to_have_count(1)
            expect(pages.first).to_be_visible()
            native.locator("#save-document").click()
            native.wait_for_function("Array.isArray(window.savedBytes)")
            with ZipFile(BytesIO(bytes(native.evaluate("window.savedBytes")))) as document:
                self.assertEqual(document.read("proof.pdf"), confirmed_pdf)
            native.evaluate("delete window.savedBytes")
            native.locator("#include-proof").uncheck()
            native.locator("#save-document").click()
            native.wait_for_function("Array.isArray(window.savedBytes)")
            returned = bytes(native.evaluate("window.savedBytes"))
            with ZipFile(BytesIO(returned)) as document:
                self.assertEqual(document.read("manuscript.md"), source)
                self.assertEqual(json.loads(document.read("style.json")), stored)
                self.assertNotIn("proof.pdf", document.namelist())
            web.once("dialog", lambda dialog: dialog.accept())
            web.locator("#document-file").set_input_files(
                {"name": "돌아온 문서.jjok", "mimeType": "application/octet-stream", "buffer": returned})
            expect(web.locator("#include-proof")).not_to_be_checked()
            expect(web.locator("#source")).to_have_value("# 원고\n\n첫 문단\n끝\n")
