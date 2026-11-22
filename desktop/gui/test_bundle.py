"""Unit tests for desktop/gui/bundle.py runtime validation and security boundaries."""
import json
import os
from pathlib import Path
import tempfile
import unittest

import bundle


class BundleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'worker').mkdir()
        (self.root / 'worker' / bundle.worker_name()).write_bytes(b'worker')
        (self.root / 'fonts').mkdir()
        (self.root / 'engine').write_bytes(b'engine')
        (self.root / 'runtime.json').write_text(json.dumps({'engine': 'engine', 'font_dir': 'fonts'}))

    def test_valid_runtime_maps_to_runtime_resource(self):
        self.assertEqual(bundle.resource_config(self.root)['bundle']['resources'], {str(self.root.resolve()): 'runtime'})

    def test_windows_includes_offline_webview_installer(self):
        config = bundle.resource_config(self.root, platform='win32')['bundle']
        self.assertEqual(config['windows']['webviewInstallMode'], {'type': 'offlineInstaller'})
        self.assertNotIn('windows', bundle.resource_config(self.root, platform='darwin')['bundle'])

    def test_rejects_missing_worker_invalid_manifest_and_path_escape(self):
        (self.root / 'worker' / bundle.worker_name()).unlink()
        with self.assertRaises(ValueError):
            bundle.resource_config(self.root)
        (self.root / 'worker' / bundle.worker_name()).write_bytes(b'worker')
        (self.root / 'runtime.json').write_text('{')
        with self.assertRaises(ValueError):
            bundle.resource_config(self.root)
        (self.root / 'runtime.json').write_text(json.dumps({'engine': '../engine', 'font_dir': 'fonts'}))
        with self.assertRaises(ValueError):
            bundle.resource_config(self.root)

    def test_rejects_missing_frontend_assets(self):
        with self.assertRaises(ValueError):
            bundle.validate_frontend(self.root)


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
