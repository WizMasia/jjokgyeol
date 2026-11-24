"""One private process group and working directory per render (POSIX)."""
import asyncio
import ctypes
from decimal import Decimal, InvalidOperation
from functools import partial
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
from app.publication import MAX_SAFE_INTEGER
from app.security import complete_pdf, harden_worker, worker_env

TIMEOUT = LIMITS.timeout_seconds
MAX_FILE = LIMITS.worker_file_bytes
WORKER_CMD = (sys.executable, "-IB", str(Path(__file__).resolve()))
INVALID_INPUT = 64
BASELINE_READY = "baseline-ready"


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


def publish_baseline(end):
    # Publish the worker's monotonic deadline only after the measured PDF is complete.
    pending = Path(BASELINE_READY + ".tmp")
    pending.write_text(str(end), "ascii")
    pending.replace(BASELINE_READY)


def read_rule_pdf(workdir):
    rule = (workdir / "measure/book.pdf").read_bytes()
    if not complete_pdf(rule):
        raise JobError(500)
    return rule


def read_spacing(folder):
    """Expose only measured coordinates and a known decision source from this PDF."""
    try:
        with (folder / "spacing.json").open("rb") as stream:
            raw = stream.read(LIMITS.request_body_bytes + 1)
        if len(raw) > LIMITS.request_body_bytes:
            return None
        rows = json.loads(raw, object_pairs_hook=unique_fields)
    except (OSError, ValueError, RecursionError):
        return None
    if not isinstance(rows, list):
        return None
    fields = ("before_line", "after_line", "gap_sp")
    result = []
    for row in rows:
        if (not isinstance(row, dict) or row.get("valid") is not True
                or row.get("source") not in ("manual", "style", "ai", "rule", "default")
                or not all(type(row.get(key)) is int and 0 <= row[key] <= MAX_SAFE_INTEGER for key in fields)):
            continue
        public = {key: row[key] for key in (*fields, "source")}
        if (row["gap_sp"] == 0 and type(row.get("left_page")) is int
                and type(row.get("page")) is int
                and 0 < row["left_page"] < row["page"] <= MAX_SAFE_INTEGER):
            public["page_break"] = True
        result.append(public)
    return result


def read_leading(folder):
    try:
        with (folder / "leading.json").open("rb") as stream:
            raw = stream.read(LIMITS.request_body_bytes + 1)
        if len(raw) > LIMITS.request_body_bytes:
            return None
        rows = json.loads(raw, object_pairs_hook=unique_fields)
    except (OSError, ValueError, RecursionError):
        return None
    if not isinstance(rows, list):
        return None
    result = []
    sources = ("manual", "style", "ai", "rule", "default")
    for row in rows:
        if (not isinstance(row, dict) or row.get("source") not in sources
                or type(row.get("line")) is not int or not 0 <= row["line"] <= MAX_SAFE_INTEGER
                or type(row.get("lines")) is not int or not 2 <= row["lines"] <= MAX_SAFE_INTEGER):
            continue
        leading, variable = row.get("leading_sp"), row.get("variable")
        if (variable is False and row.get("valid") is True and type(leading) is int
                and 0 < leading <= MAX_SAFE_INTEGER):
            pass
        elif variable is True and row.get("valid") is False and leading is None:
            pass
        else:
            continue
        result.append({"line": row["line"], "leading_sp": leading,
                       "source": row["source"], "variable": variable})
    return result or None


def read_payload(workdir, checking):
    font_frame = "--local-font-frame" in sys.argv or "--remote-font-frame" in sys.argv
    if "--stdin" in sys.argv:
        limit = LIMITS.request_body_bytes
        if font_frame:
            from app.font_assets import FRAME_LIMIT
            limit = FRAME_LIMIT
        raw = sys.stdin.buffer.read(limit + 1)
        if len(raw) > limit:
            raise JobError(413)
    else:
        request = workdir / "request.json"
        raw = request.read_bytes()
        request.unlink()
    try:
        if font_frame:
            from app.font_frame import unpack_frame
            return unpack_frame(raw, remote="--remote-font-frame" in sys.argv)
        return json.loads(raw.decode("utf-8"), parse_float=Decimal, parse_int=Decimal,
                          object_pairs_hook=unique_fields), {}
    except (ValueError, InvalidOperation, RecursionError):
        if checking:
            raise connection_error("invalid_connection") from None
        if font_frame:
            raise JobError(422, "fonts", "글꼴 파일과 내장 권한을 확인하세요.") from None
        raise JobError(422) from None


def worker():
    harden_worker(max_file_bytes=MAX_FILE)
    workdir = Path.cwd()
    checking = "--check" in sys.argv or "--models" in sys.argv
    payload, assets = read_payload(workdir, checking)
    if assets:
        from app.document_fonts import use_document_fonts
        with use_document_fonts(assets):
            return process_payload(workdir, payload, checking)
    return process_payload(workdir, payload, checking)


def process_payload(workdir, payload, checking):
    from app.document import InputError, parse_md
    from app.style import NEW_DOCUMENT_STYLE, from_editor_style
    from app.typeset import typeset

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
            if "--models" in sys.argv:
                from app.ai.providers import list_models
                print(json.dumps({"models": list_models(payload)}))
                return
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
        render_pdf = (partial(typeset, collect_spacing=True, collect_leading=True)
                      if "--spacing-receipt" in sys.argv else typeset)
        if reason:
            from app.ai.decision import measure_rule
            pdf = measure_rule(doc, style, workdir / "measure", typesetter=render_pdf)
        elif mode == "model" and (connection is not None or os.getenv("JJOKGYEOL_DECISION_HOST")):
            from app.ai.decision import render_provider_choice
            deadline = float(sys.argv[1]) if len(sys.argv) > 1 else monotonic() + TIMEOUT
            pdf, status, reason = render_provider_choice(doc, style, workdir, deadline, connection,
                                                        typesetter=render_pdf, baseline_ready=publish_baseline)
        elif "decision" in payload:
            pdf = render_pdf(doc, style, workdir, collect_candidates=True)
        else:
            pdf = render_pdf(doc, style, workdir)
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


async def render(body, deadline=None, *, compare=False, allow_key=True, font_frame=None, with_spacing=False):
    with_decision = compare or deadline is not None or with_spacing
    if deadline is None:
        deadline = monotonic() + TIMEOUT
    command = decision_command(str(deadline), allow_key=allow_key)
    if font_frame is not None:
        command += ("--remote-font-frame" if font_frame == "remote" else "--local-font-frame",)
    if compare:
        command += ("--compare",)
    if with_spacing:
        command += ("--spacing-receipt",)
    result = await isolated_job(body, command, None, "response.pdf", with_decision=True, compare=compare,
                                with_spacing=with_spacing)
    return result if with_decision else result[0]


async def isolated_job(
    body,
    command,
    request_name,
    response_name,
    env_role="render",
    *,
    with_decision=False,
    compare=False,
    with_spacing=False,
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
        marker = workdir / BASELINE_READY
        model_deadline = None
        model_timed_out = False
        try:
            if request_name is None:
                pipe = asyncio.create_task(asyncio.to_thread(process.communicate, body))
            while process.poll() is None:
                if with_decision and model_deadline is None and marker.is_file():
                    model_deadline = float(marker.read_text("ascii"))
                if model_deadline is not None and monotonic() >= model_deadline:
                    model_timed_out = True
                    break
                await asyncio.sleep(.015)
        finally:
            # Descendants may hold a pipe open even after the worker exits.
            stop_group(process)
            if pipe is not None:
                output, _ = await pipe
        status = process.returncode
        if status == INVALID_INPUT and pipe is not None:
            try:
                http_status, field, message, code = json.loads(output)
            except (ValueError, TypeError):
                pass  # A crashed model can exit with this status without a validation receipt.
            else:
                raise JobError(http_status, field, message, code=code)
        if status == INVALID_INPUT and (workdir / "invalid.json").is_file():
            field, message = json.loads((workdir / "invalid.json").read_text("utf-8"))
            raise JobError(422, field, message)
        selected_folder = workdir
        if status != 0 or model_timed_out:
            # A worker can publish its baseline and exit between consecutive polls.
            if not with_decision or not marker.is_file():
                raise JobError(500)
            reason = "timeout" if model_timed_out else "invalid_response"
            applied = "rule"
            data = read_rule_pdf(workdir)
            if (workdir / "line-replay-ready").is_file():
                selected_folder = workdir / "replay"
                try:
                    replay = (selected_folder / "book.pdf").read_bytes()
                except OSError:
                    replay = b""
                if complete_pdf(replay):
                    data, applied = replay, "mixed"
        else:
            if response_name is None:
                return output
            data = (workdir / response_name).read_bytes()
            if not with_decision:
                return data
            applied, reason = json.loads((workdir / "decision-status").read_text("ascii"))
        paired = compare and applied in ("model", "mixed")
        result = ((read_rule_pdf(workdir), data) if paired else data, applied, reason)
        if not with_spacing:
            return result
        measure = workdir / "measure"
        folders = {"rule": measure if measure.is_dir() else workdir}
        if applied in ("model", "mixed"):
            folders = {**(folders if paired else {}), "model": selected_folder}
        receipts = {key: rows for key, folder in folders.items()
                    if (rows := read_spacing(folder)) is not None}
        leading = {key: rows for key, folder in folders.items()
                   if (rows := read_leading(folder)) is not None}
        if leading:
            receipts["leading"] = leading
        return (*result, receipts)


if __name__ == "__main__":
    try:
        worker()
    except JobError as exc:
        if "--stdin" in sys.argv:
            print(json.dumps([exc.status, exc.field, exc.message, exc.code]))
        else:
            Path("invalid.json").write_text(json.dumps([exc.field, exc.message]), "utf-8")
        sys.exit(INVALID_INPUT)
