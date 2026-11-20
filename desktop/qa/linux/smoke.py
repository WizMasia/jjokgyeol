"""Require the launched application's mapped X11 window, and save failure evidence."""
import os
from pathlib import Path
import re
import subprocess
import sys
import time


ENV = dict(os.environ, LC_ALL='C.UTF-8')
WINDOW = re.compile(r'^\s*(0x[0-9a-fA-F]+)\b', re.MULTILINE)


def query(*args):
    result = subprocess.run(args, capture_output=True, text=True, encoding='utf-8',
                            errors='replace', env=ENV, check=False)
    return result.stdout if result.returncode == 0 else ''


def wm_ready():
    return bool(re.search(r'_NET_SUPPORTING_WM_CHECK\(WINDOW\): window id # 0x[1-9a-fA-F][0-9a-fA-F]*',
                          query('xprop', '-root', '_NET_SUPPORTING_WM_CHECK')))


def find_window(pid, title):
    for window in WINDOW.findall(query('xwininfo', '-root', '-tree')):
        properties = query('xprop', '-id', window, '_NET_WM_NAME', '_NET_WM_PID')
        if not re.search(rf'^_NET_WM_NAME\(UTF8_STRING\) = "{re.escape(title)}"$', properties, re.MULTILINE):
            continue
        if not re.search(rf'^_NET_WM_PID\(CARDINAL\) = {pid}$', properties, re.MULTILINE):
            continue
        state = query('xwininfo', '-id', window)
        if re.search(r'^\s*Map State: IsViewable\s*$', state, re.MULTILINE):
            return window
    return None


def window_found(pid):
    return find_window(pid, '쪽결') is not None


def activate_control(pid, window, name):
    subprocess.run(['/usr/bin/python3', str(Path(__file__).with_name('activate.py')),
                    str(pid), window, name], env=ENV, check=True, timeout=30)


def backtrace(results, process):
    log = results / 'app-backtrace.log'
    if process is None or process.poll() is not None:
        log.write_text('App is not running; no backtrace available.\n', encoding='utf-8')
        return
    command = ['timeout', '--kill-after=1s', '8s']
    command += (['sudo', '-n'] if os.environ.get('JJOKGYEOL_GDB_SUDO') == '1' else [])
    command += ['gdb', '--batch', '-ex', 'set pagination off', '-ex', 'thread apply all bt',
                '-p', str(process.pid)]
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding='utf-8', errors='replace', env=ENV,
                                timeout=10, check=False)
        log.write_text(f'gdb exit={result.returncode}\n{result.stdout}', encoding='utf-8')
    except (OSError, subprocess.TimeoutExpired) as error:
        log.write_text(f'Could not capture backtrace: {error}\n', encoding='utf-8')


def diagnostics(results, process, wm):
    backtrace(results, process)
    tree = query('xwininfo', '-root', '-tree')
    (results / 'window-tree.txt').write_text(tree, encoding='utf-8')
    with (results / 'window-details.txt').open('w', encoding='utf-8') as output:
        output.write(f'wm pid={wm.pid} exit={wm.poll()}\n')
        if process is not None:
            output.write(f'app pid={process.pid} exit={process.poll()}\n')
            status = Path(f'/proc/{process.pid}/status')
            if status.exists():
                output.write(status.read_text(encoding='utf-8', errors='replace'))
            output.write('\nthread wait channels:\n')
            for thread in sorted(Path(f'/proc/{process.pid}/task').glob('*/wchan')):
                try:
                    output.write(f'{thread.parent.name}: {thread.read_text().strip()}\n')
                except OSError:
                    pass
        output.write('\nprocess tree:\n')
        output.write(query('ps', '-eo', 'pid,ppid,stat,wchan:32,comm'))
        for window in WINDOW.findall(tree):
            output.write(f'\n--- {window} ---\n')
            output.write(query('xwininfo', '-id', window))
            output.write(query('xprop', '-id', window, '_NET_WM_NAME', '_NET_WM_PID'))
    with (results / 'screenshot.log').open('w') as log:
        subprocess.run(['xwd', '-root', '-silent', '-out', str(results / 'screen.xwd')],
                       stdout=log, stderr=log, env=ENV, check=False)


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main():
    results = Path(sys.argv[2])
    results.mkdir(parents=True, exist_ok=True)
    with (results / 'wm-stderr.log').open('w') as wm_log, (results / 'app-stderr.log').open('w') as app_log:
        wm = subprocess.Popen(['openbox'], stdout=subprocess.DEVNULL, stderr=wm_log, env=ENV)
        process = None
        try:
            deadline = time.monotonic() + 15
            while not wm_ready():
                if wm.poll() is not None:
                    raise RuntimeError(f'window manager exited early: {wm.returncode}')
                if time.monotonic() >= deadline:
                    raise RuntimeError('window manager did not become ready within 15 seconds')
                time.sleep(0.1)
            process = subprocess.Popen([sys.argv[1]], stdout=subprocess.DEVNULL, stderr=app_log, env=ENV)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if wm.poll() is not None:
                    raise RuntimeError(f'window manager exited early: {wm.returncode}')
                if process.poll() is not None:
                    raise RuntimeError(f'GUI exited early: {process.returncode}')
                if window_found(process.pid):
                    break
                time.sleep(0.25)
            else:
                raise RuntimeError('GUI window did not appear within 30 seconds')
            main_window = find_window(process.pid, '쪽결')
            activate_control(process.pid, '쪽결', '설정')
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f'GUI exited early: {process.returncode}')
                settings_window = find_window(process.pid, '출판 설정')
                if settings_window and settings_window != main_window:
                    break
                time.sleep(0.25)
            else:
                raise RuntimeError('Settings window did not appear within 30 seconds')
            activate_control(process.pid, '출판 설정', '출판 설정 닫기')
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f'GUI exited early: {process.returncode}')
                if find_window(process.pid, '출판 설정') is None:
                    if find_window(process.pid, '쪽결') != main_window:
                        raise RuntimeError('Main window is not visible after closing Settings')
                    print('PASS: Settings opened and closed; the original main window remains mapped')
                    return
                time.sleep(0.25)
            raise RuntimeError('Settings window did not close within 15 seconds')
        except Exception:
            diagnostics(results, process, wm)
            raise
        finally:
            if process is not None:
                stop(process)
            stop(wm)


if __name__ == '__main__':
    main()
