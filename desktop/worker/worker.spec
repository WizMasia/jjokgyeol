# Build with: python -m PyInstaller --clean --noconfirm desktop/worker/worker.spec
from pathlib import Path

root = Path(SPECPATH).resolve().parents[1]
a = Analysis([str(root / 'desktop/worker/worker.py')], pathex=[str(root)],
             datas=[(str(root / 'app/config'), 'app/config'),
                    (str(root / 'app/tex'), 'app/tex'),
                    (str(root / 'runtime.lock'), '.')],
             hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='jjokgyeol-worker', console=True)
coll = COLLECT(exe, a.binaries, a.datas, name='jjokgyeol-worker')
