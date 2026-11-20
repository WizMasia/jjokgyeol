import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('smoke', Path(__file__).with_name('smoke.py'))
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


class SmokeTest(unittest.TestCase):
    def test_requires_exact_utf8_title_pid_and_viewable_state(self):
        tree = '  0x100001 "frame": ()\n     0x200002 "child": ()\n'
        props = '_NET_WM_NAME(UTF8_STRING) = "쪽결"\n_NET_WM_PID(CARDINAL) = 123\n'
        def command(*args):
            if args == ('xwininfo', '-root', '-tree'):
                return tree
            if args[0] == 'xprop':
                return props if args[2] == '0x200002' else ''
            return '  Map State: IsViewable\n'
        with patch.object(smoke, 'query', side_effect=command):
            self.assertTrue(smoke.window_found(123))
            self.assertFalse(smoke.window_found(124))
        with patch.object(smoke, 'query', side_effect=lambda *args: tree if '-tree' in args else props if args[0] == 'xprop' else '  Map State: IsUnMapped\n'):
            self.assertFalse(smoke.window_found(123))
        with patch.object(smoke, 'query', side_effect=lambda *args: tree if '-tree' in args else props.replace('쪽결', '쪽결 extra') if args[0] == 'xprop' else '  Map State: IsViewable\n'):
            self.assertFalse(smoke.window_found(123))

    def test_window_manager_requires_supporting_window(self):
        with patch.object(smoke, 'query', return_value='_NET_SUPPORTING_WM_CHECK(WINDOW): window id # 0x200001\n'):
            self.assertTrue(smoke.wm_ready())
        with patch.object(smoke, 'query', return_value='_NET_SUPPORTING_WM_CHECK:  not found.\n'):
            self.assertFalse(smoke.wm_ready())

    def test_no_window_is_failure(self):
        with patch.object(smoke, 'query', return_value=''):
            self.assertFalse(smoke.window_found(123))


if __name__ == '__main__':
    unittest.main()
