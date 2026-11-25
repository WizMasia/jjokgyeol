import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('setup_runtime', Path(__file__).with_name('setup.py'))
setup = importlib.util.module_from_spec(spec)


class RuntimeSetupTest(unittest.TestCase):
    def test_embeds_utf8_manifest_with_newest_available_x64_sdk(self):
        spec.loader.exec_module(setup)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            kit = root / 'Windows Kits/10/bin'
            for path in ('10.0.9.0/x64', '10.0.10.0/x64', '10.0.11.0/x86', 'latest/x64'):
                tool = kit / path / 'mt.exe'
                tool.parent.mkdir(parents=True)
                tool.touch()
            bin_dir = root / 'runtime/texlive/bin/win32'
            with patch.dict(os.environ, {'ProgramFiles(x86)': temp}), patch.object(setup.subprocess, 'run') as run:
                setup.embed_utf8_manifests(bin_dir)
                expected_targets = [
                    str(bin_dir / name) for name in ('lualatex.exe', 'luahbtex.exe', 'kpsewhich.exe')
                ]
                self.assertEqual(len(run.call_args_list), len(expected_targets))
                for call, target in zip(run.call_args_list, expected_targets):
                    args = call.args[0]
                    self.assertEqual(Path(args[0]), kit / '10.0.10.0/x64/mt.exe')
                    self.assertIn('<activeCodePage>UTF-8</activeCodePage>', Path(args[3]).read_text())
                    self.assertEqual(args[4], f'-outputresource:{target};#1')
                    self.assertTrue(call.kwargs['check'])

    def test_requires_windows_sdk_manifest_tool(self):
        spec.loader.exec_module(setup)
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'ProgramFiles(x86)': temp}):
            with self.assertRaisesRegex(FileNotFoundError, 'Windows SDK.*mt.exe'):
                setup.embed_utf8_manifests(Path(temp))

    def test_windows_kpathsea_package_is_included(self):
        spec.loader.exec_module(setup)
        self.assertIn("kpathsea.win32", setup.PACKAGES)

    def test_database_contains_sorted_directories_and_files(self):
        spec.loader.exec_module(setup)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'z').mkdir()
            (root / 'z' / 'b.tex').write_text('x')
            (root / 'a.tex').write_text('x')
            setup.write_lsr(root)
            self.assertEqual((root / 'ls-R').read_text(),
                '% ls-R -- filename database for kpathsea; do not change this line.\n\n./:\na.tex\nls-R\nz\n\n./z:\nb.tex\n\n')

    def test_checksum_rejects_modified_archive(self):
        spec.loader.exec_module(setup)
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / 'archive'
            archive.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                setup.verify(archive, '0' * 128)

    def test_pinned_checksums_cover_all_packages(self):
        spec.loader.exec_module(setup)
        self.assertEqual(len(setup.TLPDB_SHA512), 128)
        int(setup.TLPDB_SHA512, 16)
        self.assertEqual(set(setup.PACKAGE_SHA512), set(setup.PACKAGES))
        for digest in setup.PACKAGE_SHA512.values():
            self.assertEqual(len(digest), 128)
            int(digest, 16)

    def test_safe_extract_target_blocks_traversal_and_absolute_paths(self):
        spec.loader.exec_module(setup)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'texlive'
            root.mkdir()
            valid = setup.safe_extract_target(root, 'bin/win32/lualatex.exe')
            self.assertEqual(valid, (root / 'bin/win32/lualatex.exe').resolve())
            for unsafe in (
                '',
                '../escape.exe',
                'bin/win32/../../escape.exe',
                '/etc/passwd',
                'C:/Windows/evil.exe',
                'bin\\win32\\evil.exe',
                'bin/win32/bad\x00.exe',
                '.',
            ):
                with self.subTest(unsafe=unsafe):
                    with self.assertRaisesRegex(ValueError, 'unsafe archive member path'):
                        setup.safe_extract_target(root, unsafe)
