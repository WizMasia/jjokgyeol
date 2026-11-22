"""Collect dependency notices alongside the TeX and font notices from the resource export."""
import json
import shutil
import subprocess
import sys
import sysconfig
from importlib.metadata import distribution
from pathlib import Path


def copy_notices(source, target):
    for path in source.iterdir():
        if path.is_file() and path.name.upper().startswith(('LICENSE', 'LICENCE', 'COPYING', 'NOTICE')):
            target.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target / path.name)


def main(destination):
    pdfjs = Path('desktop/gui/frontend/node_modules/pdfjs-dist')
    for folder in (Path('.'), Path('cmaps'), Path('standard_fonts')):
        copy_notices(pdfjs / folder, destination / 'pdfjs' / folder)

    python_notices = destination / 'python'
    python_notices.mkdir(parents=True)
    license_paths = [Path(sys.base_prefix) / 'LICENSE.txt', Path(sysconfig.get_path('stdlib')) / 'LICENSE.txt']
    python_license = next(path for path in license_paths if path.is_file())
    shutil.copyfile(python_license, python_notices / 'CPython-LICENSE.txt')
    for name in ('markdown-it-py', 'mdurl', 'pyinstaller'):
        dist = distribution(name)
        for path in dist.files or ():
            if '.dist-info' in str(path) and path.name.upper().startswith(('LICENSE', 'COPYING', 'NOTICE')):
                target = python_notices / name / path.name
                target.parent.mkdir(exist_ok=True)
                shutil.copyfile(dist.locate_file(path), target)

    metadata = subprocess.check_output(['cargo', 'metadata', '--locked', '--format-version', '1',
                                        '--manifest-path', 'desktop/gui/Cargo.toml'])
    packages = []
    for crate in json.loads(metadata)['packages']:
        if crate['source'] is None:
            continue
        label = f"{crate['name']}-{crate['version']}"
        packages.append('\t'.join([label, crate['license'] or '', crate['repository'] or '']))
        copy_notices(Path(crate['manifest_path']).parent, destination / 'rust' / label)
    (destination / 'rust-packages.tsv').write_text('\n'.join(sorted(packages)) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main(Path(sys.argv[1]))
