"""Browser integration against a local fixture server, never used by the app."""
import base64
import json
import os
from pathlib import Path
import threading
import unittest

from playwright.sync_api import sync_playwright

from editor_server import EditorServer

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]

class DesktopTest(unittest.TestCase):
    def test_shared_dock_menu_and_source_formatting(self):
        server = EditorServer()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=os.environ.get('CHROME_PATH'), headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.add_init_script("""
                  window.calls = [];
                  window.__TAURI__ = {core: {invoke: async (command, args) => {
                    window.calls.push({command, args});
                    if (command === 'style_convert') return {};
                    if (command === 'render_proof') throw 'no render';
                    if (command === 'paragraph_align') return {source: args.payload.source, supported: true,
                      start_line: 0, align: null, leading_pt: null, space_before_pt: null, space_after_pt: null};
                    return true;
                  }}};
                """)
                page.goto(f'http://127.0.0.1:{server.server_port}/index.html')
                page.wait_for_function("document.querySelector('#source').value.length > 0")
                source = page.locator('#source')
                text = "첫 문장"
                source.fill(text)
                source.evaluate("el => { el.focus(); el.setSelectionRange(0, 1); }")
                page.locator('#format-bold').click()
                self.assertEqual(source.input_value(), '**첫** 문장')
                source.press('ControlOrMeta+z')
                self.assertEqual(source.input_value(), text)
                self.assertFalse(page.locator('#paragraph-dock').is_visible())
                source_region = page.locator('.sheet').bounding_box()
                proof_region = page.locator('.proof-stage').bounding_box()
                page.locator('#toggle-paragraph-dock').click()
                self.assertTrue(page.locator('#paragraph-dock').is_visible())
                page.wait_for_function("window.calls.some(c => c.command === 'paragraph_align' && c.args.payload.leading === true)")
                page.locator('#paragraph-leading-mode').select_option('manual')
                page.locator('#paragraph-leading-value').fill('19')
                page.locator('#paragraph-leading-value').press('Enter')
                page.wait_for_function("window.calls.some(c => c.command === 'paragraph_align' && c.args.payload.leading_pt === '19')")
                self.assertEqual(source_region['y'], page.locator('.sheet').bounding_box()['y'])
                self.assertEqual(proof_region['y'], page.locator('.proof-stage').bounding_box()['y'])
                self.assertEqual(page.locator('#status').evaluate("el => el.parentElement.id"), 'app-status-bar')
                self.assertFalse(page.locator('[data-command="import-source"]').is_visible())
                menu = page.locator('.app-menu summary').first
                menu.click()
                menu.press('ArrowDown')
                self.assertTrue(page.get_by_role('menuitem', name='원고 열기').evaluate('el => el === document.activeElement'))
                page.keyboard.press('ArrowDown')
                self.assertTrue(page.get_by_role('menuitem', name='원고 저장').evaluate('el => el === document.activeElement'))
                page.keyboard.press('Escape')
                self.assertTrue(menu.evaluate('el => el === document.activeElement'))
                browser.close()
        finally:
            server.shutdown()
            server.server_close()

    def test_initial_pdf_save_and_late_render(self):
        pdf = (ROOT / 'proofs/numbering/odd-even.pdf').read_bytes()
        server = EditorServer()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=os.environ.get('CHROME_PATH'), headless=True)
                page = browser.new_page()
                page.add_init_script('''
                    window.calls = []; window.pendingRender = null;
                    window.__TAURI__ = {core: {invoke: async (command, args, options) => {
                      window.calls.push({command, args, options});
                      if (command === 'style_convert') return {stored: true};
                      if (command === 'render_proof') {
                        if (args.source === 'old') await new Promise(resolve => window.pendingRender = resolve);
                        return Uint8Array.from(atob(PDF), c => c.charCodeAt(0)).buffer;
                      }
                    }}};
                '''.replace('PDF', json.dumps(base64.b64encode(pdf).decode())))
                requests = []
                page.on('request', lambda request: requests.append(request.url))
                page.goto(f'http://127.0.0.1:{server.server_port}/index.html')
                page.wait_for_selector('#desktop-pages canvas', timeout=30000)
                self.assertGreater(page.locator('#source').input_value().__len__(), 0)
                self.assertEqual(page.locator('#desktop-pages canvas').count(), 2)
                self.assertEqual(page.locator('#desktop-pages canvas').first.get_attribute('aria-label'), '1쪽')
                self.assertTrue(page.locator('#desktop-pages canvas').first.evaluate("c => { const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; return d.some((v, i) => i % 4 === 3 && v > 0); }"))
                self.assertEqual(page.locator('#proof-choice option').count(), 1)
                page.click('#export')
                page.wait_for_function("window.calls.some(c => c.command === 'save_bytes')")
                saved = page.evaluate("""() => {
                    const call = window.calls.find(c => c.command === 'save_bytes');
                    return {
                        isUint8Array: call.args instanceof Uint8Array,
                        bytes: Array.from(call.args),
                        name: decodeURIComponent(call.options?.headers?.['x-file-name'] || ''),
                    };
                }""")
                self.assertTrue(saved['isUint8Array'])
                self.assertEqual(bytes(saved['bytes']), pdf)
                self.assertTrue(saved['name'].endswith('.pdf'))
                page.locator('#source').fill('old')
                page.click('#refresh')
                page.wait_for_function('window.pendingRender !== null')
                page.locator('#source').fill('new')
                page.click('#refresh')
                page.wait_for_function("document.querySelector('#status').textContent === '최신 버전'")
                page.evaluate('window.pendingRender()')
                page.wait_for_timeout(200)
                self.assertEqual(page.locator('#status').text_content(), '최신 버전')
                self.assertEqual(page.locator('#desktop-pages canvas').count(), 2)
                self.assertFalse(any('/api/' in url for url in requests))
                browser.close()
        finally:
            server.shutdown()
            server.server_close()

    def test_dirty_open_and_save_outcomes(self):
        server = EditorServer()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=os.environ.get('CHROME_PATH'), headless=True)
                page = browser.new_page()
                page.add_init_script("""
                  window.calls = []; window.saveResult = false; window.confirmResult = false;
                  window.confirm = () => window.confirmResult;
                  window.__TAURI__ = {event: {listen: async (name, fn) => { window.applied = fn; }}, core: {invoke: async (command, args) => {
                    window.calls.push({command, args});
                    if (command === 'style_convert') return {};
                    if (command === 'confirm_document_replacement') return window.confirmResult;
                    if (command === 'confirm_style_replacement') return window.confirmResult;
                    if (command === 'render_proof') throw 'no render';
                    if (command === 'save_bytes') {
                      if (window.saveResult === 'wait') return await new Promise(r => window.finishSave = r);
                      if (window.saveResult === 'error') throw 'disk error';
                      return window.saveResult;
                    }
                  }}};
                """)
                page.goto(f'http://127.0.0.1:{server.server_port}/index.html')
                page.wait_for_function("document.querySelector('#source').value.length > 0")
                page.locator('#source').fill('edited')
                page.wait_for_function("window.calls.some(c => c.command === 'set_dirty' && c.args.dirty)", timeout=3000)
                page.locator('#file').set_input_files({'name': 'next.md', 'mimeType': 'text/markdown', 'buffer': b'next'})
                page.wait_for_function("window.calls.some(c => c.command === 'confirm_document_replacement')")
                page.wait_for_timeout(50)
                self.assertEqual(page.locator('#source').input_value(), 'edited')
                page.click('#open-settings')
                page.wait_for_function("window.calls.some(c => c.command === 'open_settings_window')")
                page.evaluate("""() => {
                    const snapshot = window.calls.filter(c => c.command === 'open_settings_window').at(-1).args.snapshot;
                    window.applied({payload: {...snapshot, dirty: true, style: {...snapshot.style, body_pt: '15'}}});
                }""")
                page.wait_for_function("window.calls.filter(c => c.command === 'set_style_dirty').at(-1)?.args.dirty === true")
                page.click('#open-settings')
                page.wait_for_function("window.calls.filter(c => c.command === 'open_settings_window').length === 2")
                page.evaluate("""() => {
                    const saved = window.calls.filter(c => c.command === 'open_settings_window').at(-1).args.snapshot;
                    window.applied({payload: {...saved, dirty: false}});
                }""")
                page.wait_for_function("window.calls.filter(c => c.command === 'set_style_dirty').at(-1)?.args.dirty === false")
                self.assertTrue(page.evaluate("window.calls.filter(c => c.command === 'set_dirty').at(-1).args.dirty"))
                page.evaluate('window.saveResult = false')
                page.click('#save')
                page.wait_for_function("window.calls.some(c => c.command === 'save_bytes')")
                self.assertTrue(page.evaluate("window.calls.filter(c => c.command === 'set_dirty').at(-1).args.dirty"))
                page.evaluate("window.saveResult = 'wait'")
                page.click('#save')
                page.wait_for_function('window.finishSave !== undefined')
                page.locator('#source').fill('newer')
                page.evaluate('window.finishSave(true)')
                page.wait_for_timeout(100)
                self.assertTrue(page.evaluate("window.calls.filter(c => c.command === 'set_dirty').at(-1).args.dirty"))
                page.evaluate("window.saveResult = 'error'")
                page.click('#save')
                page.wait_for_function("document.querySelector('#error').textContent.includes('disk error')")
                self.assertTrue(page.evaluate("window.calls.filter(c => c.command === 'set_dirty').at(-1).args.dirty"))
                page.evaluate('window.saveResult = true')
                page.click('#save')
                page.wait_for_function("window.calls.filter(c => c.command === 'set_dirty').at(-1).args.dirty === false")
                page.locator('#source').fill('edited again')
                page.evaluate('window.confirmResult = true')
                page.locator('#file').set_input_files({'name': 'next.md', 'mimeType': 'text/markdown', 'buffer': b'next'})
                page.wait_for_function("document.querySelector('#source').value === 'next'")
                page.wait_for_function("window.calls.filter(c => c.command === 'set_dirty').at(-1).args.dirty === false")
                browser.close()
        finally:
            server.shutdown()
            server.server_close()

    def test_replacement_waits_for_pending_dirty_clear(self):
        server = EditorServer()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=os.environ.get('CHROME_PATH'), headless=True)
                page = browser.new_page()
                page.add_init_script("""
                  window.calls = []; window.saveResult = true; window.stallClear = false; window.failNextDirty = false;
                  window.nativeDirty = false; window.finishClear = null; window.confirmDirtyAtCall = null;
                  window.__TAURI__ = {core: {invoke: async (command, args) => {
                    window.calls.push({command, args});
                    if (command === 'style_convert') return {};
                    if (command === 'render_proof') throw 'no render';
                    if (command === 'save_bytes') return window.saveResult;
                    if (command === 'set_dirty') {
                      if (args.dirty && window.failNextDirty) {
                        window.failNextDirty = false;
                        throw 'IPC failure';
                      }
                      if (!args.dirty && window.stallClear) {
                        return await new Promise(resolve => window.finishClear = () => {
                          window.nativeDirty = false;
                          resolve(true);
                        });
                      }
                      window.nativeDirty = args.dirty;
                      return true;
                    }
                    if (command === 'confirm_document_replacement') {
                      window.confirmDirtyAtCall = window.nativeDirty;
                      return false;
                    }
                  }}};
                """)
                page.goto(f'http://127.0.0.1:{server.server_port}/index.html')
                page.wait_for_function("document.querySelector('#source').value.length > 0")
                self.assertFalse(page.evaluate("window.calls.some(c => c.command === 'set_dirty')"))
                page.locator('#source').fill('before save')
                page.wait_for_function('window.nativeDirty === true')
                page.evaluate('window.stallClear = true')
                page.click('#save')
                page.wait_for_function('window.finishClear !== null')
                page.locator('#source').fill('after save')
                page.locator('#file').set_input_files({'name': 'next.md', 'mimeType': 'text/markdown', 'buffer': b'next'})
                page.wait_for_timeout(100)
                self.assertFalse(page.evaluate("window.calls.some(c => c.command === 'confirm_document_replacement')"))
                page.evaluate('window.finishClear()')
                page.wait_for_function("window.calls.some(c => c.command === 'confirm_document_replacement')")
                self.assertTrue(page.evaluate('window.nativeDirty'))
                self.assertTrue(page.evaluate('window.confirmDirtyAtCall'))
                self.assertEqual(page.locator('#source').input_value(), 'after save')
                page.evaluate('window.stallClear = false')
                page.click('#save')
                page.wait_for_function('window.nativeDirty === false')
                page.evaluate('window.failNextDirty = true')
                page.locator('#source').fill('edit after IPC failure')
                page.wait_for_function("document.querySelector('#error').textContent.includes('IPC failure')")
                page.locator('#file').set_input_files({'name': 'last.md', 'mimeType': 'text/markdown', 'buffer': b'last'})
                page.wait_for_function("window.calls.filter(c => c.command === 'confirm_document_replacement').length === 2")
                self.assertTrue(page.evaluate('window.nativeDirty'))
                self.assertTrue(page.evaluate('window.confirmDirtyAtCall'))
                self.assertEqual(page.locator('#source').input_value(), 'edit after IPC failure')
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
