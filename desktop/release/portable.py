"""Stage the Windows release and compile it with the pinned PortableApps tools."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import tempfile
from urllib.parse import unquote, urlsplit
from urllib.request import urlopen

HERE = Path(__file__).resolve().parent
GUI = HERE.parent / 'gui'
APP_ID = 'JjokgyeolPortable'


def package_version(tag):
    component = r'(0|[1-9][0-9]{0,4})'
    match = re.fullmatch(rf'v{component}\.{component}\.{component}(?:-preview\.([1-9][0-9]{{0,4}}))?', tag)
    if not match:
        raise ValueError(f'Invalid portable release tag: {tag!r}')
    major, minor, patch, preview = match.groups()
    if any(int(part) > 65535 for part in (major, minor, patch)) or (preview and int(preview) >= 65535):
        raise ValueError(f'Invalid portable package version: {tag}')
    # The final release must sort above all previews with the same base version.
    return tag[1:], '.'.join((major, minor, patch, preview or '65535'))


def portable_filename(tag):
    display, _ = package_version(tag)
    return f'{APP_ID}_{display}.paf.exe'


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def verify_hash(path, expected):
    if not path.is_file():
        raise ValueError(f'Missing dependency: {path}')
    if sha256(path) != expected:
        raise ValueError(f'Dependency SHA-256 mismatch: {path}')


def require_pe(path, x64=False):
    if path.is_file():
        with path.open('rb') as executable:
            dos = executable.read(64)
            if len(dos) == 64 and dos.startswith(b'MZ'):
                executable.seek(struct.unpack_from('<I', dos, 60)[0])
                header = executable.read(26)
                if len(header) == 26 and header.startswith(b'PE\0\0'):
                    if not x64 or (header[4:6] == b'\x64\x86' and header[24:26] == b'\x0b\x02'):
                        return
    raise ValueError(f'Missing or invalid {"x64 " if x64 else ""}PE file: {path}')


def validate_webview(path, version):
    if path.name != f'Microsoft.WebView2.FixedVersionRuntime.{version}.x64' or not path.is_dir():
        raise ValueError(f'Invalid x64 Fixed Version WebView2 directory: {path}')
    validate_webview_files(path, version)


def validate_webview_files(path, version):
    for name in ('msedgewebview2.exe', 'msedge.dll'):
        require_pe(path / name, x64=True)
    for name in (f'{version}.manifest', 'icudtl.dat', 'Locales/en-US.pak'):
        file = path / name
        if not file.is_file() or not file.stat().st_size:
            raise ValueError(f'Missing WebView2 payload file: {file}')


def validate_inputs(executable, runtime):
    if not executable.is_absolute() or not runtime.is_absolute():
        raise ValueError('Executable and Runtime must be absolute paths')
    require_pe(executable, x64=True)
    worker = runtime / 'worker/jjokgyeol-worker.exe'
    manifest = runtime / 'runtime.json'
    if not worker.is_file() or not manifest.is_file():
        raise ValueError('Runtime must contain the Windows worker and runtime.json')
    paths = json.loads(manifest.read_text(encoding='utf-8'))
    if not isinstance(paths, dict) or set(paths) != {'engine', 'font_dir'}:
        raise ValueError('Invalid runtime.json')
    for name, directory in (('engine', False), ('font_dir', True), ('licenses', True)):
        value = 'licenses' if name == 'licenses' else paths[name]
        if not isinstance(value, str) or not value:
            raise ValueError(f'Invalid runtime {name}')
        relative = Path(value)
        path = (runtime / relative).resolve()
        exists = path.is_dir() and any(path.iterdir()) if directory else path.is_file()
        if relative.is_absolute() or not path.is_relative_to(runtime.resolve()) or not exists:
            raise ValueError(f'Missing or invalid runtime {name}: {relative}')


def stage(package, executable, runtime, webview, tag):
    display, numeric = package_version(tag)
    validate_inputs(executable, runtime)
    package.mkdir()
    for directory in ('App', 'Other'):
        shutil.copytree(HERE / 'portable' / directory, package / directory)
    shutil.copy2(HERE / 'portable/help.html', package)
    (package / 'Data').mkdir()
    info = package / 'App/AppInfo'
    template = info / 'appinfo.ini'
    metadata = template.read_text(encoding='utf-8')
    template.write_text(metadata.replace('@PACKAGE_VERSION@', numeric).replace('@DISPLAY_VERSION@', display),
                        encoding='utf-16')
    icon = (GUI / 'icons/icon.ico').read_bytes()
    (info / 'appicon.ico').write_bytes(icon)
    # Reuse the PNG entries already embedded in the release icon.
    for index in range(struct.unpack_from('<H', icon, 4)[0]):
        width, height, _, _, _, _, size, offset = struct.unpack_from('<BBBBHHII', icon, 6 + 16 * index)
        if width == height and width in (16, 32):
            png = icon[offset:offset + size]
            if not png.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError(f'Missing {width}px PNG data in the release icon')
            (info / f'appicon_{width}.png').write_bytes(png)
    for width in (16, 32):
        if not (info / f'appicon_{width}.png').is_file():
            raise ValueError(f'Missing {width}px release icon')
    app = package / 'App/Jjokgyeol'
    app.mkdir()
    shutil.copy2(executable, app / 'jjokgyeol-desktop.exe')
    shutil.copytree(runtime, app / 'runtime')
    shutil.copytree(webview, app / 'WebView2')


def run(*arguments):
    subprocess.run(arguments, check=True, timeout=1800)


def compile_package(package, launcher, installer, output):
    # Both official generators take the app directory as their only argument.
    # They skip the finish page on success. A GUI stuck on an error times out.
    for folder, program, result, log in (
        (launcher, 'PortableApps.comLauncherGenerator.exe', package / f'{APP_ID}.exe',
         'PortableApps.comLauncherGeneratorLog.txt'),
        (installer, 'PortableApps.comInstaller.exe', output, 'PortableApps.comInstallerLog.txt'),
    ):
        require_pe(folder / program)
        (folder / 'Data').mkdir(exist_ok=True)
        try:
            run(folder / program, package)
            require_pe(result)
        except (ValueError, subprocess.SubprocessError):
            logfile = folder / 'Data' / log
            if logfile.is_file():
                print(logfile.read_bytes().decode('utf-8', errors='replace')[-8000:])
            raise
    if list(package.parent.glob('*.paf.exe')) != [output]:
        raise ValueError(f'Expected exactly one generated PAF: {output.name}')


def payload_inventory(package, version):
    app = package / 'App/Jjokgyeol'
    validate_inputs(app / 'jjokgyeol-desktop.exe', app / 'runtime')
    validate_webview_files(app / 'WebView2', version)
    return {file.relative_to(package).as_posix(): sha256(file)
            for file in app.rglob('*') if file.is_file()}


def verify_payload(seven_zip, candidate, inventory):
    require_pe(candidate)
    run(seven_zip, 't', candidate)
    with tempfile.TemporaryDirectory(dir=candidate.parent) as temporary:
        extracted = Path(temporary)
        run(seven_zip, 'x', '-y', f'-o{extracted}', candidate)
        for name, digest in inventory.items():
            file = extracted / name
            if not file.is_file():
                raise ValueError(f'PAF is missing payload file: {name}')
            if sha256(file) != digest:
                raise ValueError(f'PAF changed payload file: {name}')
        require_pe(extracted / f'{APP_ID}.exe')
        for name in ('appinfo.ini', 'EULA.txt', f'Launcher/{APP_ID}.ini'):
            file = extracted / 'App/AppInfo' / name
            if not file.is_file() or not file.stat().st_size:
                raise ValueError(f'PAF is missing metadata: {name}')


def prepare_installer_template(path):
    source = path.read_text(encoding='utf-16')
    early_write = '\tSectionIn RO\n\tSetOutPath $INSTDIR'
    preinstall = '\t!ifmacrodef CustomCodePreInstall\n\t\t!insertmacro CustomCodePreInstall\n\t!endif'
    for original in (early_write, preinstall, '\tSetSilent normal'):
        if source.count(original) != 1:
            raise ValueError(f'Unexpected PortableApps installer template: {original!r}')
    # Keep all destination writes after the embedded guard. The explicit /S switch
    # is for acceptance runs where the operator has already accepted the EULA.
    source = source.replace(preinstall, '').replace('\tSetSilent normal', '')
    source = source.replace(early_write, '\tSectionIn RO\n' + preinstall + '\n\tSetOutPath $INSTDIR')
    path.write_text(source, encoding='utf-16', newline='\r\n')


def build(executable, runtime, output_dir, tag):
    filename = portable_filename(tag)
    if not output_dir.is_absolute():
        raise ValueError('OutputDir must be an absolute path')
    output = output_dir / filename
    if os.path.lexists(output):
        raise FileExistsError(f'Output already exists: {output}')
    validate_inputs(executable, runtime)
    if platform.system() != 'Windows' or platform.machine().upper() != 'AMD64':
        raise ValueError('Build the portable release on Windows x64')
    seven_zip = Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / '7-Zip/7z.exe'
    require_pe(seven_zip, x64=True)
    pins = json.loads((HERE / 'portable.lock.json').read_text(encoding='utf-8'))
    with tempfile.TemporaryDirectory(prefix='jjok-paf-') as temporary:
        work = Path(temporary)
        extracted = {}
        for name, pin in pins.items():
            archive = work / Path(unquote(urlsplit(pin['url']).path)).name
            with urlopen(pin['url'], timeout=120) as source, archive.open('xb') as destination:
                shutil.copyfileobj(source, destination)
            verify_hash(archive, pin['sha256'])
            folder = work / name
            run(seven_zip, 'x', '-y', f'-o{folder}', archive)
            extracted[name] = folder
        version = pins['webview']['version']
        webview = extracted['webview'] / f'Microsoft.WebView2.FixedVersionRuntime.{version}.x64'
        validate_webview(webview, version)
        payload = work / 'payload'
        payload.mkdir()
        package = payload / APP_ID
        stage(package, executable, runtime, webview, tag)
        source = package / 'Other/Source'
        shutil.copytree(extracted['launcher'] / 'Other/Source', source / 'PortableApps.comLauncher')
        shutil.copy2(extracted['installer'] / 'Other/Source/License.txt', source / 'Installer-License.txt')
        template = extracted['installer'] / 'App/installer/PortableApps.comInstaller.nsi'
        prepare_installer_template(template)
        inventory = payload_inventory(package, version)
        candidate = payload / output.name
        compile_package(package, extracted['launcher'], extracted['installer'], candidate)
        verify_payload(seven_zip, candidate, inventory)

        output_dir.mkdir(parents=True, exist_ok=True)
        # Publish only the completed installer, with an atomic no-overwrite operation on Windows.
        with tempfile.TemporaryDirectory(dir=output_dir) as outgoing:
            finished = Path(outgoing) / output.name
            shutil.copy2(candidate, finished)
            finished.rename(output)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path)
    parser.add_argument('runtime', type=Path)
    parser.add_argument('output_dir', type=Path)
    arguments = parser.parse_args()
    print(build(arguments.executable, arguments.runtime, arguments.output_dir,
                os.environ.get('JJOKGYEOL_RELEASE_TAG', '')))
