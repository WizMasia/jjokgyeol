"""Activate a named accessible control in one application's named window."""
import sys
import time


def find_control(app, window, name, frame_role, button_role):
    def descend(node):
        yield node
        for child in node:
            if child is not None:
                yield from descend(child)

    for frame in app:
        if frame is not None and frame.getRole() == frame_role and frame.name == window:
            for node in descend(frame):
                if node.getRole() == button_role and node.name == name:
                    return node
    return None


def main():
    import pyatspi

    pid, window, name = int(sys.argv[1]), sys.argv[2], sys.argv[3]
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        try:
            for app in pyatspi.Registry.getDesktop(0):
                if app is not None and app.get_process_id() == pid:
                    control = find_control(app, window, name, pyatspi.ROLE_FRAME, pyatspi.ROLE_PUSH_BUTTON)
                    if control:
                        action = control.queryAction()
                        if action.nActions and action.doAction(0):
                            print(f'Activated {name} in {window}')
                            return
        except (LookupError, RuntimeError):
            pass
        time.sleep(0.25)
    raise RuntimeError(f'Accessible control {name!r} not available in {window!r} for PID {pid}')


if __name__ == '__main__':
    main()
