"""Stage a relocatable TeX Live runtime from an extracted resource export."""
import argparse
import importlib.util
import json
import lzma
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess
import tarfile
import tempfile

spec = importlib.util.spec_from_file_location('runtime_setup', Path(__file__).resolve().parents[1] / 'qa/runtime/setup.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)
BASE, download, verify, write_lsr = setup.BASE, setup.download, setup.verify, setup.write_lsr


def extract_archive(archive, tex, arch):
    wanted = {f'bin/{arch}/luahbtex', f'bin/{arch}/kpsewhich',
              f'bin/{arch}/lualatex', 'texmf-dist/web2c/texmf.cnf'}
    with tarfile.open(archive) as bundle:
        for member in bundle.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts:
                raise ValueError(f'unsafe archive member: {member.name}')
            if member.name not in wanted:
                continue
            target = tex / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            if member.isfile():
                with bundle.extractfile(member) as source, target.open('wb') as output:
                    shutil.copyfileobj(source, output)
                target.chmod(member.mode & 0o777)
            elif member.name == f'bin/{arch}/lualatex' and member.issym() and member.linkname == 'luahbtex':
                shutil.copy2(target.parent / member.linkname, target)
            else:
                raise ValueError(f'unsupported archive member: {member.name}')


def stage(resources, runtime):
    platforms = {('Darwin', 'arm64'): 'universal-darwin', ('Linux', 'x86_64'): 'x86_64-linux'}
    system = (platform.system(), platform.machine())
    if system not in platforms:
        raise ValueError(f'unsupported platform: {system[0]} {system[1]}')
    arch = platforms[system]
    packages = ('kpathsea', f'kpathsea.{arch}', f'luahbtex.{arch}', f'latex-bin.{arch}')
    resources, runtime = Path(resources).resolve(), Path(runtime).resolve()
    runtime.mkdir(parents=True, exist_ok=False)
    tex = runtime / 'texlive'
    shutil.copytree(resources / 'texmf-dist', tex / 'texmf-dist')
    shutil.copytree(resources / 'nanum', runtime / 'fonts')
    shutil.copytree(resources / 'licenses', runtime / 'licenses')
    with tempfile.TemporaryDirectory() as temp:
        cache = Path(temp)
        database = cache / 'texlive.tlpdb.xz'
        download(BASE + 'tlpkg/texlive.tlpdb.xz', database)
        records = {}
        for record in lzma.decompress(database.read_bytes()).decode().split('\n\n'):
            lines = dict(line.split(' ', 1) for line in record.splitlines() if ' ' in line)
            if lines.get('name') in packages:
                records[lines['name']] = lines['containerchecksum']
        if set(records) != set(packages):
            raise ValueError('missing package checksums in TeX Live database')
        for package in packages:
            archive = cache / (package + '.tar.xz')
            download(BASE + 'archive/' + archive.name, archive)
            verify(archive, records[package])
            extract_archive(archive, tex, arch)
    (tex / 'texmf.cnf').write_text(
        'TEXMFHOME = $TEXMFROOT/empty\nTEXMFLOCAL = $TEXMFROOT/empty\n'
        'TEXMFVAR = $TEXMFROOT/texmf-var\nTEXMFCONFIG = $TEXMFROOT/texmf-config\n', encoding='ascii')
    write_lsr(tex / 'texmf-dist')
    fmt = tex / 'texmf-var/web2c/luahbtex'
    fmt.mkdir(parents=True)
    env = dict(os.environ, TEXMFCACHE=str(tex / 'texmf-var'))
    bin_dir = tex / 'bin' / arch
    subprocess.run([str(bin_dir / 'luahbtex'), '--ini', '--jobname=lualatex',
                    '--progname=lualatex', 'lualatex.ini'], cwd=fmt, env=env, check=True)
    write_lsr(tex / 'texmf-var')
    found = subprocess.check_output([str(bin_dir / 'kpsewhich'), '--progname=lualatex',
                                     '--engine=luahbtex', '--format=fmt', 'lualatex.fmt'],
                                    cwd=runtime, env=env, text=True).strip()
    if Path(found).resolve() != (fmt / 'lualatex.fmt').resolve():
        raise ValueError(f'wrong LuaLaTeX format: {found}')
    subprocess.run([str(bin_dir / 'lualatex'), '--version'], env=env, check=True)
    (runtime / 'runtime.json').write_text(json.dumps({
        'engine': f'texlive/bin/{arch}/lualatex', 'font_dir': 'fonts'
    }, indent=2) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('resources', type=Path, help='extracted resource directory')
    parser.add_argument('runtime', type=Path, help='new runtime directory')
    args = parser.parse_args()
    stage(args.resources, args.runtime)


if __name__ == '__main__':
    main()
