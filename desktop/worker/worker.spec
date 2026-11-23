# Build with: python -m PyInstaller --clean --noconfirm desktop/worker/worker.spec
from pathlib import Path
from importlib.metadata import distribution

import certifi

root = Path(SPECPATH).resolve().parents[1]
certificates = [(certifi.where(), 'certifi')]
dist = distribution('certifi')
certificates.extend((str(dist.locate_file(path)), 'certifi') for path in dist.files if path.name == 'LICENSE')

weights = root / 'app/ai/weights'
model_data = [(str(weights), 'app/ai/weights')] if weights.is_dir() else []

a = Analysis([str(root / 'desktop/worker/worker.py')], pathex=[str(root)],
             datas=certificates + model_data + [(str(root / 'app/config'), 'app/config'),
                    (str(root / 'app/tex'), 'app/tex'),
                    (str(root / 'runtime.lock'), '.')],
             hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='jjokgyeol-worker', console=True)
coll = COLLECT(exe, a.binaries, a.datas, name='jjokgyeol-worker')
