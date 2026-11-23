"""Build a Tauri distribution from a separately staged native runtime."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

GUI = Path(__file__).resolve().parent
CLI = '@tauri-apps/cli@2.12.6'


def worker_name():
    return 'jjokgyeol-worker.exe' if os.name == 'nt' else 'jjokgyeol-worker'


def resource_config(runtime, platform=sys.platform, *, release_tag=None):
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
    options = {'active': True, 'resources': {str(runtime): 'runtime'}}
    if platform == 'win32':
        options['windows'] = {'webviewInstallMode': {'type': 'offlineInstaller'}}
    config = {'bundle': options}
    base = json.loads((GUI / 'tauri.conf.json').read_text(encoding='utf-8'))
    if release_tag is not None:
        if release_tag != 'v' + base['version']:
            raise ValueError('release tag must match the configured application version')
        config.update(json.loads((GUI / 'release.conf.json').read_text(encoding='utf-8')))
        if platform == 'darwin':
            # Previous development copies share the original desktop identifier.
            config['identifier'] = 'kr.jjokgyeol.app'
    if platform == 'darwin':
        version, _, ordinal = base['version'].partition('-preview.')
        config['version'] = version
        if ordinal:
            options['macOS'] = {'bundleVersion': ordinal}
    return config


def validate_frontend(dist, release_tag=None):
    for name in ('index.html', 'sample.md', 'static/desktop.js', 'static/editor.js',
                 'static/decision-settings.js', 'static/personal-defaults.js',
                 'static/zip.min.js', 'static/zip.LICENSE.txt', 'static/jjok-archive.js', 'static/jjok-document.js',
                 'static/document-fonts.js', 'static/font-rights.js', 'static/ofl-1.1.txt',
                 'static/sha256.min.js', 'static/sha256.LICENSE.txt',
                 'static/pdf.min.mjs', 'static/pdf.worker.min.mjs'):
        if not (dist / name).is_file():
            raise ValueError(f'missing frontend asset: {name}; build frontend first')
    for name in ('static/cmaps', 'static/standard_fonts'):
        directory = dist / name
        if not directory.is_dir() or not any(p.is_file() for p in directory.iterdir()):
            raise ValueError(f'missing frontend assets: {name}; build frontend first')

    label = '원고 편집기 · ' + (release_tag or '개발판')
    if f'<span class="product">{label}</span>' not in (dist / 'index.html').read_text(encoding='utf-8'):
        raise ValueError('frontend version mismatch; rebuild the frontend for this package')


def tauri_command(gui_dir: Path = GUI) -> list[str]:
    """로컬에 설치된 Tauri CLI 경로를 찾는다(무검증 원격 다운로드 금지) / Locate locally installed Tauri CLI without remote auto-download."""
    binary = 'tauri.cmd' if os.name == 'nt' else 'tauri'
    for candidate in (
        gui_dir / 'frontend' / 'node_modules' / '.bin' / binary,
        gui_dir / 'node_modules' / '.bin' / binary,
    ):
        if candidate.is_file():
            return [str(candidate)]
    raise ValueError(
        f'local Tauri CLI ({CLI}) is not installed; install pinned devDependencies via npm ci first'
    )


def main() -> None:
    if len(sys.argv) != 2:
        raise ValueError('usage: python bundle.py ABSOLUTE_RUNTIME_DIRECTORY')
    if sys.platform not in ('darwin', 'win32'):
        raise ValueError('run this build on macOS or Windows')
    tag = os.environ.get('JJOKGYEOL_RELEASE_TAG')
    config = resource_config(Path(sys.argv[1]), release_tag=tag)
    validate_frontend(GUI / 'frontend' / 'dist', tag)
    cmd = tauri_command(GUI)
    with tempfile.TemporaryDirectory() as directory:
        override = Path(directory) / 'release.conf.json'
        override.write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
        subprocess.run(
            [*cmd, 'build', '--config', str(override),
             '--bundles', 'nsis' if os.name == 'nt' else 'app'],
            cwd=GUI, check=True,
        )


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
