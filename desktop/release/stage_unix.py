"""Stage a relocatable TeX Live runtime from an extracted resource export."""
import argparse
import importlib.util
import json
import lzma
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import tempfile

spec = importlib.util.spec_from_file_location('runtime_setup', Path(__file__).resolve().parents[1] / 'qa/runtime/setup.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)
BASE = setup.BASE
TLPDB_SHA512 = setup.TLPDB_SHA512
download = setup.download
safe_extract_target = setup.safe_extract_target
verify = setup.verify
write_lsr = setup.write_lsr

# TeX Live 2022 historic tlnet-final Unix 패키지 고정 SHA-512 체크섬 / Pinned SHA-512 digests for Unix TeX Live 2022 packages.
UNIX_PACKAGE_SHA512: dict[str, str] = {
    'kpathsea': setup.PACKAGE_SHA512['kpathsea'],
    'kpathsea.universal-darwin': (
        'ccd90921eeafcc5c7a6ac5b91337e0a8362a8b8b13bbf2c75ac8067cbd4eeed4'
        '483daae07c42b5f67a8ae02a8c2b831bb7e6ae6e7ad257368137defd49311051'
    ),
    'luahbtex.universal-darwin': (
        'aa9b6f1f709f9d4ab9ab6ae33698583d1d21fce1dae2124cef59a92474f6e6f5'
        '42d2efa3d95ed4ef52359062b96cf486c7f0f1f21fdebe5dde7dc5abf2df477b'
    ),
    'latex-bin.universal-darwin': (
        '45abdf9fec79500491f0f3c5511aa2a8a25521ee9bf648e9f81ea667c194aac2'
        'bdf76267e0f4276cfbd70e4b8f3c1f109129c77030138ba43f51f4e6e0d291f8'
    ),
    'kpathsea.x86_64-linux': (
        '360da968ba0ff35f47ce2fa220b5fdcdf45f11d1d27fc48544ff61058d80956de'
        '0ecde02ce5db80f26b1def558899f792cd07a2e50db4fa7558777826b7d086c'
    ),
    'luahbtex.x86_64-linux': (
        '51ce4d7673519232fb915bfd3967cca8546d3113288a72217a688b4748e6179a'
        'c29ec2416ace301ad9cf64a9f96f10f9050b8500501ff584db425763c5cd2712'
    ),
    'latex-bin.x86_64-linux': (
        '4822d2b4bab3a785fd2d53d845b52178b4d7c8b82cef29757cddaa7d08bfb34a'
        '72583c4f51e5c8267240cdd5f5df8e41fe9ffc472d2b27ef4e80942c462c1ce2'
    ),
}


def extract_archive(archive: Path, tex: Path, arch: str) -> None:
    wanted = {f'bin/{arch}/luahbtex', f'bin/{arch}/kpsewhich',
              f'bin/{arch}/lualatex', 'texmf-dist/web2c/texmf.cnf'}
    with tarfile.open(archive) as bundle:
        for member in bundle.getmembers():
            target = safe_extract_target(tex, member.name)
            if member.name not in wanted:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if member.isfile():
                with bundle.extractfile(member) as source, target.open('wb') as output:
                    shutil.copyfileobj(source, output)
                target.chmod(member.mode & 0o777)
            elif member.name == f'bin/{arch}/lualatex' and member.issym() and member.linkname == 'luahbtex':
                shutil.copy2(target.parent / member.linkname, target)
            else:
                raise ValueError(f'unsupported archive member: {member.name}')


def stage(resources: Path | str, runtime: Path | str) -> None:
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
        # 원격 TLPDB 자체의 고정 SHA-512 검증 / Verify downloaded TLPDB against pinned SHA-512.
        verify(database, TLPDB_SHA512)
        records: dict[str, str] = {}
        for record in lzma.decompress(database.read_bytes()).decode().split('\n\n'):
            lines = dict(line.split(' ', 1) for line in record.splitlines() if ' ' in line)
            if lines.get('name') in packages:
                records[lines['name']] = lines['containerchecksum']
        if set(records) != set(packages):
            raise ValueError('missing package checksums in TeX Live database')
        for package in packages:
            pinned = UNIX_PACKAGE_SHA512[package]
            if records[package] != pinned:
                raise ValueError(f'TLPDB checksum does not match pinned digest: {package}')
            archive = cache / (package + '.tar.xz')
            download(BASE + 'archive/' + archive.name, archive)
            verify(archive, pinned)
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
