import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('smoke', Path(__file__).with_name('smoke.py'))
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class SmokeTest(unittest.TestCase):
    def test_search_uses_process_and_exact_title(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'xdotool'
            executable.write_text('#!/bin/sh\n[ "$*" = "search --onlyvisible --pid 123 --name ^쪽결$" ]\n')
            executable.chmod(0o755)
            with patch.dict(os.environ, {'PATH': directory}):
                self.assertTrue(smoke.window_found(123))
                self.assertFalse(smoke.window_found(124))


if __name__ == '__main__':
    unittest.main()
