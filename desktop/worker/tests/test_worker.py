import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
WORKER = ROOT / 'desktop/worker/worker.py'
STYLE = json.loads((ROOT / 'proofs/numbering/odd-even.json').read_text())


def run(tmp_path, request, runtime=None):
    job = tmp_path / 'job'
    job.mkdir()
    (job / 'request.json').write_text(json.dumps(request), encoding='utf-8')
    return subprocess.run([sys.executable, str(WORKER), '--runtime', str(runtime or tmp_path / 'runtime')],
                          cwd=job, env={**os.environ, 'PYTHONPATH': str(ROOT), 'PATH': ''}, capture_output=True), job


def test_invalid_style_reports_field(tmp_path):
    result, job = run(tmp_path, {'source': '글', 'style': {**STYLE, 'body_sp': -1}})
    assert result.returncode != 0
    assert json.loads(result.stderr)['field'] == 'body_sp'
    assert not (job / 'response.pdf').exists()


def test_corrupt_runtime_fails(tmp_path):
    result, job = run(tmp_path, {'source': '글', 'style': STYLE})
    assert result.returncode != 0
    assert not (job / 'response.pdf').exists()


def test_native_engine_and_fonts_without_symlinks(tmp_path):
    runtime = tmp_path / 'runtime'
    (runtime / 'fonts').mkdir(parents=True)
    lock = json.loads((ROOT / 'runtime.lock').read_text())
    for faces in lock['fonts'].values():
        for face in faces.values():
            (runtime / 'fonts' / Path(face['path']).name).write_bytes(b'font')
    engine = runtime / 'engine'
    engine.write_text('#!/bin/sh\ntest ! -L fonts || exit 2\nprintf "%%PDF-1.4\\n%%%%EOF\\n" > book.pdf\n')
    engine.chmod(0o755)
    (runtime / 'runtime.json').write_text(json.dumps({'engine': 'engine', 'font_dir': 'fonts'}))
    result, job = run(tmp_path, {'source': '글', 'style': STYLE}, runtime)
    assert result.returncode == 0, result.stderr.decode()
    assert (job / 'response.pdf').read_bytes().startswith(b'%PDF-')


def test_failed_engine_reports_json(tmp_path):
    runtime = tmp_path / 'runtime'
    (runtime / 'fonts').mkdir(parents=True)
    lock = json.loads((ROOT / 'runtime.lock').read_text())
    for faces in lock['fonts'].values():
        for face in faces.values():
            (runtime / 'fonts' / Path(face['path']).name).write_bytes(b'font')
    engine = runtime / 'engine'
    engine.write_text('#!/bin/sh\nexit 7\n')
    engine.chmod(0o755)
    (runtime / 'runtime.json').write_text(json.dumps({'engine': 'engine', 'font_dir': 'fonts'}))
    result, job = run(tmp_path, {'source': '글', 'style': STYLE}, runtime)
    assert result.returncode != 0
    assert json.loads(result.stderr)['code'] == 'worker_error'
    assert not (job / 'response.pdf').exists()
