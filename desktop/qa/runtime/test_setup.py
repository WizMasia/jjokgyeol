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
