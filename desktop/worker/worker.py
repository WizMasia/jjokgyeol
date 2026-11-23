"""One native worker request; the caller supervises the process and owns its directory."""
import argparse
from contextlib import nullcontext
from functools import partial
import hashlib
from decimal import Decimal
import json
import os
import re
import shutil
from pathlib import Path
import sys
import tempfile
import traceback
from time import monotonic

from app.configuration import unique_fields
from app.document import InputError, parse_md
from app.operations import LIMITS
from app.security import complete_pdf
from app.style import from_editor_style, from_stored_style
from app.typeset import typeset


def resource(root, value):
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise ValueError('runtime paths must be relative')
    path = (root / value).resolve(strict=True)
    if not path.is_relative_to(root):
        raise ValueError('runtime path escapes installation')
    return path


def limit_worker_files(max_bytes):
    if os.name == 'posix':
        import resource
        # 임시·진단 파일이 소유자 전용 권한으로만 생성되도록 umask를 제한합니다.
        # Restrict umask so worker-created files are owner-only on POSIX.
        os.umask(0o077)
        _, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
        limit = min(hard, max_bytes) if hard != resource.RLIM_INFINITY else max_bytes
        resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def engine_path(path):
    value = str(path)
    if value.startswith('\\\\?\\UNC\\'):
        ordinary = '\\\\' + value[8:]
    elif re.match(r'^\\\\\?\\[A-Za-z]:\\', value):
        ordinary = value[4:]
    else:
        return value
    return ordinary if len(ordinary) < 260 else value


def prepare_external_engine():
    if sys.platform == 'win32' and getattr(sys, 'frozen', False):
        import ctypes
        # 현재 작업 디렉터리를 DLL 검색 경로에서 제거합니다 / Remove CWD from Windows DLL search path.
        if not ctypes.windll.kernel32.SetDllDirectoryW(''):
            raise OSError('could not harden DLL search path')


def record_exception(diagnostic):
    if diagnostic:
        try:
            Path('diagnostic-exception.txt').write_text(traceback.format_exc(), encoding='utf-8')
        except OSError:
            pass


def fixture_diagnostics(request):
    if os.environ.get('JJOKGYEOL_TEST_DIAGNOSTICS') != '1':
        return False
    if isinstance(request, dict) and isinstance(request.get('source'), str):
        request = {**request, 'source': request['source'].replace('\r\n', '\n').replace('\r', '\n')}
    encoded = json.dumps(request, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
    # Only the checked-in numbering fixture may expose test diagnostics.
    return hashlib.sha256(encoded).hexdigest() == 'cec7fb87b42035bc00d42b44ff647e0441039367cd130b962a3cffc902b94ad9'


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def manuscript(request):
    if not isinstance(request, dict) or set(request) != {'source', 'style'}:
        raise InputError({'request': '원고와 양식이 필요합니다.'})
    if not isinstance(request['source'], str):
        raise InputError({'source': '원고는 문자열이어야 합니다.'})
    if not isinstance(request['style'], dict):
        raise InputError({'style': '양식은 객체여야 합니다.'})
    return parse_md(request['source']), from_stored_style(request['style'])


def bundled_typesetter(runtime):
    root = runtime.resolve(strict=True)
    config = json.loads((root / 'runtime.json').read_text(encoding='utf-8'), object_pairs_hook=unique_fields)
    if not isinstance(config, dict) or set(config) != {'engine', 'font_dir'}:
        raise ValueError('invalid runtime.json')
    engine = resource(root, config['engine'])
    fonts = resource(root, config['font_dir'])
    if not engine.is_file() or not fonts.is_dir():
        raise ValueError('invalid runtime resources')
    prepare_external_engine()
    return partial(typeset, engine=Path(engine_path(engine)), font_dir=fonts, max_pdf_bytes=LIMITS.worker_file_bytes)


def decision_request(runtime, *, plain=False, with_spacing=False, catalog=False):
    from app.ai.providers import ProviderError, check_connection, parse_render_connection

    deadline = monotonic() + LIMITS.timeout_seconds
    if getattr(sys, 'frozen', False):
        import ssl
        if not ssl.get_default_verify_paths().cafile:
            # Some packaged OpenSSL builds point at a CA file on the build machine.
            os.environ['SSL_CERT_FILE'] = str(Path(sys._MEIPASS) / 'certifi' / 'cacert.pem')
    stream = sys.stdin.buffer
    first = stream.read(1)
    context = nullcontext()
    if runtime is not None and first == b'\0':
        from app.document_fonts import use_document_fonts
        from app.font_assets import FRAME_LIMIT
        from app.font_frame import unpack_frame

        try:
            request, assets = unpack_frame(first + stream.read(FRAME_LIMIT), remote=False)
        except (ValueError, RecursionError) as error:
            raise InputError({'fonts': '글꼴 파일의 형식, 크기와 내장 권한을 확인하세요.'}) from error
        context = use_document_fonts(assets)
    else:
        try:
            raw = first + stream.read(LIMITS.request_body_bytes)
            if len(raw) > LIMITS.request_body_bytes:
                raise ValueError
            request = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_fields)
        except (ValueError, RecursionError):
            print(json.dumps({'code': 'invalid_connection'}), file=sys.stderr)
            return 1
    try:
        if catalog:
            from app.ai.providers import list_models
            print(json.dumps({'models': list_models(request, desktop=True)}))
            return 0
        if runtime is None:
            check_connection(request, desktop=True)
            print(json.dumps({'ok': True}))
            return 0
        connection = None
        reason = None
        if not plain and isinstance(request, dict) and 'connection' in request:
            raw_connection = request.pop('connection')
            if raw_connection != {'provider': 'rule'}:
                connection, reason = parse_render_connection(raw_connection, desktop=True)
        with context:
            return render_decision(runtime, request, deadline, connection, reason, plain=plain, with_spacing=with_spacing)
    except ProviderError as error:
        print(json.dumps({'code': error.code}), file=sys.stderr)
        return 1


def render_decision(runtime, request, deadline, connection, reason, *, plain=False, with_spacing=False):
    doc, style = manuscript(request)
    bundled = bundled_typesetter(runtime)
    if with_spacing:
        bundled = partial(bundled, collect_spacing=True)
    workdir = Path.cwd()
    measure = workdir / 'measure'

    def render(doc, style, folder, **options):
        pdf = bundled(doc, style, folder, **options)
        if folder == measure and complete_pdf(pdf):
            # TeX may have an incomplete book.pdf on disk until it exits.
            (workdir / 'baseline-ready').touch()
        return pdf

    if connection is None:
        measure.mkdir()
        pdf = render(doc, style, measure, collect_candidates=not plain)
        status = 'rule'
    else:
        from app.ai.decision import render_provider_choice
        pdf, status, reason = render_provider_choice(doc, style, workdir, deadline, connection, typesetter=render)
    if not complete_pdf(pdf):
        raise ValueError('incomplete PDF')
    if status != 'rule':
        (workdir / 'response.pdf').write_bytes(pdf)
    (workdir / 'decision.json').write_text(json.dumps({'decision': status, 'reason': reason}), encoding='utf-8')
    return 0


def main():
    parser = ArgumentParser()
    parser.add_argument('--spacing-receipt', action='store_true')
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--runtime', type=Path)
    modes.add_argument('--model-proof', type=Path)
    modes.add_argument('--plain-proof', type=Path)
    modes.add_argument('--check-decision', action='store_true')
    modes.add_argument('--list-decision-models', action='store_true')
    modes.add_argument('--paragraph-align', action='store_true')
    modes.add_argument('--style-open', action='store_true')
    modes.add_argument('--style-export', action='store_true')
    diagnostic = False
    try:
        args = parser.parse_args()
        limit_worker_files(LIMITS.worker_file_bytes)
        if args.plain_proof or args.model_proof or args.check_decision or args.list_decision_models:
            return decision_request(args.plain_proof or args.model_proof, plain=args.plain_proof is not None,
                                    with_spacing=args.spacing_receipt, catalog=args.list_decision_models)
        if args.paragraph_align:
            from app.paragraph_worker import alignment_response
            sys.stdout.buffer.write(alignment_response(sys.stdin.buffer))
            return 0
        if args.style_open or args.style_export:
            raw = sys.stdin.buffer.read(LIMITS.request_body_bytes + 1)
            if len(raw) > LIMITS.request_body_bytes:
                raise InputError({'style': '요청 크기가 너무 큽니다.'})
            values = json.loads(raw.decode('utf-8-sig'), parse_float=Decimal, object_pairs_hook=unique_fields)
            style = from_stored_style(values) if args.style_open else from_editor_style(values)
            result = style.to_editor() if args.style_open else style.to_stored()
            print(json.dumps(result, allow_nan=False))
            return 0
        with Path('request.json').open('rb') as stream:
            raw = stream.read(LIMITS.request_body_bytes + 1)
        if len(raw) > LIMITS.request_body_bytes:
            raise InputError({'request': '요청 크기가 너무 큽니다.'})
        request = json.loads(raw, object_pairs_hook=unique_fields)
        diagnostic = fixture_diagnostics(request)
        doc, style = manuscript(request)
        bundled = bundled_typesetter(args.runtime)
        with tempfile.TemporaryDirectory(dir='.') as folder:
            try:
                pdf = bundled(doc, style, Path(folder))
            finally:
                log = Path(folder) / 'book.log'
                if diagnostic and log.is_file():
                    shutil.copyfile(log, 'diagnostic-book.log')
        if len(pdf) > LIMITS.worker_file_bytes or not pdf.startswith(b'%PDF-'):
            raise ValueError('invalid or oversized PDF')
        Path('response.pdf').write_bytes(pdf)
        return 0
    except InputError as error:
        record_exception(diagnostic)
        print(json.dumps({'code': error.code, 'field': error.field, 'message': error.message}, ensure_ascii=True), file=sys.stderr)
    except Exception:
        record_exception(diagnostic)
        print(json.dumps({'code': 'worker_error', 'message': '작업을 처리할 수 없습니다.'}, ensure_ascii=True), file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
