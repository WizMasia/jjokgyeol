import configparser
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from portable import (
    build, package_version, payload_inventory, stage,
    validate_inputs, validate_webview, verify_hash, verify_payload,
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

    def test_paf_contains_the_original_vendor_template(self):
        candidate = self.root / 'candidate.exe'
        self.pe(candidate)
        original = b'pinned original source'
        digest = hashlib.sha256(original).hexdigest()

        for source in (b'edited vendor source', original):
            def extract(*arguments):
                if arguments[1] == 'x':
                    output = Path(arguments[3][2:])
                    template = output / 'Other/Source/PortableApps.comInstaller.nsi'
                    template.parent.mkdir(parents=True)
                    template.write_bytes(source)
                    self.pe(output / 'JjokgyeolPortable.exe')
                    for name in ('appinfo.ini', 'EULA.txt', 'Launcher/JjokgyeolPortable.ini'):
                        file = output / 'App/AppInfo' / name
                        file.parent.mkdir(parents=True, exist_ok=True)
                        file.write_bytes(b'fixture')

            with self.subTest(source=source), patch('portable.run', side_effect=extract):
                if source == original:
                    verify_payload(Path('7z'), candidate, {}, digest)
                else:
                    with self.assertRaisesRegex(ValueError, 'changed payload.*PortableApps.comInstaller.nsi'):
                        verify_payload(Path('7z'), candidate, {}, digest)

    def runtime_inputs(self):
        executable = self.root / 'compiled.exe'
        self.pe(executable)
        runtime = self.root / 'runtime'
        lock_path = Path(__file__).resolve().parents[2] / 'runtime.lock'
        lock = json.loads(lock_path.read_text(encoding='utf-8'))
        fonts = {Path(record['path']).name for family in lock['fonts'].values()
                 for record in family.values()} | set(lock['document_font_files'])
        for name in ('worker/jjokgyeol-worker.exe', 'texlive/lualatex.exe',
                     'licenses/debian/fonts-nanum.copyright', 'licenses/other.txt',
                     *(f'fonts/{name}' for name in fonts)):
            path = runtime / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'fixture')
        (runtime / 'runtime.json').write_text(
            '{"engine":"texlive/lualatex.exe","font_dir":"fonts"}', encoding='ascii')
        return executable, runtime

    def test_requires_locked_fonts_and_nanum_notice(self):
        executable, runtime = self.runtime_inputs()
        validate_inputs(executable, runtime)
        for name in ('fonts/NanumMyeongjo.ttf', 'fonts/NanumSquareRoundR.ttf',
                     'licenses/debian/fonts-nanum.copyright'):
            path = runtime / name
            with self.subTest(name=name):
                path.unlink()
                with self.assertRaisesRegex(ValueError, path.name):
                    validate_inputs(executable, runtime)
                path.write_bytes(b'fixture')

    def test_stage(self):
        executable, runtime = self.runtime_inputs()
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
        for name in ('runtime/texlive/lualatex.exe', 'runtime/fonts/NanumMyeongjo.ttf',
                     'runtime/licenses/debian/fonts-nanum.copyright', 'WebView2/158.0.4370.33.manifest',
                     'WebView2/icudtl.dat', 'WebView2/Locales/en-US.pak'):
            self.assertIn('App/Jjokgyeol/' + name, inventory)
        candidate = self.root / 'candidate.exe'
        self.pe(candidate)
        with patch('portable.run') as run:
            with self.assertRaisesRegex(ValueError, 'missing payload'):
                verify_payload(Path('7z'), candidate, inventory, 'unused-after-missing-payload')
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
