"""Build the offline desktop editor without importing the web server."""
import json
import os
from pathlib import Path
import shutil
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from app.style import editor_bootstrap

out = HERE / 'dist'
shutil.rmtree(out, ignore_errors=True)
shutil.copytree(ROOT / 'app/static', out / 'static')
shutil.copy(ROOT / 'proofs/engine-comparison/manuscript.md', out / 'sample.md')
shutil.copy(HERE / 'desktop.js', out / 'static/desktop.js')
shutil.copy(HERE / 'desktop.css', out / 'static/desktop.css')
html = (ROOT / 'app/static/index.html').read_text(encoding='utf-8')
tag = os.environ.get('JJOKGYEOL_RELEASE_TAG')
version = json.loads((HERE.parent / 'tauri.conf.json').read_text(encoding='utf-8'))['version']
if tag is not None and tag != 'v' + version:
    raise ValueError('release tag must match the configured application version')
html = html.replace('<!--APP_VERSION-->', ' · ' + (tag or '개발판'))
marker = '<!--EDITOR_SETTINGS-->'
if html.count(marker) != 1:
    raise ValueError('editor settings marker must appear exactly once')
settings = json.dumps(editor_bootstrap(), allow_nan=False).replace('<', r'\u003c')
html = html.replace(marker, f'<script type="application/json" id="editor-settings">{settings}</script>\n  <link rel="stylesheet" href="static/desktop.css">\n  <script src="static/desktop.js" defer></script>')
html = html.replace('/static/', 'static/').replace('<script type="module" src="static/style-suggestion.js"></script>', '')
(out / 'index.html').write_text(html, encoding='utf-8')
