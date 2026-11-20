"""One private process group and working directory per render (POSIX)."""
import asyncio
import ctypes
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory

if __name__ == "__main__":
    # -I excludes the request directory. Import only from the installed app.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.operations import LIMITS

TIMEOUT = LIMITS.timeout_seconds
MAX_FILE = LIMITS.worker_file_bytes
WORKER_CMD = (sys.executable, "-IB", str(Path(__file__).resolve()))
INVALID_INPUT = 64


class JobError(Exception):
    def __init__(self, status, field=None, message=None):
        self.status = status
        self.field = field
        self.message = message


def limit_worker_files():
    _, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
    limit = MAX_FILE if hard == resource.RLIM_INFINITY else min(MAX_FILE, hard)
    resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def worker():
    from app.document import InputError, parse_md
    from app.style import Style, apply_style
    from app.typeset import typeset

    if os.geteuid() == 0:
        raise RuntimeError("unprivileged worker required")
    os.umask(0o077)
    limit_worker_files()
    workdir = Path.cwd()
    request = workdir / "request.json"
    try:
        payload = json.loads(request.read_bytes().decode("utf-8"), parse_float=Decimal, parse_int=Decimal)
    except (ValueError, InvalidOperation, RecursionError):
        raise JobError(422) from None
    request.unlink()
    if not isinstance(payload, dict) or payload.keys() - {"source", "style"}:
        raise JobError(422)
    try:
        doc = parse_md(payload.get("source"))
        style = apply_style(Style(), payload.get("style"))
        pdf = typeset(doc, style, workdir)
    except InputError as error:
        # Only the public input error crosses the process boundary.
        raise JobError(422, error.field, error.message) from None

    if not pdf.startswith(b"%PDF-") or not pdf[-1024:].rstrip().endswith(b"%%EOF"):
        raise ValueError("incomplete PDF")
    (workdir / "response.pdf").write_bytes(pdf)


def stop_group(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()
    if sys.platform == "linux":
        # Reap orphaned engines even if the container's PID 1 is not an init.
        while True:
            try:
                os.waitpid(-process.pid, 0)
            except ChildProcessError:
                break


async def render(body):
    return await isolated_job(body, WORKER_CMD, "request.json", "response.pdf")


async def isolated_job(body, command, request_name, response_name):
    if sys.platform == "linux":
        # PR_SET_CHILD_SUBREAPER adopts orphaned descendants for group-scoped waitpid.
        if ctypes.CDLL(None).prctl(36, 1, 0, 0, 0) != 0:
            raise OSError("cannot supervise render descendants")
    with TemporaryDirectory(prefix="jjokgyeol-") as tmp:
        workdir = Path(tmp)
        (workdir / request_name).write_bytes(body)
        process = subprocess.Popen(
            command, cwd=workdir, start_new_session=True,
            env={**os.environ, "TMPDIR": tmp}, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            while process.poll() is None:
                await asyncio.sleep(.015)
        finally:
            stop_group(process)
        status = process.returncode
        if status == INVALID_INPUT and (workdir / "invalid.json").is_file():
            field, message = json.loads((workdir / "invalid.json").read_text("utf-8"))
            raise JobError(422, field, message)
        if status != 0:
            raise JobError(500)
        return (workdir / response_name).read_bytes()


if __name__ == "__main__":
    try:
        worker()
    except JobError as exc:
        Path("invalid.json").write_text(json.dumps([exc.field, exc.message]), "utf-8")
        sys.exit(INVALID_INPUT)
