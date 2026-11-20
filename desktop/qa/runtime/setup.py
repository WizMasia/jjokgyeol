"""Build the portable Windows TeX tree from a Debian resource export."""
import argparse
import hashlib
import json
import lzma
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request

BASE = 'https://ftp.math.utah.edu/pub/tex/historic/systems/texlive/2022/tlnet-final/'
PACKAGES = ('kpathsea', 'kpathsea.win32', 'luahbtex.win32', 'luatex.win32', 'latex-bin.win32')


def verify(path, expected):
    if hashlib.sha512(path.read_bytes()).hexdigest() != expected:
        raise ValueError(f'checksum mismatch: {path.name}')


def write_lsr(root):
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


def download(url, dest):
    with urllib.request.urlopen(url, timeout=120) as response, dest.open('wb') as output:
        shutil.copyfileobj(response, output)


def main():
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
    records = {}
    for record in lzma.decompress(database.read_bytes()).decode().split('\n\n'):
        lines = dict(line.split(' ', 1) for line in record.splitlines() if ' ' in line)
        if lines.get('name') in PACKAGES:
            records[lines['name']] = lines['containerchecksum']
    if set(records) != set(PACKAGES):
        raise ValueError('missing package checksums in TeX Live database')
    for package in PACKAGES:
        archive = cache / (package + '.tar.xz')
        download(BASE + 'archive/' + archive.name, archive)
        verify(archive, records[package])
        with tarfile.open(archive) as bundle:
            for member in bundle.getmembers():
                # Only the launcher binaries and upstream portable configuration are needed.
                if (member.isfile()
                        and (member.name.startswith('bin/win32/') or member.name == 'texmf-dist/web2c/texmf.cnf')
                        and (package != 'latex-bin.win32' or member.name == 'bin/win32/lualatex.exe')):
                    target = tex / member.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.extractfile(member) as source, target.open('wb') as output:
                        shutil.copyfileobj(source, output)
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
                                    cwd=runtime, env=env, text=True).strip()
    if Path(found).resolve() != (fmt / 'lualatex.fmt').resolve():
        raise ValueError(f'wrong LuaLaTeX format: {found}')
    subprocess.run([str(tex / 'bin/win32/lualatex.exe'), '--version'], env=env, check=True)
    (runtime / 'runtime.json').write_text(json.dumps({
        'engine': 'texlive/bin/win32/lualatex.exe', 'font_dir': 'fonts'
    }, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
