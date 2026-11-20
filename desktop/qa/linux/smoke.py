"""Fail unless the launched process shows its expected X11 window."""
import subprocess
import sys
import time


def window_found(pid):
    return subprocess.run(
        ['xdotool', 'search', '--onlyvisible', '--pid', str(pid), '--name', '^쪽결$'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    ).returncode == 0


def main():
    process = subprocess.Popen([sys.argv[1]])
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f'GUI exited early: {process.returncode}')
            if window_found(process.pid):
                print('PASS: visible 쪽결 window belongs to the launched process')
                return
            time.sleep(0.25)
        raise RuntimeError('GUI window did not appear within 30 seconds')
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == '__main__':
    main()
