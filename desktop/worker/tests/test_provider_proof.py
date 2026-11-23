"""Exercise the packaged worker, engine, fonts and decision replay from a staged native runtime."""
import json
import os
from pathlib import Path
import subprocess

import pytest


@pytest.fixture
def staged_worker(monkeypatch):
    if not os.getenv('JJOKGYEOL_NATIVE_RUNTIME'):
        pytest.skip('requires staged native runtime')
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / 'tests'))
    runtime = Path(os.environ['JJOKGYEOL_NATIVE_RUNTIME']).resolve()
    worker = runtime / 'worker' / ('jjokgyeol-worker.exe' if os.name == 'nt' else 'jjokgyeol-worker')
    return worker, runtime


def test_packaged_model_changes_printed_lines(tmp_path, staged_worker):
    from test_decision import SOURCE, STYLE, model_server, scan
    from app.style import Style, apply_style

    worker, runtime = staged_worker
    calls = []

    def answer(path, data):
        options = json.loads(data['messages'][-1]['content'])
        calls.append(path)
        selected = min(options, key=lambda key: options[key][0]['end_glyph'])
        return {'choices': [{'finish_reason': 'stop', 'index': 0, 'message': {
            'role': 'assistant', 'content': json.dumps({'id': selected})}}]}

    with model_server(answer) as port:
        connection = {'provider': 'chat', 'model': 'fixture', 'base_url': f'http://127.0.0.1:{port}/v1',
                      'api_key': 'native-proof-fixture-key'}
        request = {'connection': connection, 'source': SOURCE, 'style': apply_style(Style(), STYLE).to_stored()}
        result = subprocess.run([worker, '--model-proof', runtime], cwd=tmp_path, capture_output=True,
                                input=json.dumps(request).encode(), timeout=30)
    assert result.returncode == 0, result.stderr
    assert not result.stdout and not result.stderr
    assert json.loads((tmp_path / 'decision.json').read_bytes()) == {'decision': 'model', 'reason': None}
    assert calls == ['/v1/chat/completions']
    rule, rule_notes = scan((tmp_path / 'measure/book.pdf').read_bytes())
    selected, selected_notes = scan((tmp_path / 'response.pdf').read_bytes())
    assert rule == [9, 11] and selected == [7, 11]
    assert selected_notes == rule_notes
    assert all(b'native-proof-fixture-key' not in path.read_bytes() for path in tmp_path.rglob('*') if path.is_file())


def test_packaged_rejected_address_delivers_rule_without_provider_call(tmp_path, staged_worker):
    from test_decision import SOURCE, STYLE, model_server, scan
    from app.style import Style, apply_style

    worker, runtime = staged_worker
    calls = []
    with model_server(lambda path, data: calls.append(path) or {}) as port:
        connection = {'provider': 'chat', 'model': 'fixture', 'base_url': f'http://127.0.0.1:{port}/v1#invalid'}
        checked = subprocess.run([worker, '--check-decision'], cwd=tmp_path, input=json.dumps(connection).encode(),
                                 capture_output=True, timeout=30)
        assert checked.returncode != 0 and json.loads(checked.stderr) == {'code': 'address'}
        request = {'connection': connection, 'source': SOURCE, 'style': apply_style(Style(), STYLE).to_stored()}
        result = subprocess.run([worker, '--model-proof', runtime], cwd=tmp_path, input=json.dumps(request).encode(),
                                capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert not calls
    assert json.loads((tmp_path / 'decision.json').read_bytes()) == {'decision': 'rule', 'reason': 'address'}
    assert (tmp_path / 'baseline-ready').is_file()
    assert not (tmp_path / 'response.pdf').exists()
    ends, _ = scan((tmp_path / 'measure/book.pdf').read_bytes())
    assert ends == [9, 11]


def test_packaged_builtin_without_weights_delivers_rule(tmp_path, staged_worker):
    from test_decision import SOURCE, STYLE, scan
    from app.style import Style, apply_style

    worker, runtime = staged_worker
    connection = {'provider': 'builtin'}
    checked = subprocess.run([worker, '--check-decision'], cwd=tmp_path, input=json.dumps(connection).encode(),
                             capture_output=True, timeout=30)
    assert checked.returncode != 0 and json.loads(checked.stderr) == {'code': 'builtin_unavailable'}
    request = {'connection': connection, 'source': SOURCE, 'style': apply_style(Style(), STYLE).to_stored()}
    result = subprocess.run([worker, '--model-proof', runtime], cwd=tmp_path, input=json.dumps(request).encode(),
                            capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads((tmp_path / 'decision.json').read_bytes()) == {'decision': 'rule', 'reason': 'builtin_unavailable'}
    assert scan((tmp_path / 'measure/book.pdf').read_bytes())[0] == [9, 11]
