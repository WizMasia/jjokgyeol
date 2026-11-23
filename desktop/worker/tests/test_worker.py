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


def test_redirected_cp949_stderr_preserves_manuscript_error(tmp_path, monkeypatch):
    monkeypatch.setenv('PYTHONIOENCODING', 'cp949')
    raw = json.dumps({'source': '[^주]: 하나\n[^주]: 둘', 'style': STYLE}).encode()
    result, _ = run(tmp_path, raw)
    assert result.returncode == 1
    assert result.stderr.isascii()
    assert json.loads(result.stderr) == {'code': 'invalid_input', 'field': 'source',
                                         'message': '각주 정의가 중복되었습니다.'}


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
soft_fsize, hard_fsize = resource.getrlimit(resource.RLIMIT_FSIZE)
assert soft_fsize <= 4096 and hard_fsize <= 4096
assert resource.getrlimit(resource.RLIMIT_CORE) == (0, 0)
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
    assert calls == ['']


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


def test_engine_path_removes_windows_verbatim_prefix():
    from pathlib import PureWindowsPath
    assert worker.engine_path(PureWindowsPath(r'\\?\C:\TeX Live\bin\lualatex.exe')) == r'C:\TeX Live\bin\lualatex.exe'
    assert worker.engine_path(PureWindowsPath(r'\\?\UNC\server\share\lualatex.exe')) == r'\\server\share\lualatex.exe'
    assert worker.engine_path(PureWindowsPath(r'\\?\Volume{123}\lualatex.exe')) == r'\\?\Volume{123}\lualatex.exe'


def test_fixture_diagnostics_accepts_windows_line_endings(monkeypatch):
    source = (ROOT / 'proofs/numbering/manuscript.md').read_text(encoding='utf-8').replace('\n', '\r\n')
    monkeypatch.setenv('JJOKGYEOL_TEST_DIAGNOSTICS', '1')
    assert worker.fixture_diagnostics({'source': source, 'style': STYLE})


def test_worker_imports_only_worker_dependencies():
    # 네이티브 워커(PyInstaller)는 웹 프레임워크(starlette 등) 없이 독립 임포트되어야 합니다.
    # The packaged native worker must import cleanly without web-only packages like starlette.
    code = """
import sys
ALLOWED_3P = {'markdown_it', 'mdurl'}
class WorkerOnlyFinder:
    def find_spec(self, fullname, path, target=None):
        top = fullname.split('.')[0]
        if top in sys.stdlib_module_names or top in ('app', 'desktop') or top in ALLOWED_3P or top.startswith('_'):
            return None
        raise ModuleNotFoundError(f'Forbidden non-worker third-party import: {fullname}')
sys.meta_path.insert(0, WorkerOnlyFinder())
import desktop.worker.worker
import app.ai.providers
import app.ai.decision
"""
    result = subprocess.run(
        [sys.executable, '-c', code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr



def test_check_rejects_unsafe_address_without_leaking_key(tmp_path):
    connection = {'provider': 'chat', 'model': 'test', 'api_key': 'private-test-key',
                  'base_url': 'http://public.example.test/v1'}
    result = style_cli(tmp_path, '--check-decision', json.dumps(connection).encode())
    assert result.returncode == 1
    assert json.loads(result.stderr) == {'code': 'address'}
    assert result.stdout == b''
    assert list(tmp_path.iterdir()) == []


def test_worker_checks_and_replays_through_bundled_engine(tmp_path, monkeypatch, capsys):
    import io
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            options = json.loads(data['messages'][-1]['content'])
            seen.append((self.path, self.headers.get('Authorization')))
            answer = 'other' if 'other' in options else next(iter(options))
            body = json.dumps({'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {
                'role': 'assistant', 'content': json.dumps({'id': answer})}}]}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    runtime = runtime_dir(tmp_path)
    job = tmp_path / 'job'
    job.mkdir()
    service = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=service.serve_forever)
    thread.start()
    try:
        secret = 'private-native-test-key'
        connection = {'provider': 'chat', 'base_url': f'http://127.0.0.1:{service.server_port}/v1',
                      'model': 'fixture', 'api_key': secret}
        checked = style_cli(job, '--check-decision', json.dumps(connection).encode())
        assert checked.returncode == 0, checked.stderr
        assert json.loads(checked.stdout) == {'ok': True}

        monkeypatch.setattr(worker, 'limit_worker_files', lambda size: None)
        monkeypatch.chdir(job)
        monkeypatch.setattr(sys, 'argv', ['worker', '--model-proof', str(runtime)])
        payload = {'source': '글', 'style': STYLE, 'connection': connection}
        monkeypatch.setattr(sys, 'stdin', io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode())))

        def engine(args, *, cwd, env, stdin, **kwargs):
            assert Path(args[0]) == runtime / 'engine'
            assert not (cwd / 'fonts').is_symlink()
            assert stdin == subprocess.DEVNULL and secret not in repr(env)
            raw = (cwd / 'input.json').read_text(encoding='utf-8')
            assert secret not in raw
            choices = json.loads(raw)['paragraphs']['choices']
            selected = choices['1'] if choices else 'rule'
            if choices:
                assert (job / 'baseline-ready').is_file()
            line = dict(end_glyph=1, start_sp=0, available_sp=10000, remaining_sp=0,
                        tracking_per_10000=0, space_per_10000=10000, tracking_delta=0,
                        space_deviation=0, tracking_change=0, space_change=0, split_word=False)
            row = {'block': 1, 'rule_id': 'rule', 'selected_id': selected,
                   'candidates': [{'id': key, 'lines': [line]} for key in ('rule', 'other')]}
            (cwd / 'paragraph-candidates.jsonl').write_text(json.dumps(row))
            (cwd / 'book.pdf').write_bytes(f'%PDF-1.4\n{selected}\n%%EOF\n'.encode())
            return subprocess.CompletedProcess(args, 0)

        monkeypatch.setattr(subprocess, 'run', engine)
        assert worker.main() == 0
        assert (job / 'measure/book.pdf').read_bytes() == b'%PDF-1.4\nrule\n%%EOF\n'
        assert (job / 'response.pdf').read_bytes() == b'%PDF-1.4\nother\n%%EOF\n'
        assert json.loads((job / 'decision.json').read_text()) == {'decision': 'model', 'reason': None}
        assert seen == [('/v1/chat/completions', 'Bearer ' + secret)] * 2
        assert all(secret.encode() not in p.read_bytes() for p in job.rglob('*') if p.is_file())
        assert capsys.readouterr().err == ''
    finally:
        service.shutdown()
        thread.join()
        service.server_close()


@pytest.mark.parametrize(('base_url', 'provider', 'model', 'reason', 'style', 'pdf', 'error'), [
    ('http://public.example.test/v1', 'chat', 'test', 'address', STYLE, b'%PDF-1.4\nrule\n%%EOF\n', None),
    ('https://[bad', 'chat', 'test', 'address', STYLE, b'%PDF-1.4\nrule\n%%EOF\n', None),
    ('https://gemini.test', 'gemini', 'Gemini Flash', 'model', STYLE, b'%PDF-1.4\nrule\n%%EOF\n', None),
    ('http://public.example.test/v1', 'chat', 'test', 'address', {**STYLE, 'body_sp': -1}, None, 'invalid_input'),
    ('http://public.example.test/v1', 'chat', 'test', 'address', STYLE, b'%PDF-1.4\n', 'worker_error'),
])
def test_model_setting_fallback_validates_rule_proof(tmp_path, monkeypatch, capsys, base_url, provider, model,
                                                    reason, style, pdf, error):
    import io
    from app.ai.providers import Connection

    runtime = runtime_dir(tmp_path)
    job = tmp_path / 'job'
    job.mkdir()
    monkeypatch.chdir(job)
    monkeypatch.setattr(worker, 'limit_worker_files', lambda size: None)
    monkeypatch.setattr(sys, 'argv', ['worker', '--model-proof', str(runtime)])
    connection = {'provider': provider, 'model': model, 'base_url': base_url, 'api_key': 'test-key'}
    checked = style_cli(job, '--check-decision', json.dumps(connection).encode())
    assert checked.returncode == 1 and json.loads(checked.stderr) == {'code': reason}
    data = {'source': '글', 'style': style, 'connection': connection}
    monkeypatch.setattr(sys, 'stdin', io.TextIOWrapper(io.BytesIO(json.dumps(data).encode())))

    def engine(args, *, cwd, **kwargs):
        assert Path(args[0]) == runtime / 'engine'
        assert cwd == job / 'measure'
        assert json.loads((cwd / 'input.json').read_bytes())['paragraphs']['collect']
        (cwd / 'book.pdf').write_bytes(pdf)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, 'run', engine)
    monkeypatch.setattr(Connection, 'predict', lambda *a, **kw: pytest.fail('invalid setting reached provider'))
    status = worker.main()
    if error is None:
        assert status == 0
        assert (job / 'baseline-ready').is_file()
        assert (job / 'measure/book.pdf').read_bytes() == pdf
        assert json.loads((job / 'decision.json').read_bytes()) == {'decision': 'rule', 'reason': reason}
        assert not (job / 'response.pdf').exists()
    else:
        assert status == 1
        diagnostic = json.loads(capsys.readouterr().err)
        assert diagnostic['code'] == error
        if error == 'invalid_input':
            assert diagnostic['field'] == 'body_sp'
        assert not (job / 'baseline-ready').exists()
        assert not (job / 'decision.json').exists()


def test_framed_manuscript_uses_native_stored_style_validation(tmp_path):
    header = json.dumps({'request': {'source': '글', 'style': {**STYLE, 'body_sp': -1},
                                     'connection': {'provider': 'rule'}}, 'faces': []}).encode()
    result = subprocess.run([sys.executable, str(WORKER), '--model-proof', str(tmp_path)],
                            input=len(header).to_bytes(4, 'big') + header, cwd=tmp_path,
                            env={**os.environ, 'PYTHONPATH': str(ROOT)}, capture_output=True)
    assert result.returncode == 1
    assert json.loads(result.stderr)['code'] == 'invalid_input'
    assert json.loads(result.stderr)['field'] == 'body_sp'
    assert not list(tmp_path.iterdir())
