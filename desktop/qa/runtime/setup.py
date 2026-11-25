"""Build the portable Windows TeX tree from a Debian resource export."""
import argparse
import hashlib
import json
import lzma
import os
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import subprocess
import tarfile
import urllib.request

BASE = 'https://ftp.math.utah.edu/pub/tex/historic/systems/texlive/2022/tlnet-final/'
PACKAGES = ('kpathsea', 'kpathsea.win32', 'luahbtex.win32', 'luatex.win32', 'latex-bin.win32')

# TeX Live 2022 historic tlnet-final 고정 SHA-512 체크섬 / Pinned SHA-512 checksums for TeX Live 2022 tlnet-final.
TLPDB_SHA512 = (
    '8ac9553c60b04387d87ffbc8cf77cc6925beee46a889680c4a28942dc63cb0fb'
    '8ca6815f820474520fd369ebcddd7880ddcacd60db8675bd51697791612236d8'
)
PACKAGE_SHA512: dict[str, str] = {
    'kpathsea': (
        '8a9f0dd49470bec5ba0f963a0385bea45141d6b805682bd65e95291b02158b9d'
        '2cedd5bd43592de7c447fe87f04efa00e4d1aa191a490147adcb57ec3922b5db'
    ),
    'kpathsea.win32': (
        'a97ece6a01988e180baa354c1c46f80e7193fd58c70f515f84802b33d5df9f59'
        '5003f902c5e98980a73a4188949ac1dbd10972f6d520665198ca91c316a58ad5'
    ),
    'latex-bin.win32': (
        '65c7c6fceb3fe9e1373ca515241801a87fb71efe506297cb82f23f54ac61ccf0'
        '3cfb58f4b83bef2fc58c0a43b36725bc23a31aad3e97a83f264d49214561ccb0'
    ),
    'luahbtex.win32': (
        'f68f0061f1a4fc427d3d49d9ad9daebd8770ce09b7e29049f0fa7af1dc7dafea'
        '5727e7c659c38ab98f4cd3602761081f5a7a88d4a04f7263a12db48c5f07fd66'
    ),
    'luatex.win32': (
        'da11835b3c85825518760deafa8dd31ee762731047942d4d540c41e144b3e870'
        '21f507745c303644db029d2c3c32f28ee679ad2677919e65824d5138d1f61fe0'
    ),
}


def verify(path: Path, expected: str) -> None:
    if hashlib.sha512(path.read_bytes()).hexdigest() != expected:
        raise ValueError(f'checksum mismatch: {path.name}')


def safe_extract_target(root: Path, member_name: str) -> Path:
    """tar 멤버 경로가 추출 루트 밖으로 벗어나지 않도록 검증한다 / Ensure a tar member stays inside root."""
    if not member_name or '\x00' in member_name or '\\' in member_name:
        raise ValueError(f'unsafe archive member path: {member_name!r}')
    posix = PurePosixPath(member_name)
    windows = PureWindowsPath(member_name)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        raise ValueError(f'unsafe archive member path: {member_name!r}')
    if any(part in ('', '.', '..') for part in posix.parts):
        raise ValueError(f'unsafe archive member path: {member_name!r}')
    root_resolved = root.resolve()
    target = (root_resolved / posix).resolve()
    if target == root_resolved or not target.is_relative_to(root_resolved):
        raise ValueError(f'unsafe archive member path: {member_name!r}')
    return target


def write_lsr(root: Path) -> None:
    database = root / 'ls-R'
    database.touch()
    with database.open('w', encoding='utf-8', newline='\n') as output:
        output.write('% ls-R -- filename database for kpathsea; do not change this line.\n\n')
        for directory, dirs, files in os.walk(root):
            dirs.sort()
            files.sort()
            relative = Path(directory).relative_to(root).as_posix()
            output.write(f'./{relative if relative != "." else ""}:\n')
            for name in sorted(dirs + files):
                output.write(name + '\n')
            output.write('\n')


def download(url: str, dest: Path) -> None:
    with urllib.request.urlopen(url, timeout=120) as response, dest.open('wb') as output:
        shutil.copyfileobj(response, output)


def embed_utf8_manifests(bin_dir: Path) -> None:
    program_files = os.environ.get('ProgramFiles(x86)')
    if program_files:
        kit = Path(program_files) / 'Windows Kits/10/bin'
        candidates = [
            (tuple(map(int, path.parent.parent.name.split('.'))), path)
            for path in kit.glob('*/x64/mt.exe')
            if path.is_file() and re.fullmatch(r'[0-9]+(?:\.[0-9]+){3}', path.parent.parent.name)
        ]
    else:
        candidates = []
    if not candidates:
        raise FileNotFoundError('Install the Windows SDK with x64 mt.exe to provision the TeX runtime.')
    tool = max(candidates)[1]
    manifest = Path(__file__).resolve().with_name('utf8.manifest')
    # The pinned binaries have no manifest. Kpathsea uses the process code page
    # to locate the TeX tree relative to the executable on Windows.
    for name in ('lualatex.exe', 'luahbtex.exe', 'kpsewhich.exe'):
        exe = bin_dir / name
        subprocess.run([str(tool), '-nologo', '-manifest', str(manifest),
                        f'-outputresource:{exe};#1'], check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('resources', type=Path, help='extracted resource directory')
    parser.add_argument('runtime', type=Path, help='new runtime directory')
    args = parser.parse_args()
    resources, runtime = args.resources.resolve(), args.runtime.resolve()
    runtime.mkdir(parents=True, exist_ok=False)
    tex = runtime / 'texlive'
    shutil.copytree(resources / 'texmf-dist', tex / 'texmf-dist')
    shutil.copytree(resources / 'nanum', runtime / 'fonts')
    cache = runtime / 'downloads'
    cache.mkdir()
    database = cache / 'texlive.tlpdb.xz'
    download(BASE + 'tlpkg/texlive.tlpdb.xz', database)
    # 원격 TLPDB 자체의 고정 SHA-512 검증 / Verify downloaded TLPDB against pinned SHA-512.
    verify(database, TLPDB_SHA512)
    records: dict[str, str] = {}
    for record in lzma.decompress(database.read_bytes()).decode().split('\n\n'):
        lines = dict(line.split(' ', 1) for line in record.splitlines() if ' ' in line)
        if lines.get('name') in PACKAGES:
            records[lines['name']] = lines['containerchecksum']
    if set(records) != set(PACKAGES):
        raise ValueError('missing package checksums in TeX Live database')
    for package in PACKAGES:
        pinned = PACKAGE_SHA512[package]
        if records[package] != pinned:
            raise ValueError(f'TLPDB checksum does not match pinned digest: {package}')
        archive = cache / (package + '.tar.xz')
        download(BASE + 'archive/' + archive.name, archive)
        verify(archive, pinned)
        with tarfile.open(archive) as bundle:
            for member in bundle.getmembers():
                # Only the launcher binaries and upstream portable configuration are needed.
                if (member.isfile()
                        and (member.name.startswith('bin/win32/') or member.name == 'texmf-dist/web2c/texmf.cnf')
                        and (package != 'latex-bin.win32' or member.name == 'bin/win32/lualatex.exe')):
                    target = safe_extract_target(tex, member.name)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.extractfile(member) as source, target.open('wb') as output:
                        shutil.copyfileobj(source, output)
    embed_utf8_manifests(tex / 'bin/win32')
    shutil.rmtree(cache)
    (tex / 'texmf.cnf').write_text(
        'TEXMFHOME = $TEXMFROOT/empty\nTEXMFLOCAL = $TEXMFROOT/empty\n'
        'TEXMFVAR = $TEXMFROOT/texmf-var\nTEXMFCONFIG = $TEXMFROOT/texmf-config\n', encoding='ascii')
    write_lsr(tex / 'texmf-dist')
    fmt = tex / 'texmf-var' / 'web2c' / 'luahbtex'
    fmt.mkdir(parents=True)
    env = dict(os.environ, TEXMFCACHE=str(tex / 'texmf-var'))
    subprocess.run([str(tex / 'bin/win32/luahbtex.exe'), '--ini', '--jobname=lualatex',
                    '--progname=lualatex', 'lualatex.ini'], cwd=fmt, env=env, check=True)
    write_lsr(tex / 'texmf-var')
    found = subprocess.check_output([str(tex / 'bin/win32/kpsewhich.exe'),
                                     '--progname=lualatex', '--engine=luahbtex', '--format=fmt', 'lualatex.fmt'],
                                    cwd=runtime, env=env, encoding='utf-8').strip()
    if Path(found).resolve() != (fmt / 'lualatex.fmt').resolve():
        raise ValueError(f'wrong LuaLaTeX format: {found}')
    subprocess.run([str(tex / 'bin/win32/lualatex.exe'), '--version'], env=env, check=True)
    (runtime / 'runtime.json').write_text(json.dumps({
        'engine': 'texlive/bin/win32/lualatex.exe', 'font_dir': 'fonts'
    }, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
