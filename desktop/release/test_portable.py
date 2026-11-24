import configparser
import hashlib
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from portable import (
    build, package_version, payload_inventory, prepare_installer_template, stage,
    validate_webview, verify_hash, verify_payload,
)


class PortableTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_release_versions(self):
        self.assertEqual(package_version('v0.1.1-preview.7'), ('0.1.1-preview.7', '0.1.1.7'))
        self.assertEqual(package_version('v0.1.1'), ('0.1.1', '0.1.1.65535'))
        for tag in ('', '0.1.1', 'v00.1.1', 'v0.1.1-preview.0', 'v65536.0.0',
                    'v0.1.1-preview.65535', 'v0.1.1+local', 'v0.1.1\n'):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                package_version(tag)

    def test_dependency_integrity(self):
        path = self.root / 'dependency'
        digest = hashlib.sha256(b'expected').hexdigest()
        with self.assertRaisesRegex(ValueError, 'Missing'):
            verify_hash(path, digest)
        path.write_bytes(b'expected')
        verify_hash(path, digest)
        path.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            verify_hash(path, digest)

    def pe(self, path, machine=0x8664):
        block = bytearray(128)
        block[:2] = b'MZ'
        struct.pack_into('<I', block, 60, 64)
        struct.pack_into('<4sH', block, 64, b'PE\0\0', machine)
        struct.pack_into('<H', block, 88, 0x20b if machine == 0x8664 else 0x10b)
        path.write_bytes(block)

    def webview(self):
        source = self.root / 'Microsoft.WebView2.FixedVersionRuntime.158.0.4370.33.x64'
        (source / 'Locales').mkdir(parents=True)
        for name in ('158.0.4370.33.manifest', 'icudtl.dat', 'Locales/en-US.pak'):
            (source / name).write_bytes(b'fixture')
        for name in ('msedgewebview2.exe', 'msedge.dll'):
            self.pe(source / name)
        return source

    def test_webview_layout(self):
        webview = self.webview()
        validate_webview(webview, '158.0.4370.33')
        self.pe(webview / 'msedgewebview2.exe', 0x14c)
        with self.assertRaisesRegex(ValueError, 'x64'):
            validate_webview(webview, '158.0.4370.33')
        with self.assertRaises(ValueError):
            validate_webview(webview.parent, '158.0.4370.33')

    def test_rejects_an_existing_release_output_before_building(self):
        output = self.root / 'JjokgyeolPortable_0.1.1-preview.7.paf.exe'
        output.write_bytes(b'existing release')
        with self.assertRaisesRegex(FileExistsError, 'Output already exists'):
            build(self.root / 'missing.exe', self.root / 'runtime', self.root, 'v0.1.1-preview.7')
        self.assertEqual(output.read_bytes(), b'existing release')

    def test_installer_guard_precedes_destination_writes(self):
        template = self.root / 'installer.nsi'
        original = ('\tSetSilent normal\n\tSectionIn RO\n\tSetOutPath $INSTDIR\n'
                    '\t!ifmacrodef CustomCodePreInstall\n'
                    '\t\t!insertmacro CustomCodePreInstall\n\t!endif\n'
                    '\tRMDir /r "$INSTDIR\\App"\n')
        template.write_text(original, encoding='utf-16')
        prepare_installer_template(template)
        result = template.read_text(encoding='utf-16')
        hook = '!insertmacro CustomCodePreInstall'
        self.assertEqual(result.count(hook), 1)
        self.assertLess(result.index(hook), result.index('SetOutPath $INSTDIR'))
        self.assertNotIn('SetSilent normal', result)
        with self.assertRaisesRegex(ValueError, 'Unexpected PortableApps'):
            prepare_installer_template(template)

    def test_stage(self):
        executable = self.root / 'compiled.exe'
        self.pe(executable)
        runtime = self.root / 'runtime'
        for name in ('worker/jjokgyeol-worker.exe', 'texlive/lualatex.exe', 'fonts/font.ttf',
                     'licenses/notice.txt'):
            path = runtime / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'fixture')
        (runtime / 'runtime.json').write_text(
            '{"engine":"texlive/lualatex.exe","font_dir":"fonts"}', encoding='ascii')
        package = self.root / 'JjokgyeolPortable'
        webview = self.webview()
        stage(package, executable, runtime, webview, 'v0.1.1-preview.7')
        app_info = package / 'App/AppInfo'
        ini = configparser.ConfigParser(interpolation=None)
        ini.read(app_info / 'appinfo.ini', encoding='utf-16')
        self.assertEqual(ini['Details']['AppID'], 'JjokgyeolPortable')
        self.assertEqual(ini['Version']['PackageVersion'], '0.1.1.7')
        self.assertEqual(ini['Version']['DisplayVersion'], '0.1.1-preview.7')
        ini.clear()
        ini.read(app_info / 'Launcher/JjokgyeolPortable.ini')
        self.assertEqual(ini['Environment']['WEBVIEW2_BROWSER_EXECUTABLE_FOLDER'],
                         r'%PAL:AppDir%\Jjokgyeol\WebView2')
        self.assertEqual(ini['Environment']['WEBVIEW2_USER_DATA_FOLDER'], r'%PAL:DataDir%\WebView2')
        self.assertTrue((package / 'App/Jjokgyeol/runtime/runtime.json').is_file())
        self.assertTrue((package / 'App/Jjokgyeol/WebView2/msedgewebview2.exe').is_file())
        inventory = payload_inventory(package, '158.0.4370.33')
        for name in ('runtime/texlive/lualatex.exe', 'runtime/fonts/font.ttf',
                     'runtime/licenses/notice.txt', 'WebView2/158.0.4370.33.manifest',
                     'WebView2/icudtl.dat', 'WebView2/Locales/en-US.pak'):
            self.assertIn('App/Jjokgyeol/' + name, inventory)
        candidate = self.root / 'candidate.exe'
        self.pe(candidate)
        with patch('portable.run') as run:
            with self.assertRaisesRegex(ValueError, 'missing payload'):
                verify_payload(Path('7z'), candidate, inventory)
            self.assertEqual(run.call_args_list[0].args[1], 't')
        (package / 'App/Jjokgyeol/WebView2/icudtl.dat').unlink()
        with self.assertRaisesRegex(ValueError, 'icudtl.dat'):
            payload_inventory(package, '158.0.4370.33')
        self.assertEqual(list((package / 'Data').iterdir()), [])
        sentinel = package / 'Data/keep'
        sentinel.write_bytes(b'preserve')
        with self.assertRaises(FileExistsError):
            stage(package, executable, runtime, webview, 'v0.1.1-preview.7')
        self.assertEqual(sentinel.read_bytes(), b'preserve')
