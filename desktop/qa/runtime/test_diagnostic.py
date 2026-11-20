import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


class DiagnosticTest(unittest.TestCase):
    @unittest.skipIf(os.name == 'nt', 'test engine is a POSIX script')
    def test_failed_engine_keeps_input_and_log(self):
        from app.publication import engine_policy
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            runtime = base / 'runtime'
            fonts = runtime / 'fonts'
            fonts.mkdir(parents=True)
            for faces in engine_policy()['fonts'].values():
                for name in faces.values():
                    (fonts / name).write_bytes(b'fake font')
            engine = runtime / 'engine'
            engine.write_text('#!/bin/sh\nprintf "synthetic TeX error\\n" > book.log\nexit 1\n')
            engine.chmod(0o755)
            (runtime / 'runtime.json').write_text(json.dumps({'engine': 'engine', 'font_dir': 'fonts'}))
            job = base / 'job'
            result = subprocess.run([sys.executable, str(ROOT / 'desktop/qa/runtime/diagnostic.py'), str(runtime), str(job)], cwd=ROOT, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('synthetic TeX error', (job / 'book.log').read_text())
            self.assertIn('blocks', json.loads((job / 'input.json').read_text(encoding='utf-8')))
            self.assertIn('CalledProcessError', result.stderr)
