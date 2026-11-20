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


class PackagedDiagnosticTest(unittest.TestCase):
    def test_fixture_is_sent_to_packaged_worker_with_opt_in(self):
        import importlib.util
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location('proof_diagnostic', ROOT / 'desktop/qa/runtime/diagnostic.py')
        diagnostic = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(diagnostic)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            runtime = base / 'runtime'
            runtime.mkdir()
            job = base / 'job'
            def invoke(args, **kwargs):
                self.assertEqual(args[1:], ['--runtime', str(runtime.resolve())])
                self.assertEqual(kwargs['cwd'], job.resolve())
                self.assertEqual(kwargs['env']['JJOKGYEOL_TEST_DIAGNOSTICS'], '1')
                request = json.loads((job / 'request.json').read_text(encoding='utf-8'))
                self.assertEqual(request['source'], (ROOT / 'proofs/numbering/manuscript.md').read_text(encoding='utf-8'))
                return subprocess.CompletedProcess(args, 7)
            with patch.object(diagnostic.subprocess, 'run', side_effect=invoke):
                self.assertEqual(diagnostic.main([str(runtime), str(job), '--packaged']), 7)
