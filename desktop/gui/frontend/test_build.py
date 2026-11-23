import filecmp
import json
import os
import re
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[3]
DIST = Path(__file__).parent / 'dist'

class BuildTest(unittest.TestCase):
    def test_desktop_bundle_contains_offline_editor(self):
        html = (DIST / 'index.html').read_text()
        label = os.getenv('JJOKGYEOL_RELEASE_TAG', '개발판')
        self.assertIn(f'<span class="product">원고 편집기 · {label}</span>', html)
        self.assertIn('id="editor-settings"', html)
        for marker in (
            'id="open-settings"',
            '<dialog id="settings-dialog"',
            'id="apply-settings"',
            'id="close-settings"',
            'id="save-document"',
            'id="document-file"',
            'id="include-proof"',
        ):
            self.assertIn(marker, html)
        self.assertNotIn('<!--EDITOR_SETTINGS-->', html)
        self.assertNotIn('src="/static/', html)
        for asset in re.findall(r'<script\b[^>]*\bsrc="([^"]+)"', html):
            self.assertTrue((DIST / asset).is_file(), asset)
        self.assertTrue((DIST / 'sample.md').is_file())
        self.assertTrue((DIST / 'static/pdf.worker.min.mjs').is_file())
        self.assertTrue((DIST / 'static/desktop.js').is_file())
        self.assertTrue((DIST / 'static/cmaps').is_dir())
        upstream = DIST.parent / 'node_modules/pdfjs-dist/legacy/build'
        for module in ('pdf.min.mjs', 'pdf.worker.min.mjs'):
            self.assertTrue(filecmp.cmp(DIST / 'static' / module, upstream / module, shallow=False), module)
        sys.path.insert(0, str(ROOT))
        from app.style import editor_bootstrap
        embedded = re.search(r'<script type="application/json" id="editor-settings">(.*?)</script>', html).group(1)
        self.assertEqual(json.loads(embedded), editor_bootstrap())
