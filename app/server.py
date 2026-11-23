import asyncio
import hashlib
import json
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from subprocess import DEVNULL, PIPE
from tempfile import TemporaryDirectory
from threading import BoundedSemaphore
from time import monotonic
from uuid import uuid4

from anyio import CapacityLimiter
from fastapi import FastAPI
from starlette.requests import ClientDisconnect, Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.staticfiles import StaticFiles

from app import jobs, publication
from app import style as styles
from app.ai.decision import PIN as DECISION
from app.ai.style import SETTINGS as SUGGESTION
from app.configuration import CONFIG_DIR, read_config
from app.document import InputError
from app.font_assets import FONT_LIMITS, FRAME_LIMIT, LOCKED_ASSET_PATHS
from app.operations import LIMITS
from app.reference import load_settings
from app.security import SecurityMiddleware, allows_decision_key, is_loopback_host, worker_env

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app/static"
MAX_BODY = LIMITS.request_body_bytes
SLOTS = BoundedSemaphore(LIMITS.render_slots)
STYLE_SLOTS = BoundedSemaphore(LIMITS.render_slots)
PIN_WORKERS = CapacityLimiter(LIMITS.render_slots)
TARGET_SLOTS = BoundedSemaphore(LIMITS.render_slots)
IMPORT_MAX_BYTES = read_config(CONFIG_DIR / "imports.json")["max_bytes"]
REFERENCE = load_settings()
REFERENCE_SLOTS = BoundedSemaphore(REFERENCE["slots"])
SUGGESTION_SLOTS = BoundedSemaphore(SUGGESTION["slots"])
NO_STORE = {"Cache-Control": "no-store"}
FONT_MEDIA_TYPE = "application/x-jjok-font-render"
ERRORS = {
    413: ("too_large", "요청은 {limit:,}바이트 이하여야 합니다."),
    422: ("invalid_input", "원고와 양식의 입력값을 확인하세요."),
    429: ("busy", f"조판 작업 {LIMITS.render_slots}건이 진행 중입니다. 잠시 뒤 다시 시도하세요."),
    500: ("engine_failed", "PDF를 만들 수 없습니다."),
    502: ("model_failed", "양식 모델의 응답을 읽지 못했습니다. 모델 연결을 확인하세요."),
    504: ("timeout", "조판 제한 시간을 초과했습니다."),
}


def failure(status, field=None, message=None, *, byte_limit=None, code=None):
    default_code, fallback = ERRORS[status]
    if status == 413 and not message:
        fallback = fallback.format(limit=MAX_BODY if byte_limit is None else byte_limit)
    return JSONResponse({"code": code or default_code, "field": field, "message": message or fallback},
                        status_code=status, headers=NO_STORE)


def proof_pair_response(rule, model, headers):
    boundary = uuid4().hex

    async def parts():
        for name, pdf in (("rule", rule), ("model", model)):
            yield (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
                   f'filename="{name}.pdf"\r\nContent-Type: application/pdf\r\n\r\n').encode("ascii")
            yield pdf
            yield b"\r\n"
        yield f"--{boundary}--\r\n".encode("ascii")

    return StreamingResponse(parts(), media_type=f"multipart/form-data; boundary={boundary}", headers=headers)


async def read_body(request, limit=None):
    if limit is None:
        limit = MAX_BODY
    length = request.headers.get("content-length")
    if length is not None:
        if not length.isascii() or not length.isdecimal():
            raise jobs.JobError(422)
        # Compare as decimal strings to cover even an unrepresentable length.
        length = length.lstrip("0")
        if len(length) > len(str(limit)) or int(length or "0") > limit:
            raise jobs.JobError(413)
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise jobs.JobError(413)
        body.extend(chunk)
    return body


async def until_disconnect(receive):
    while (await receive())["type"] != "http.disconnect":
        pass


async def while_connected(work, receive):
    job = asyncio.create_task(work)
    disconnect = asyncio.create_task(until_disconnect(receive))
    try:
        done, _ = await asyncio.wait((job, disconnect), return_when=asyncio.FIRST_COMPLETED)
        if disconnect in done:
            raise ClientDisconnect
        return job.result()
    finally:
        for task in (job, disconnect):
            task.cancel()
        await asyncio.gather(job, disconnect, return_exceptions=True)


def reference_command(request, targets, deadline):
    fmt = "targets"
    if not targets:
        fmt = Path(request.query_params.get("name", "")).suffix.lower()[1:]
        if fmt == "jpg":
            fmt = "jpeg"
        if fmt not in ("pdf", "png", "jpeg", "docx", "hwpx"):
            raise jobs.JobError(422, "reference", "DOCX, HWPX, PDF, PNG 또는 JPEG 파일을 선택하세요.")
    page = request.query_params.get("page")
    if page is not None:
        maximum = REFERENCE["max_pages"]
        if (fmt not in ("pdf", "png", "jpeg") or not page.isascii() or not page.isdecimal()
                or len(page) > len(str(maximum)) or not 1 <= int(page) <= maximum):
            raise jobs.JobError(422, "reference", "다시 분석할 참고 쪽 번호를 확인하세요.")
    return (sys.executable, "-IB", str(ROOT / "app/reference.py"), fmt, page or "0", str(deadline))


class DocumentJob:
    """Keep each request, isolated process and response delivery within one slot."""

    def __init__(self, action):
        self.action = action

    def error(self, status, byte_limit, field=None, message=None):
        if self.action == "suggest":
            if status == 500:
                status = 502
            message = message or {
                413: f'양식 제안 요청은 {byte_limit:,}바이트 이하여야 합니다.',
                422: "요청과 현재 양식을 확인하세요.",
                429: "다른 양식 제안을 처리 중입니다. 잠시 뒤 다시 시도하세요.",
                504: "양식 제안 시간이 초과되었습니다.",
            }.get(status)
            return failure(status, field or "request", message)
        if self.action == "check" and status in (500, 504):
            code = "timeout" if status == 504 else "unavailable"
            return failure(status, "connection", "모델 연결을 확인할 수 없습니다.", code=code)
        if self.action not in ("render", "check") and not message:
            field = "reference" if self.action == "analyze" else "source"
            message = {
                413: f"파일은 {byte_limit:,}바이트 이하여야 합니다.",
                429: "다른 문서를 처리 중입니다. 잠시 뒤 다시 시도하세요.",
                500: "문서를 읽지 못했습니다.",
                504: "문서 분석 시간이 초과되었습니다.",
            }.get(status)
        return failure(status, field, message, byte_limit=byte_limit)

    def limits(self):
        if self.action == "render":
            return MAX_BODY, SLOTS, jobs.TIMEOUT
        if self.action == "check":
            return MAX_BODY, SLOTS, min(jobs.TIMEOUT, DECISION["decision_seconds"])
        if self.action == "import":
            return IMPORT_MAX_BYTES, SLOTS, jobs.TIMEOUT
        if self.action == "targets":
            return MAX_BODY, TARGET_SLOTS, jobs.TIMEOUT
        if self.action == "analyze":
            return REFERENCE["max_bytes"], REFERENCE_SLOTS, REFERENCE["timeout_seconds"]
        if self.action == "suggest":
            return SUGGESTION["max_request_bytes"], SUGGESTION_SLOTS, SUGGESTION["timeout_seconds"]
        raise KeyError(self.action)

    async def result(self, request, worker_deadline, byte_limit, font_destination=None):
        if self.action in ("render", "check"):
            body = await read_body(request, byte_limit)
            allow_key = allows_decision_key(request.scope.get("scheme", "http"), request.url.hostname)
            if self.action == "check":
                command = jobs.decision_command("--check", allow_key=allow_key)
                result = await while_connected(
                    jobs.isolated_job(body, command, request_name=None, response_name=None), request.receive)
                return Response(result, media_type="application/json", headers=NO_STORE)
            compare = request.headers.get("accept", "").strip().lower() == "multipart/form-data"
            result, status, reason = await while_connected(
                jobs.render(body, worker_deadline, compare=compare, allow_key=allow_key, font_frame=font_destination),
                request.receive)
            headers = {**NO_STORE, "X-Jjokgyeol-Decision": status}
            if reason is not None:
                headers["X-Jjokgyeol-Decision-Reason"] = reason
            if isinstance(result, tuple):
                return proof_pair_response(*result, headers=headers)
            return Response(result, media_type="application/pdf", headers=headers)
        if self.action == "import":
            name = request.query_params.get("name", "")
            if not name or "\x00" in name:
                raise jobs.JobError(422, "source", "가져올 원고의 파일 이름을 확인하세요.")
            command = (sys.executable, "-IB", str(ROOT / "app/import_worker.py"), name,
                       str(REFERENCE["worker_memory_bytes"]))
            role = "import"
        elif self.action == "suggest":
            command = (sys.executable, "-IB", str(ROOT / "app/ai/style_worker.py"))
            role = "suggest"
        else:
            command = reference_command(request, targets=self.action == "targets", deadline=worker_deadline)
            role = "reference"
        body = await read_body(request, byte_limit)
        result = await while_connected(
            jobs.isolated_job(body, command, "request.bin", "response.json", role), request.receive)
        return Response(result, media_type="application/json", headers=NO_STORE)

    async def __call__(self, scope, receive, send):
        byte_limit, slots, timeout = self.limits()
        request = Request(scope, receive)
        font_destination = None
        if (self.action == "render" and
                request.headers.get("content-type", "").partition(";")[0].strip().lower() == FONT_MEDIA_TYPE):
            byte_limit = FRAME_LIMIT
            font_destination = "local" if is_loopback_host(request.url.hostname) else "remote"
        if not slots.acquire(blocking=False):
            await self.error(429, byte_limit)(scope, receive, send)
            return
        try:
            worker_deadline = monotonic() + timeout
            deadline = asyncio.get_running_loop().time() + timeout
            try:
                async with asyncio.timeout_at(deadline):
                    response = await self.result(request, worker_deadline, byte_limit, font_destination)
            except ClientDisconnect:
                return
            except TimeoutError:
                response = self.error(504, byte_limit)
            except jobs.JobError as error:
                if error.code:
                    response = failure(error.status, error.field, error.message, code=error.code)
                else:
                    response = self.error(error.status, byte_limit, error.field, error.message)
            except OSError:
                response = self.error(500, byte_limit)
            # Once headers go out a deadline must close the transfer.
            if response.status_code != 200:
                deadline = asyncio.get_running_loop().time() + jobs.TIMEOUT
            async with asyncio.timeout_at(deadline):
                await response(scope, receive, send)
        finally:
            slots.release()


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SecurityMiddleware)
app.add_route("/api/import", DocumentJob("import"), methods=["POST"])
app.add_route("/api/style-suggestion", DocumentJob("suggest"), methods=["POST"])
app.add_route("/api/decision/check", DocumentJob("check"), methods=["POST"])
app.add_route("/api/render", DocumentJob("render"), methods=["POST"])
app.add_route("/api/reference/analyze", DocumentJob("analyze"), methods=["POST"])
app.add_route("/api/reference/targets", DocumentJob("targets"), methods=["POST"])


@app.get("/api/reference/settings")
async def reference_settings():
    return JSONResponse({
        "model": REFERENCE["vision_model"], "max_bytes": REFERENCE["max_bytes"],
        "heading_per_10000": publication.HEADINGS,
        "heading_leading_per_10000": publication.HEADING_LEADING,
    }, headers=NO_STORE)


@app.post("/api/article/pin")
async def article_pin(request: Request):
    try:
        async with asyncio.timeout(jobs.TIMEOUT):
            # 슬롯을 먼저 확보한 뒤 본문을 읽고 격리된 임시 디렉터리에서 실행 / Acquire slot before reading body and run in isolated tempdir.
            async with PIN_WORKERS:
                body = await read_body(request)
                with TemporaryDirectory(prefix="jjokgyeol-pin-") as tmp:
                    # Own the process from launch so even a deadline during startup reaps it.
                    worker = await asyncio.create_subprocess_exec(
                        sys.executable, "-IB", str(ROOT / "app/article_edit.py"), cwd=tmp,
                        env=worker_env("pin", tmpdir=tmp),
                        stdin=PIPE, stdout=PIPE, stderr=DEVNULL,
                    )
                    try:
                        output, _ = await worker.communicate(bytes(body))
                    except BaseException:
                        try:
                            worker.kill()
                        except ProcessLookupError:
                            pass
                        await worker.communicate()
                        raise
        if worker.returncode == jobs.INVALID_INPUT:
            return failure(422, "source", output.decode())
        if worker.returncode:
            return failure(500)
        return Response(output, media_type="application/json", headers=NO_STORE)
    except jobs.JobError as error:
        return failure(error.status)
    except TimeoutError:
        return failure(504)


def _convert_style_payload(raw: bytes, *, export: bool) -> dict:
    """양식 JSON 파싱 및 단위 변환을 워커 스레드에서 수행합니다. / Parse and convert style JSON off the event loop."""
    values = json.loads(raw.decode("utf-8-sig"), parse_float=Decimal)
    if export:
        return styles.from_editor_style(values).to_stored()
    return styles.from_stored_style(values).to_editor()


@app.post("/api/style/export")
@app.post("/api/style/open")
async def style_file(request: Request):
    if not STYLE_SLOTS.acquire(blocking=False):
        return failure(429)
    try:
        async with asyncio.timeout(jobs.TIMEOUT):
            body = await read_body(request)
            result = await asyncio.to_thread(
                _convert_style_payload, bytes(body), export=request.url.path.endswith("/export")
            )
        return JSONResponse(result, headers=NO_STORE)
    except InputError as error:
        return failure(422, error.field, error.message)
    except (ValueError, InvalidOperation, RecursionError):
        return failure(422, "style", "올바른 양식 JSON 파일을 선택하세요.")
    except jobs.JobError as error:
        return failure(error.status)
    except TimeoutError:
        return failure(504)
    finally:
        STYLE_SLOTS.release()


def editor_html() -> str:
    template = (STATIC / "index.html").read_text(encoding="utf-8")
    marker = "<!--EDITOR_SETTINGS-->"
    if template.count(marker) != 1:
        raise ValueError(f"index.html must contain exactly one {marker}")
    # HTML parses script contents as raw text even for application/json.
    settings = json.dumps(styles.editor_bootstrap(), allow_nan=False).replace("<", r"\u003c")
    script = f'<script type="application/json" id="editor-settings">{settings}</script>'
    return template.replace(marker, script)


@app.get("/", response_class=HTMLResponse)
@app.get("/static/index.html", response_class=HTMLResponse)
def editor():
    return editor_html()


@app.get("/sample.md")
def sample():
    return FileResponse(ROOT / "proofs/engine-comparison/manuscript.md", media_type="text/markdown; charset=utf-8")


@app.get("/api/document-fonts/{digest}")
def builtin_font_asset(digest: str):
    path = LOCKED_ASSET_PATHS.get(digest)
    if path is None:
        return Response(status_code=404)
    try:
        with path.open("rb") as stream:
            data = stream.read(FONT_LIMITS["file_bytes"] + 1)
    except OSError:
        return Response(status_code=503)
    if len(data) > FONT_LIMITS["file_bytes"] or hashlib.sha256(data).hexdigest() != digest:
        return Response(status_code=503)
    return Response(data, media_type="application/octet-stream", headers={"Cache-Control": "private, max-age=86400"})


app.mount("/static", StaticFiles(directory=STATIC, check_dir=False))
