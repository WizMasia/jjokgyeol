from pathlib import Path
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / '.github/workflows/native-release.yml'
FILES = (
    'jjokgyeol-linux-x86_64.deb',
    'jjokgyeol-macos-arm64.dmg',
    'jjokgyeol-windows-x64-setup.exe',
    'JjokgyeolPortable_0.1.1-preview.7.paf.exe',
)


class InventoryOutputTests(unittest.TestCase):
    def test_windows_asset_list_uses_lf_with_crlf_stdout(self):
        result = subprocess.check_output([
            sys.executable, '-c',
            r'import runpy, sys; sys.stdout.reconfigure(newline="\r\n"); '
            'sys.path.insert(0, "desktop/release"); runpy.run_path(sys.argv.pop(1), run_name="__main__")',
            'desktop/release/assets.py', 'v0.1.1-preview.7', '--platform', 'windows',
        ], cwd=ROOT)
        self.assertEqual(result, ('\n'.join(FILES[-2:]) + '\n').encode('ascii'))


@unittest.skipIf(os.name == 'nt', 'draft preflight runs on Linux')
class DraftInventoryTests(unittest.TestCase):
    def test_draft_requires_exact_assets_before_writing_checksums(self):
        # Exercise the actual draft preflight without network or write credentials.
        draft = WORKFLOW.read_text(encoding='utf-8').split('- name: Create and verify draft prerelease')[1]
        script = textwrap.dedent(draft.split('        run: |\n')[1].split('          sha256sum -c')[0])
        for names, expected in ((FILES[:-1], False), (FILES, True), ((*FILES, 'unexpected.exe'), False)):
            with self.subTest(names=names), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary) / 'assets'
                directory.mkdir()
                # Match the workflow layout while using this interpreter locally.
                (directory.parent / 'desktop').symlink_to(ROOT / 'desktop', target_is_directory=True)
                for name in names:
                    (directory / name).write_bytes(b'release file')
                result = subprocess.run(['bash', '-e', '-c', script.replace('python3 ', '"$TEST_PYTHON" ')], cwd=directory,
                                        env={**os.environ, 'TAG': 'v0.1.1-preview.7', 'TEST_PYTHON': sys.executable}, capture_output=True, text=True)
                self.assertEqual(result.returncode == 0, expected, result.stderr)
                sums = directory / 'SHA256SUMS'
                self.assertEqual(sums.exists(), expected)
                if expected:
                    self.assertEqual({line.split('  ')[1] for line in sums.read_text().splitlines()}, set(FILES))
