import tempfile
import unittest
from pathlib import Path

from verify_uninstall import check_removed, installed_files


class UninstallTest(unittest.TestCase):
    def test_user_files_and_empty_directories_can_remain(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'runtime').mkdir()
            exe = root / 'app.exe'
            exe.touch()
            shipped = installed_files(root)
            exe.unlink()
            (root / 'user.md').touch()
            self.assertEqual(check_removed(root, shipped), ['user.md'])

    def test_names_each_remaining_shipped_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'worker.exe').touch()
            with self.assertRaisesRegex(ValueError, 'worker.exe'):
                check_removed(root, installed_files(root))
