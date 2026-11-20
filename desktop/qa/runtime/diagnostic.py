"""Reproduce the checked-in proof in a retained development job directory."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root))


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('runtime', type=Path)
    parser.add_argument('job', type=Path)
    parser.add_argument('--packaged', action='store_true')
    args = parser.parse_args(argv)
    runtime = args.runtime.resolve(strict=True)
    job = args.job.resolve()
    job.mkdir(parents=True, exist_ok=False)
    source = (root / 'proofs/numbering/manuscript.md').read_text(encoding='utf-8')
    style = json.loads((root / 'proofs/numbering/odd-even.json').read_text(encoding='utf-8'))
    if args.packaged:
        (job / 'request.json').write_text(json.dumps({'source': source, 'style': style}), encoding='utf-8')
        worker = runtime / 'worker' / ('jjokgyeol-worker.exe' if os.name == 'nt' else 'jjokgyeol-worker')
        return subprocess.run([str(worker), '--runtime', str(runtime)], cwd=job,
                              env={**os.environ, 'JJOKGYEOL_TEST_DIAGNOSTICS': '1'}).returncode

    from app.document import parse_md
    from app.style import from_stored_style
    from app.typeset import typeset

    config = json.loads((runtime / 'runtime.json').read_text(encoding='utf-8'))
    typeset(parse_md(source), from_stored_style(style), job,
            engine=(runtime / config['engine']).resolve(strict=True),
            font_dir=(runtime / config['font_dir']).resolve(strict=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
