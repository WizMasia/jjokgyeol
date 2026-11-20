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

class BacktraceTest(unittest.TestCase):
    def test_running_process_backtrace_records_debugger_output(self):
        import tempfile
        from types import SimpleNamespace
        process = SimpleNamespace(pid=123, poll=lambda: None)
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(smoke.subprocess, 'run') as run:
                run.return_value = SimpleNamespace(returncode=0, stdout='thread backtrace\n')
                smoke.backtrace(Path(directory), process)
            self.assertIn('thread backtrace', (Path(directory) / 'app-backtrace.log').read_text())
            self.assertIn('thread apply all bt', run.call_args.args[0])
            self.assertEqual(run.call_args.kwargs['timeout'], 10)

    def test_debugger_failure_is_recorded(self):
        import tempfile
        from types import SimpleNamespace
        process = SimpleNamespace(pid=123, poll=lambda: None)
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(smoke.subprocess, 'run', side_effect=FileNotFoundError('gdb missing')):
                smoke.backtrace(Path(directory), process)
            self.assertIn('gdb missing', (Path(directory) / 'app-backtrace.log').read_text())

    def test_debugger_timeout_is_recorded(self):
        import subprocess
        import tempfile
        from types import SimpleNamespace
        process = SimpleNamespace(pid=123, poll=lambda: None)
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(smoke.subprocess, 'run', side_effect=subprocess.TimeoutExpired('gdb', 10)):
                smoke.backtrace(Path(directory), process)
            self.assertIn('timed out', (Path(directory) / 'app-backtrace.log').read_text())


if __name__ == '__main__':
    unittest.main()
