"""Reproduce the checked-in proof in a retained development job directory."""
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root))
runtime = Path(sys.argv[1]).resolve(strict=True)
job = Path(sys.argv[2]).resolve()
job.mkdir(parents=True, exist_ok=False)

from app.document import parse_md
from app.style import from_stored_style
from app.typeset import typeset

config = json.loads((runtime / 'runtime.json').read_text(encoding='utf-8'))
source = (root / 'proofs/numbering/manuscript.md').read_text(encoding='utf-8')
style = json.loads((root / 'proofs/numbering/odd-even.json').read_text(encoding='utf-8'))
typeset(parse_md(source), from_stored_style(style), job,
        engine=(runtime / config['engine']).resolve(strict=True),
        font_dir=(runtime / config['font_dir']).resolve(strict=True))
