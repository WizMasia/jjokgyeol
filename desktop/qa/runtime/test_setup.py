import importlib.util
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('setup_runtime', Path(__file__).with_name('setup.py'))
setup = importlib.util.module_from_spec(spec)


class RuntimeSetupTest(unittest.TestCase):
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
