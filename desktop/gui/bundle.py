"""Build a Tauri distribution from a separately staged native runtime."""
import json
import os
from pathlib import Path
import subprocess
import sys

GUI = Path(__file__).resolve().parent
CLI = '@tauri-apps/cli@2.12.6'


def worker_name():
    return 'jjokgyeol-worker.exe' if os.name == 'nt' else 'jjokgyeol-worker'


def resource_config(runtime):
    if not runtime.is_absolute() or not runtime.is_dir():
        raise ValueError('runtime must be an existing absolute directory')
    runtime = runtime.resolve()
    if not (runtime / 'worker' / worker_name()).is_file():
        raise ValueError('runtime worker is missing')
    try:
        manifest = json.loads((runtime / 'runtime.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise ValueError('runtime.json is missing or invalid') from error
    if not isinstance(manifest, dict) or set(manifest) != {'engine', 'font_dir'}:
        raise ValueError('runtime.json must contain engine and font_dir')
    for key in ('engine', 'font_dir'):
        value = manifest[key]
        if not isinstance(value, str) or not value or Path(value).is_absolute():
            raise ValueError(f'invalid {key} in runtime.json')
        path = (runtime / value).resolve()
        if not path.is_relative_to(runtime) or not (path.is_file() if key == 'engine' else path.is_dir()):
            raise ValueError(f'missing or invalid {key} in runtime.json')
    return {'bundle': {'active': True, 'resources': {str(runtime): 'runtime'}}}


def validate_frontend(dist):
    for name in ('index.html', 'sample.md', 'static/desktop.js', 'static/editor.js',
                 'static/pdf.min.mjs', 'static/pdf.worker.min.mjs'):
        if not (dist / name).is_file():
            raise ValueError(f'missing frontend asset: {name}; build frontend first')
    for name in ('static/cmaps', 'static/standard_fonts'):
        directory = dist / name
        if not directory.is_dir() or not any(p.is_file() for p in directory.iterdir()):
            raise ValueError(f'missing frontend assets: {name}; build frontend first')


def main():
    if len(sys.argv) != 2:
        raise ValueError('usage: python bundle.py ABSOLUTE_RUNTIME_DIRECTORY')
    if sys.platform not in ('darwin', 'win32'):
        raise ValueError('run this build on macOS or Windows')
    config = resource_config(Path(sys.argv[1]))
    validate_frontend(GUI / 'frontend' / 'dist')
    npm = 'npm.cmd' if os.name == 'nt' else 'npm'
    subprocess.run([npm, 'exec', '--yes', f'--package={CLI}', '--', 'tauri', 'build',
                    '--config', json.dumps(config, ensure_ascii=False),
                    '--bundles', 'nsis' if os.name == 'nt' else 'app'], cwd=GUI, check=True)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
