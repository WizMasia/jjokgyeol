import hashlib
import importlib.util
import io
import json
import lzma
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).with_name('stage_unix.py')


@unittest.skipIf(os.name == 'nt', 'Unix runtime requires Unix executable scripts and symlinks')
class StageUnixTest(unittest.TestCase):
    def test_stages_relocatable_runtime_with_verified_archives(self):
        self.assertTrue(SCRIPT.exists(), 'stage_unix.py must exist')
        spec = importlib.util.spec_from_file_location('stage_unix', SCRIPT)
        stage = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(stage)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            resources = root / 'resources'
            for name in ('texmf-dist', 'nanum', 'licenses'):
                (resources / name).mkdir(parents=True)
                (resources / name / 'example').write_text(name)
            archives = {}
            for package in ('kpathsea', 'kpathsea.universal-darwin', 'luahbtex.universal-darwin', 'latex-bin.universal-darwin'):
                data = io.BytesIO()
                with tarfile.open(fileobj=data, mode='w:xz') as tar:
                    if package == 'kpathsea':
                        files = {'texmf-dist/web2c/texmf.cnf': b'TEXMFROOT = $SELFAUTOPARENT\n'}
                    elif package.startswith('kpathsea.'):
                        files = {'bin/universal-darwin/kpsewhich': b'#!/bin/sh\nprintf "%s\\n" "$TEXMFCACHE/web2c/luahbtex/lualatex.fmt"\n'}
                    elif package.startswith('luahbtex.'):
                        files = {'bin/universal-darwin/luahbtex': b'#!/bin/sh\n[ "$1" = --version ] || touch lualatex.fmt\n'}
                    else:
                        files = {}
                        link = tarfile.TarInfo('bin/universal-darwin/lualatex')
                        link.type = tarfile.SYMTYPE
                        link.linkname = 'luahbtex'
                        tar.addfile(link)
                        dangling = tarfile.TarInfo('bin/universal-darwin/pdflatex')
                        dangling.type = tarfile.SYMTYPE
                        dangling.linkname = 'pdftex'
                        tar.addfile(dangling)
                    for name, content in files.items():
                        item = tarfile.TarInfo(name)
                        item.mode = 0o755 if name.startswith('bin/') else 0o644
                        item.size = len(content)
                        tar.addfile(item, io.BytesIO(content))
                archives[package] = data.getvalue()
            digests = {name: hashlib.sha512(data).hexdigest() for name, data in archives.items()}
            database = lzma.compress('\n\n'.join(f'name {name}\ncontainerchecksum {digest}' for name, digest in digests.items()).encode())
            def download(url, dest):
                dest.write_bytes(database if url.endswith('texlive.tlpdb.xz') else archives[dest.name[:-7]])
            with (
                patch.object(stage, 'download', side_effect=download),
                patch.object(stage, 'TLPDB_SHA512', hashlib.sha512(database).hexdigest()),
                patch.object(stage, 'UNIX_PACKAGE_SHA512', digests),
                patch.object(stage.platform, 'system', return_value='Darwin'),
                patch.object(stage.platform, 'machine', return_value='arm64'),
            ):
                stage.stage(resources, root / 'runtime')
            runtime = root / 'runtime'
            self.assertEqual(json.loads((runtime / 'runtime.json').read_text()), {'engine': 'texlive/bin/universal-darwin/lualatex', 'font_dir': 'fonts'})
            engine = runtime / 'texlive/bin/universal-darwin/lualatex'
            self.assertFalse(engine.is_symlink())
            self.assertEqual(engine.resolve().name, 'lualatex')
            self.assertEqual(engine.read_bytes(), (engine.parent / 'luahbtex').read_bytes())
            self.assertTrue(engine.stat().st_mode & 0o111)
            self.assertFalse((runtime / 'texlive/bin/universal-darwin/pdflatex').is_symlink())
            self.assertTrue((runtime / 'texlive/bin/universal-darwin/luahbtex').stat().st_mode & 0o111)
            self.assertTrue((runtime / 'texlive/texmf-var/web2c/luahbtex/lualatex.fmt').is_file())
            self.assertEqual((runtime / 'licenses/example').read_text(), 'licenses')
            self.assertEqual((runtime / 'fonts/example').read_text(), 'nanum')
            self.assertFalse((runtime / 'downloads').exists())

    def test_pinned_checksums_cover_all_unix_packages(self):
        spec = importlib.util.spec_from_file_location('stage_unix', SCRIPT)
        stage = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(stage)
        expected = {
            'kpathsea',
            'kpathsea.universal-darwin', 'luahbtex.universal-darwin', 'latex-bin.universal-darwin',
            'kpathsea.x86_64-linux', 'luahbtex.x86_64-linux', 'latex-bin.x86_64-linux',
        }
        self.assertEqual(set(stage.UNIX_PACKAGE_SHA512), expected)
        self.assertEqual(len(stage.TLPDB_SHA512), 128)
        int(stage.TLPDB_SHA512, 16)
        for digest in stage.UNIX_PACKAGE_SHA512.values():
            self.assertEqual(len(digest), 128)
            int(digest, 16)

    def test_rejects_archive_traversal(self):
        spec = importlib.util.spec_from_file_location('stage_unix', SCRIPT)
        stage = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(stage)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / 'package.tar.xz'
            with tarfile.open(archive, 'w:xz') as bundle:
                member = tarfile.TarInfo('bin/universal-darwin/../../escape')
                member.size = 1
                bundle.addfile(member, io.BytesIO(b'x'))
            with self.assertRaisesRegex(ValueError, 'unsafe archive'):
                stage.extract_archive(archive, root / 'texlive', 'universal-darwin')
            self.assertFalse((root / 'texlive/escape').exists())

    def test_rejects_engine_link_to_parent_directory(self):
        spec = importlib.util.spec_from_file_location('stage_unix', SCRIPT)
        stage = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(stage)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / 'package.tar.xz'
            with tarfile.open(archive, 'w:xz') as bundle:
                member = tarfile.TarInfo('bin/universal-darwin/lualatex')
                member.type = tarfile.SYMTYPE
                member.linkname = '../../escape'
                bundle.addfile(member)
            with self.assertRaisesRegex(ValueError, 'unsupported archive'):
                stage.extract_archive(archive, root / 'texlive', 'universal-darwin')


if __name__ == '__main__':
    unittest.main()
