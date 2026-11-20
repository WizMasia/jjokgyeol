import json
import tempfile
import unittest
from pathlib import Path

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


if __name__ == '__main__':
    unittest.main()
