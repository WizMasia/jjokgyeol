import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
WORKER = ROOT / 'desktop/worker/worker.py'
STYLE = json.loads((ROOT / 'proofs/numbering/odd-even.json').read_text())
spec = importlib.util.spec_from_file_location('native_worker', WORKER)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


def run(tmp_path, raw, runtime=None):
    job = tmp_path / 'job'
    job.mkdir()
    (job / 'request.json').write_bytes(raw)
    return subprocess.run([sys.executable, str(WORKER), '--runtime', str(runtime or tmp_path / 'runtime')],
                          cwd=job, env={**os.environ, 'PYTHONPATH': str(ROOT)}, capture_output=True), job


def request(style=STYLE):
    return json.dumps({'source': '글', 'style': style}).encode()


def runtime_dir(tmp_path):
    runtime = tmp_path / 'runtime'
    (runtime / 'fonts').mkdir(parents=True)
    lock = json.loads((ROOT / 'runtime.lock').read_text())
    for faces in lock['fonts'].values():
        for face in faces.values():
            (runtime / 'fonts' / PurePosixPath(face['path']).name).write_bytes(b'font')
    (runtime / 'engine').write_bytes(b'engine')
    (runtime / 'runtime.json').write_text(json.dumps({'engine': 'engine', 'font_dir': 'fonts'}))
    return runtime


def test_invalid_style_reports_field(tmp_path):
    result, job = run(tmp_path, request({**STYLE, 'body_sp': -1}))
    assert result.returncode != 0
    assert json.loads(result.stderr)['field'] == 'body_sp'
    assert not (job / 'response.pdf').exists()


def test_corrupt_runtime_does_not_expose_paths(tmp_path):
    result, job = run(tmp_path, request())
    assert result.returncode != 0
    assert json.loads(result.stderr)['code'] == 'worker_error'
    assert str(tmp_path).encode() not in result.stderr
    assert not (job / 'response.pdf').exists()


@pytest.mark.parametrize('raw', [b'{"source":', b'[' * 2000 + b'0' + b']' * 2000, b'\xff'])
def test_bad_json_is_json_error_without_traceback(tmp_path, raw):
    result, job = run(tmp_path, raw)
    assert result.returncode != 0
    assert json.loads(result.stderr)['code'] in ('invalid_input', 'worker_error')
    assert b'Traceback' not in result.stderr
    assert not (job / 'response.pdf').exists()


def test_request_read_is_bounded(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(worker, 'limit_worker_files', lambda size: None)
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'request.json').write_bytes(b'x' * (worker.LIMITS.request_body_bytes + 2))
    original = Path.open
    reads = []
    class Reader:
        def __init__(self, f): self.f = f
        def __enter__(self): return self
        def __exit__(self, *args): self.f.close()
        def read(self, size=-1):
            reads.append(size)
            return self.f.read(size)
    def tracked(path, *args, **kwargs):
        f = original(path, *args, **kwargs)
        return Reader(f) if path == Path('request.json') else f
    monkeypatch.setattr(Path, 'open', tracked)
    monkeypatch.setattr(sys, 'argv', ['worker', '--runtime', str(tmp_path)])
    assert worker.main() == 1
    assert reads == [worker.LIMITS.request_body_bytes + 1]
    assert json.loads(capsys.readouterr().err)['field'] == 'request'


@pytest.mark.parametrize('exit_code', [0, 7])
def test_worker_with_engine_process(tmp_path, monkeypatch, capsys, exit_code):
    runtime = runtime_dir(tmp_path)
    job = tmp_path / 'job'
    job.mkdir()
    (job / 'request.json').write_bytes(request())
    monkeypatch.setattr(worker, 'limit_worker_files', lambda size: None)
    monkeypatch.chdir(job)
    monkeypatch.setattr(sys, 'argv', ['worker', '--runtime', str(runtime)])
    def engine(args, *, cwd, **kwargs):
        assert Path(args[0]) == runtime / 'engine'
        assert (cwd / 'fonts').is_dir() and not (cwd / 'fonts').is_symlink()
        if exit_code == 0:
            (cwd / 'book.pdf').write_bytes(b'%PDF-1.4\n%%EOF\n')
        return subprocess.CompletedProcess(args, exit_code)
    monkeypatch.setattr(subprocess, 'run', engine)
    result = worker.main()
    if exit_code == 0:
        assert result == 0
        assert (job / 'response.pdf').read_bytes().startswith(b'%PDF-')
    else:
        assert result == 1
        assert json.loads(capsys.readouterr().err)['code'] == 'worker_error'
        assert not (job / 'response.pdf').exists()


def test_oversized_pdf_is_rejected_before_read(tmp_path, monkeypatch, capsys):
    runtime = runtime_dir(tmp_path)
    job = tmp_path / 'job'
    job.mkdir()
    (job / 'request.json').write_bytes(request())
    monkeypatch.setattr(worker, 'limit_worker_files', lambda size: None)
    monkeypatch.chdir(job)
    monkeypatch.setattr(sys, 'argv', ['worker', '--runtime', str(runtime)])
    monkeypatch.setattr(worker, 'LIMITS', type('Limits', (), {'request_body_bytes': 1048576, 'worker_file_bytes': 10})())
    def engine(args, *, cwd, **kwargs):
        (cwd / 'book.pdf').write_bytes(b'%PDF-' + b'x' * 6)
        return subprocess.CompletedProcess(args, 0)
    monkeypatch.setattr(subprocess, 'run', engine)
    original = Path.read_bytes
    def guarded(path):
        assert path.name != 'book.pdf', 'oversized PDF was read'
        return original(path)
    monkeypatch.setattr(Path, 'read_bytes', guarded)
    assert worker.main() == 1
    assert json.loads(capsys.readouterr().err)['code'] == 'worker_error'
    assert not (job / 'response.pdf').exists()


@pytest.mark.skipif(os.name != 'posix', reason='POSIX file limits only')
def test_posix_worker_caps_files_written_by_child(tmp_path):
    code = """
import resource, subprocess, sys
from desktop.worker.worker import limit_worker_files
limit_worker_files(4096)
p = subprocess.run([sys.executable, '-c', "f=open('large', 'wb'); f.write(b'x' * 8192); f.close()"])
assert p.returncode != 0
assert __import__('os').stat('large').st_size <= 4096
"""
    result = subprocess.run([sys.executable, '-c', code], cwd=tmp_path,
                            env={**os.environ, 'PYTHONPATH': str(ROOT)}, capture_output=True)
    assert result.returncode == 0, result.stderr.decode()


def style_cli(tmp_path, mode, raw):
    return subprocess.run([sys.executable, str(WORKER), mode], input=raw, cwd=tmp_path,
                          env={**os.environ, 'PYTHONPATH': str(ROOT)}, capture_output=True)


def test_style_open_export_roundtrip_with_bom(tmp_path):
    opened = style_cli(tmp_path, '--style-open', b'\xef\xbb\xbf' + json.dumps(STYLE).encode())
    assert opened.returncode == 0, opened.stderr.decode()
    editor = json.loads(opened.stdout)
    exported = style_cli(tmp_path, '--style-export', json.dumps(editor).encode())
    assert exported.returncode == 0, exported.stderr.decode()
    from app.style import from_stored_style
    assert json.loads(exported.stdout) == from_stored_style(STYLE).to_stored()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(('mode', 'raw'), [
    ('--style-open', b'[]'),
    ('--style-open', b'{"body_sp": -1}'),
    ('--style-export', b'{"body_pt": "not a number"}'),
    ('--style-export', b'{'),
    ('--style-open', b'[' * 2000 + b'0' + b']' * 2000),
    ('--style-open', b' ' * (1048576 + 1)),
])
def test_style_invalid_input_returns_json_error(tmp_path, mode, raw):
    result = style_cli(tmp_path, mode, raw)
    assert result.returncode != 0
    error = json.loads(result.stderr)
    assert error['code'] in ('invalid_input', 'worker_error')
    assert b'Traceback' not in result.stderr
    assert result.stdout == b''


def test_style_modes_are_mutually_exclusive(tmp_path):
    result = subprocess.run([sys.executable, str(WORKER), '--style-open', '--style-export'],
                            input=b'{}', cwd=tmp_path, env={**os.environ, 'PYTHONPATH': str(ROOT)}, capture_output=True)
    assert result.returncode != 0
    assert json.loads(result.stderr)['code'] == 'worker_error'


def test_locked_font_paths_use_posix_syntax_on_windows(monkeypatch):
    from pathlib import PureWindowsPath
    from app import publication
    choices = json.loads((ROOT / 'app/config/publication.json').read_text())['fonts']
    lock = json.loads((ROOT / 'runtime.lock').read_text())['fonts']
    monkeypatch.setattr(publication, 'Path', PureWindowsPath)
    labels, faces, directory = publication._load_fonts(choices, lock)
    assert labels['nanum-myeongjo']
    assert faces['nanum-myeongjo']['regular'] == 'NanumMyeongjo.ttf'
    assert directory == PureWindowsPath('/usr/share/fonts/truetype/nanum')
    assert isinstance(directory, PureWindowsPath)


def test_frozen_windows_restores_dll_search(monkeypatch):
    import ctypes
    calls = []
    class Kernel:
        def SetDllDirectoryW(self, value):
            calls.append(value)
            return 1
    class Windll:
        kernel32 = Kernel()
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(ctypes, 'windll', Windll(), raising=False)
    worker.prepare_external_engine()
    assert calls == [None]


@pytest.mark.parametrize(('fixture', 'enabled'), [(True, True), (True, False), (False, True)])
def test_diagnostics_are_limited_to_fixture(tmp_path, monkeypatch, fixture, enabled):
    runtime = runtime_dir(tmp_path)
    job = tmp_path / 'job'
    job.mkdir()
    source = (ROOT / 'proofs/numbering/manuscript.md').read_text(encoding='utf-8') if fixture else 'private source'
    (job / 'request.json').write_text(json.dumps({'source': source, 'style': STYLE}), encoding='utf-8')
    monkeypatch.chdir(job)
    if enabled:
        monkeypatch.setenv('JJOKGYEOL_TEST_DIAGNOSTICS', '1')
    else:
        monkeypatch.delenv('JJOKGYEOL_TEST_DIAGNOSTICS', raising=False)
    monkeypatch.setattr(worker, 'limit_worker_files', lambda size: None)
    monkeypatch.setattr(sys, 'argv', ['worker', '--runtime', str(runtime)])
    def engine(args, *, cwd, **kwargs):
        (cwd / 'book.log').write_text('synthetic failure')
        return subprocess.CompletedProcess(args, 7)
    monkeypatch.setattr(subprocess, 'run', engine)
    assert worker.main() == 1
    if fixture and enabled:
        assert (job / 'diagnostic-book.log').read_text() == 'synthetic failure'
        assert 'CalledProcessError' in (job / 'diagnostic-exception.txt').read_text()
    else:
        assert not list(job.glob('diagnostic-*'))


def test_tex_paths_strip_extended_windows_prefix():
    from pathlib import PureWindowsPath
    from app.typeset import _tex_path
    assert _tex_path(PureWindowsPath(r'\\?\C:\Program Files\worker\book.tex')) == 'C:/Program Files/worker/book.tex'
    assert _tex_path(PureWindowsPath(r'\\?\UNC\server\share\book.tex')) == '//server/share/book.tex'
