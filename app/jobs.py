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
from time import monotonic

if __name__ == "__main__":
    # -I excludes the request directory. Import only from the installed app.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.configuration import unique_fields
from app.operations import LIMITS
from app.security import complete_pdf, harden_worker, worker_env

TIMEOUT = LIMITS.timeout_seconds
MAX_FILE = LIMITS.worker_file_bytes
WORKER_CMD = (sys.executable, "-IB", str(Path(__file__).resolve()))
INVALID_INPUT = 64


class JobError(Exception):
    def __init__(self, status, field=None, message=None, *, code=None):
        self.status = status
        self.field = field
        self.message = message
        self.code = code


def limit_worker_files():
    _, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
    limit = MAX_FILE if hard == resource.RLIM_INFINITY else min(MAX_FILE, hard)
    resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def connection_error(code, message="모델 연결 설정을 확인하세요."):
    status = {"rate_limit": 429, "unavailable": 502, "invalid_response": 502, "timeout": 504}.get(code, 422)
    return JobError(status, "connection", message, code=code)


def read_payload(workdir, checking):
    if "--stdin" in sys.argv:
        raw = sys.stdin.buffer.read(LIMITS.request_body_bytes + 1)
        if len(raw) > LIMITS.request_body_bytes:
            raise JobError(413)
    else:
        request = workdir / "request.json"
        raw = request.read_bytes()
        request.unlink()
    try:
        return json.loads(raw.decode("utf-8"), parse_float=Decimal, parse_int=Decimal,
                          object_pairs_hook=unique_fields)
    except (ValueError, InvalidOperation, RecursionError):
        if checking:
            raise connection_error("invalid_connection") from None
        raise JobError(422) from None


def worker():
    from app.document import InputError, parse_md
    from app.style import NEW_DOCUMENT_STYLE, from_editor_style
    from app.typeset import typeset

    harden_worker(max_file_bytes=MAX_FILE)
    workdir = Path.cwd()
    checking = "--check" in sys.argv
    payload = read_payload(workdir, checking)
    if not checking and (not isinstance(payload, dict)
                         or payload.keys() - {"source", "style", "decision", "connection"}):
        raise JobError(422)
    raw = payload if checking else payload.get("connection")
    key = raw.get("api_key") if isinstance(raw, dict) else None
    if "--keyless" in sys.argv and isinstance(key, str) and key:
        raise connection_error("address", "API 키를 사용하려면 HTTPS 또는 루프백 주소로 편집기를 여세요.")
    if checking:
        from app.ai.providers import ProviderError, check_connection
        try:
            check_connection(payload)
        except ProviderError as error:
            raise connection_error(error.code) from None
        print(json.dumps({"ok": True}))
        return
    compare = "--compare" in sys.argv
    mode = payload.get("decision", "model" if compare or "connection" in payload else "rule")
    if mode not in ("rule", "model"):
        raise JobError(422, "decision", "문단 선택 방식을 확인하세요.")
    connection = None
    reason = None
    if "connection" in payload:
        if raw == {"provider": "rule"}:
            mode = "rule"
        else:
            from app.ai.providers import ProviderError, parse_render_connection
            try:
                connection, reason = parse_render_connection(raw)
            except ProviderError as error:
                raise connection_error(error.code) from None
    status = "rule"
    try:
        doc = parse_md(payload.get("source"))
        fields = payload.get("style", {})
        style = NEW_DOCUMENT_STYLE if fields == {} else from_editor_style(fields)
        if reason:
            from app.ai.decision import measure_rule
            pdf = measure_rule(doc, style, workdir / "measure")
        elif mode == "model" and (connection is not None or os.getenv("JJOKGYEOL_DECISION_HOST")):
            from app.ai.decision import render_provider_choice
            deadline = float(sys.argv[1]) if len(sys.argv) > 1 else monotonic() + TIMEOUT
            pdf, status, reason = render_provider_choice(doc, style, workdir, deadline, connection)
        elif "decision" in payload:
            pdf = typeset(doc, style, workdir, collect_candidates=True)
        else:
            pdf = typeset(doc, style, workdir)
    except InputError as error:
        raise JobError(422, error.field, error.message) from None

    if not complete_pdf(pdf):
        raise ValueError("incomplete PDF")
    (workdir / "response.pdf").write_bytes(pdf)
    receipt = json.dumps([status, reason]) if "--stdin" in sys.argv else status
    (workdir / "decision-status").write_text(receipt, "ascii")


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


def decision_command(*args, allow_key):
    command = (*WORKER_CMD, *args, "--stdin")
    return command if allow_key else (*command, "--keyless")


async def render(body, deadline=None, *, compare=False, allow_key=True):
    with_decision = compare or deadline is not None
    if deadline is None:
        deadline = monotonic() + TIMEOUT
    command = decision_command(str(deadline), allow_key=allow_key)
    if compare:
        command += ("--compare",)
    return await isolated_job(body, command, None, "response.pdf", with_decision=with_decision, compare=compare)


async def isolated_job(
    body,
    command,
    request_name,
    response_name,
    env_role="render",
    *,
    with_decision=False,
    compare=False,
):
    """A missing request filename keeps the entire body in a pipe, including invalid JSON."""
    if sys.platform == "linux":
        # PR_SET_CHILD_SUBREAPER adopts orphaned descendants for group-scoped waitpid.
        if ctypes.CDLL(None).prctl(36, 1, 0, 0, 0) != 0:
            raise OSError("cannot supervise render descendants")
    with TemporaryDirectory(prefix="jjokgyeol-") as tmp:
        workdir = Path(tmp)
        if request_name is not None:
            (workdir / request_name).write_bytes(body)
        process = subprocess.Popen(
            command, cwd=workdir, start_new_session=True,
            env=worker_env(env_role, tmpdir=tmp),
            stdin=subprocess.PIPE if request_name is None else subprocess.DEVNULL,
            stdout=subprocess.PIPE if request_name is None else subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        pipe = None
        try:
            if request_name is None:
                pipe = asyncio.create_task(asyncio.to_thread(process.communicate, body))
            while process.poll() is None:
                await asyncio.sleep(.015)
        finally:
            # Descendants may hold a pipe open even after the worker exits.
            stop_group(process)
            if pipe is not None:
                output, _ = await pipe
        status = process.returncode
        if status == INVALID_INPUT and pipe is not None:
            http_status, field, message, code = json.loads(output)
            raise JobError(http_status, field, message, code=code)
        if status == INVALID_INPUT and (workdir / "invalid.json").is_file():
            field, message = json.loads((workdir / "invalid.json").read_text("utf-8"))
            raise JobError(422, field, message)
        if status != 0:
            raise JobError(500)
        if response_name is None:
            return output
        data = (workdir / response_name).read_bytes()
        if with_decision:
            applied, reason = json.loads((workdir / "decision-status").read_text("ascii"))
            if compare and applied in ("model", "mixed"):
                rule = (workdir / "measure/book.pdf").read_bytes()
                if not complete_pdf(rule):
                    raise JobError(500)
                return (rule, data), applied, reason
            return data, applied, reason
        return data


if __name__ == "__main__":
    try:
        worker()
    except JobError as exc:
        if "--stdin" in sys.argv:
            print(json.dumps([exc.status, exc.field, exc.message, exc.code]))
        else:
            Path("invalid.json").write_text(json.dumps([exc.field, exc.message]), "utf-8")
        sys.exit(INVALID_INPUT)
