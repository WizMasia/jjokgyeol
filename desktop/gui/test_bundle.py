"""Unit tests for desktop/gui/bundle.py security boundaries."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('desktop_gui_bundle', Path(__file__).with_name('bundle.py'))
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


class BundleSecurityTest(unittest.TestCase):
    """Tauri 번들 CLI 로컬 해결 및 원격 자동 다운로드 차단 검증 / Verify local Tauri CLI resolution without remote auto-download."""

    def test_tauri_command_prefers_local_frontend_node_modules_binary(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            gui = Path(temp)
            binary_name = 'tauri.cmd' if os.name == 'nt' else 'tauri'
            local_bin = gui / 'frontend' / 'node_modules' / '.bin' / binary_name
            local_bin.parent.mkdir(parents=True)
            local_bin.write_text('#!/bin/sh\n', encoding='utf-8')
            self.assertEqual(bundle.tauri_command(gui), [str(local_bin)])

    def test_tauri_command_rejects_missing_local_install(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, 'local Tauri CLI'):
                bundle.tauri_command(Path(temp))

    def test_bundle_source_does_not_use_npm_exec_yes(self) -> None:
        source = Path(__file__).with_name('bundle.py').read_text(encoding='utf-8')
        self.assertNotIn("'--yes'", source)
        self.assertNotIn('"--yes"', source)


if __name__ == '__main__':
    unittest.main()
