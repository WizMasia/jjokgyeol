"""One local, rule-only render request; the caller owns the working directory."""
import argparse
import json
from pathlib import Path
import sys
import subprocess
import tempfile

from app.configuration import unique_fields
from app.document import InputError, parse_md
from app.operations import LIMITS
from app.style import from_stored_style
from app.typeset import typeset


def resource(root, value):
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise ValueError('runtime paths must be relative')
    path = (root / value).resolve(strict=True)
    if not path.is_relative_to(root):
        raise ValueError('runtime path escapes installation')
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', required=True, type=Path)
    args = parser.parse_args()
    try:
        raw = Path('request.json').read_bytes()
        if len(raw) > LIMITS.request_body_bytes:
            raise InputError({'request': '요청 크기가 너무 큽니다.'})
        request = json.loads(raw, object_pairs_hook=unique_fields)
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
        with tempfile.TemporaryDirectory(dir='.') as folder:
            pdf = typeset(doc, style, Path(folder), engine=engine, font_dir=fonts)
        if len(pdf) > LIMITS.worker_file_bytes or not pdf.startswith(b'%PDF-'):
            raise ValueError('invalid or oversized PDF')
        Path('response.pdf').write_bytes(pdf)
        return 0
    except InputError as error:
        print(json.dumps({'code': error.code, 'field': error.field, 'message': error.message}, ensure_ascii=False), file=sys.stderr)
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError) as error:
        print(json.dumps({'code': 'worker_error', 'message': str(error)}, ensure_ascii=False), file=sys.stderr)
    return 1


if __name__ == '__main__':
    sys.exit(main())
