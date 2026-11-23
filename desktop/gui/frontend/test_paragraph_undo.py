"""macOS WebKit must keep a paragraph command separate from adjacent typing."""
import os
from pathlib import Path
import sys
import threading
import unittest

from playwright.sync_api import expect, sync_playwright

from editor_server import EditorServer

ROOT = Path(__file__).resolve().parents[3]


@unittest.skipUnless(sys.platform == "darwin", "requires macOS WebKit undo grouping")
class ParagraphUndoTest(unittest.TestCase):
    def test_typing_and_each_alignment_remain_separate(self):
        server = EditorServer()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        with sync_playwright() as p, p.webkit.launch(executable_path=os.getenv("JJOKGYEOL_WEBKIT_PATH")) as browser:
            page = browser.new_page()
            sys.path.insert(0, str(ROOT))
            self.addCleanup(sys.path.remove, str(ROOT))
            from app.paragraph_edit import edit_alignment
            page.expose_function("nativeAlign", edit_alignment)
            page.add_init_script("""window.__TAURI__ = {core: {invoke: async (command, args) => {
              if (command === 'paragraph_align') return window.nativeAlign(args.payload);
              if (command === 'style_convert') return JSON.parse(args.body);
              if (command === 'render_proof') throw 'no preview';
              return true;
            }}};""")
            page.goto(f"http://127.0.0.1:{server.server_port}/index.html")
            source = page.locator("#source")
            page.wait_for_function("document.querySelector('#source').value.length > 0")
            sample = source.input_value()
            text = "# 보고서\n\n작성일과 작성자\n\n일반 본문"
            source.fill(text)
            source.evaluate("el => {el.focus(); el.setSelectionRange(8, 9)}")
            chooser = page.locator("#paragraph-align")
            chooser.focus()
            chooser.select_option("right")
            right = text.replace("작성일", "<!-- 쪽결 정렬=오른쪽 -->\n작성일")
            expect(source).to_have_value(right)
            source.press("x")
            source.press("Meta+z")
            expect(source).to_have_value(right)
            source.press("Meta+z")
            expect(source).to_have_value(text)
            source.press("Meta+z")
            expect(source).to_have_value(sample)
            source.press("Meta+Shift+z")
            expect(source).to_have_value(text)
            source.press("Meta+Shift+z")
            expect(source).to_have_value(right)
            source.evaluate("el => el.setSelectionRange(el.value.indexOf('작성일'), el.value.indexOf('작성일'))")
            chooser.select_option("center")
            center = right.replace("정렬=오른쪽", "정렬=가운데")
            expect(source).to_have_value(center)
            chooser.select_option("")
            expect(source).to_have_value(text)
            source.press("Meta+z")
            expect(source).to_have_value(center)
            source.press("Meta+z")
            expect(source).to_have_value(right)
