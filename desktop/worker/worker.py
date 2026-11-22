"""One local, rule-only render request; the caller owns the working directory."""
import argparse
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

from app.configuration import unique_fields
from app.document import InputError, parse_md
from app.operations import LIMITS
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


def main():
    parser = ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--runtime', type=Path)
    modes.add_argument('--style-open', action='store_true')
    modes.add_argument('--style-export', action='store_true')
    diagnostic = False
    try:
        args = parser.parse_args()
        limit_worker_files(LIMITS.worker_file_bytes)
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
        if not isinstance(request, dict) or set(request) != {'source', 'style'}:
            raise InputError({'request': '원고와 양식이 필요합니다.'})
        if not isinstance(request['source'], str):
            raise InputError({'source': '원고는 문자열이어야 합니다.'})
        if not isinstance(request['style'], dict):
            raise InputError({'style': '양식은 객체여야 합니다.'})
        doc = parse_md(request['source'])
        style = from_stored_style(request['style'])
        root = args.runtime.resolve(strict=True)
        config = json.loads((root / 'runtime.json').read_text(encoding='utf-8'), object_pairs_hook=unique_fields)
        if not isinstance(config, dict) or set(config) != {'engine', 'font_dir'}:
            raise ValueError('invalid runtime.json')
        engine = resource(root, config['engine'])
        fonts = resource(root, config['font_dir'])
        if not engine.is_file() or not fonts.is_dir():
            raise ValueError('invalid runtime resources')
        prepare_external_engine()
        with tempfile.TemporaryDirectory(dir='.') as folder:
            try:
                pdf = typeset(doc, style, Path(folder), engine=Path(engine_path(engine)), font_dir=fonts, max_pdf_bytes=LIMITS.worker_file_bytes)
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
        print(json.dumps({'code': error.code, 'field': error.field, 'message': error.message}, ensure_ascii=False), file=sys.stderr)
    except Exception:
        record_exception(diagnostic)
        print(json.dumps({'code': 'worker_error', 'message': '작업을 처리할 수 없습니다.'}, ensure_ascii=False), file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
