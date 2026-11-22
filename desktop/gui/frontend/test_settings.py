"""Exercise the settings entry points through the bundled editor."""
import json
import os
import threading
import unittest

from playwright.sync_api import sync_playwright

from editor_server import EditorServer


class SettingsTest(unittest.TestCase):
    def test_native_snapshot_apply_and_child_isolation(self):
        server = EditorServer()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=os.environ.get('CHROME_PATH', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'), headless=True)
                page = browser.new_page()
                page.set_default_timeout(5000)
                page.add_init_script('''
                  window.calls = []; window.handler = null;
                  window.__TAURI__ = {event: {listen: async (name, cb) => { window.handler = cb; }},
                    core: {invoke: async (command, args) => {
                      window.calls.push({command, args});
                      if (command === 'style_convert') return JSON.parse(args.body);
                      if (command === 'render_proof') throw 'no render';
                      if (command === 'settings_snapshot') return window.snapshot;
                      if (command === 'confirm_settings_replacement') return false;
                      return true;
                    }}};
                ''')
                url = f'http://127.0.0.1:{server.server_port}/index.html'
                page.goto(url)
                page.wait_for_function("document.querySelector('#source').value.length > 0")
                page.locator('#source').fill('unsaved manuscript')
                page.locator('#open-settings').click()
                page.wait_for_function("window.calls.some(c => c.command === 'open_settings_window')")
                snapshot = page.evaluate("window.calls.find(c => c.command === 'open_settings_window').args.snapshot")
                self.assertEqual(page.evaluate('typeof window.handler'), 'function')
                self.assertEqual(snapshot['style']['body_pt'], '11')
                page.evaluate("snapshot => window.handler({payload: {...snapshot, style: {...snapshot.style, body_pt: '15'}, dirty: true}})", snapshot)
                self.assertEqual(page.locator('#body_pt').input_value(), '15')
                self.assertEqual(page.locator('#source').input_value(), 'unsaved manuscript')
                self.assertTrue(page.evaluate("window.calls.filter(c => c.command === 'set_dirty').at(-1).args.dirty"))

                child = browser.new_page()
                child.set_default_timeout(5000)
                errors = []
                child.on('pageerror', lambda error: errors.append(str(error)))
                child.add_init_script('window.snapshot = ' + json.dumps(snapshot, ensure_ascii=False))
                child.add_init_script('''
                  window.calls = []; window.__TAURI__ = {core: {invoke: async (command, args) => {
                    window.calls.push({command, args});
                    if (command === 'settings_snapshot') return window.snapshot;
                    if (command === 'style_convert') {
                      const value = JSON.parse(args.body);
                      if (value.body_pt === '0') throw JSON.stringify({field:'body_pt', message:'invalid size'});
                      return value;
                    }
                    return true;
                  }}};
                ''')
                requests = []
                child.on('request', lambda request: requests.append(request.url))
                child.goto(url + '?view=settings')
                child.wait_for_function("window.calls.some(c => c.command === 'settings_snapshot')")
                child.wait_for_function("document.querySelector('#body_pt').value === '11'")
                child.locator('#body_pt').fill('0')
                child.locator('#apply-settings').click()
                child.wait_for_timeout(100)
                self.assertEqual(errors, [])
                child.wait_for_function("document.querySelector('#settings-error').textContent.includes('invalid size')")
                self.assertFalse(child.evaluate("window.calls.some(c => c.command === 'apply_settings')"))
                child.locator('#body_pt').fill('14')
                child.locator('#apply-settings').click()
                child.wait_for_function("window.calls.some(c => c.command === 'apply_settings')")
                self.assertEqual(child.evaluate("window.calls.find(c => c.command === 'apply_settings').args.snapshot.style.body_pt"), '14')
                self.assertFalse(child.evaluate("window.calls.some(c => ['render_proof', 'set_dirty', 'set_style_dirty'].includes(c.command))"))
                self.assertFalse(any(url.endswith('/sample.md') for url in requests))
                browser.close()
        finally:
            server.shutdown()
            server.server_close()

    def test_child_does_not_apply_while_style_save_is_pending(self):
        server = EditorServer()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(executable_path=os.environ.get('CHROME_PATH', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'), headless=True)
                page = browser.new_page()
                page.set_default_timeout(5000)
                page.add_init_script('''
                  window.calls = []; window.finishSave = null;
                  window.__TAURI__ = {core: {invoke: async (command, args) => {
                    window.calls.push({command, args});
                    if (command === 'settings_snapshot') return {style: window.initialStyle, dirty: false, baseRevision: 0};
                    if (command === 'style_convert') return JSON.parse(args.body);
                    if (command === 'save_bytes') return await new Promise(r => window.finishSave = r);
                    return true;
                  }}};
                ''')
                # Capture a complete editor style from a main window before opening the child.
                main = browser.new_page()
                main.add_init_script('''window.__TAURI__ = {core: {invoke: async (c, a) => {
                  if (c === 'style_convert') return JSON.parse(a.body); if (c === 'render_proof') throw 'no render';
                }}}''')
                url = f'http://127.0.0.1:{server.server_port}/index.html'
                main.goto(url)
                main.wait_for_function("document.querySelector('#source').value.length > 0")
                style = main.evaluate('styleForm.get()')
                page.add_init_script('window.initialStyle = ' + json.dumps(style, ensure_ascii=False))
                page.goto(url + '?view=settings')
                page.wait_for_function("window.calls.some(c => c.command === 'settings_snapshot') && document.querySelector('#body_pt').value === '11' && !document.querySelector('#apply-settings').disabled")
                page.locator('#body_pt').fill('14')
                page.locator('#style summary').click()
                page.locator('#save-style').click()
                page.wait_for_function('window.finishSave !== null')
                page.locator('#apply-settings').click()
                page.wait_for_timeout(100)
                self.assertFalse(page.evaluate("window.calls.some(c => c.command === 'apply_settings')"))
                page.evaluate('window.finishSave(true)')
                page.wait_for_function("document.querySelector('#apply-settings').disabled === false")
                page.locator('#apply-settings').click()
                page.wait_for_function("window.calls.some(c => c.command === 'apply_settings')")
                self.assertFalse(page.evaluate("window.calls.find(c => c.command === 'apply_settings').args.snapshot.dirty"))
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
