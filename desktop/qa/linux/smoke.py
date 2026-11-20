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


def window_found(pid):
    for window in WINDOW.findall(query('xwininfo', '-root', '-tree')):
        properties = query('xprop', '-id', window, '_NET_WM_NAME', '_NET_WM_PID')
        if not re.search(r'^_NET_WM_NAME\(UTF8_STRING\) = "쪽결"$', properties, re.MULTILINE):
            continue
        if not re.search(rf'^_NET_WM_PID\(CARDINAL\) = {pid}$', properties, re.MULTILINE):
            continue
        state = query('xwininfo', '-id', window)
        if re.search(r'^\s*Map State: IsViewable\s*$', state, re.MULTILINE):
            return True
    return False


def diagnostics(results, process, wm):
    tree = query('xwininfo', '-root', '-tree')
    (results / 'window-tree.txt').write_text(tree, encoding='utf-8')
    with (results / 'window-details.txt').open('w', encoding='utf-8') as output:
        output.write(f'wm pid={wm.pid} exit={wm.poll()}\n')
        if process is not None:
            output.write(f'app pid={process.pid} exit={process.poll()}\n')
            status = Path(f'/proc/{process.pid}/status')
            if status.exists():
                output.write(status.read_text(encoding='utf-8', errors='replace'))
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
                    print('PASS: mapped UTF-8 쪽결 window belongs to the launched process')
                    return
                time.sleep(0.25)
            raise RuntimeError('GUI window did not appear within 30 seconds')
        except Exception:
            diagnostics(results, process, wm)
            raise
        finally:
            if process is not None:
                stop(process)
            stop(wm)


if __name__ == '__main__':
    main()
