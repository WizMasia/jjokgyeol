"""공통 보안 경계 모듈 / Shared defensive security boundaries for server, workers, and parsers."""
from __future__ import annotations

from collections.abc import Mapping
from io import BytesIO
import os
import re
import sys
from typing import Any
from urllib.parse import SplitResult, urlsplit
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

from starlette.responses import JSONResponse

# 기본 허용 루프백 호스트 / Default allowed loopback hostnames.
LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "localhost", "::1"})
# 토큰 없는 로컬 Docker AI 모델 연결에만 허용하는 호스트 / Host allowed for tokenless local Docker AI endpoints.
DOCKER_LOCAL_HOSTS: frozenset[str] = frozenset({"host.docker.internal"})
# 브라우저 단순 폼 CSRF 요청에서 사용되는 Content-Type / Simple CSRF-eligible form Content-Types.
SIMPLE_CSRF_CONTENT_TYPES: frozenset[str] = frozenset({
    "application/x-www-form-urlencoded",
    "multipart/form-data",
    "text/plain",
})
ALLOWED_FETCH_SITES: frozenset[str] = frozenset({"same-origin", "none"})

# 모든 HTTP 응답에 부여하는 보안 헤더 / Baseline HTTP security headers for all responses.
SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"SAMEORIGIN"),
    (b"referrer-policy", b"no-referrer"),
)
# 웹 편집기 HTML용 CSP / Content-Security-Policy for the web editor HTML.
HTML_CSP: bytes = (
    b"default-src 'self'; "
    b"script-src 'self'; "
    b"style-src 'self' 'unsafe-inline'; "
    b"img-src 'self' data: blob:; "
    b"frame-src 'self' blob:; "
    b"object-src 'self' blob:; "
    b"connect-src 'self'; "
    b"font-src 'self' data:; "
    b"base-uri 'none'; "
    b"form-action 'self'; "
    b"frame-ancestors 'self'"
)

# 자식 프로세스에 전달할 최소 OS 환경변수 / Minimal OS environment variables passed to child processes.
_BASE_ENV_KEYS: tuple[str, ...] = (
    "PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "HOME",
    "TZ",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
)
# 워커 역할별 허용 환경변수 / Role-scoped environment variable allowlists.
_ROLE_ENV_KEYS: dict[str, tuple[str, ...]] = {
    "render": (
        "JJOKGYEOL_FONT_DIR",
        "JJOKGYEOL_DECISION_HOST",
        "JJOKGYEOL_DECISION_PORT",
        "JJOKGYEOL_DECISION_MODEL_DIR",
        "TEXMFCACHE",
    ),
    "reference": (
        "JJOKGYEOL_VISION_URL",
        "JJOKGYEOL_VISION_MODEL",
        "JJOKGYEOL_VISION_TOKEN",
    ),
    "suggest": (
        "JJOKGYEOL_STYLE_URL",
        "JJOKGYEOL_STYLE_MODEL",
    ),
    "import": (),
    "pin": (),
    "engine": (),
    "poppler": (),
}

_HOST_CHAR_RE = re.compile(r"^[A-Za-z0-9.\-:\[\]]+$")
_HANGUL_RE = re.compile(r"[가-힣]")
_ZIP_CHUNK_BYTES = 65536


def _parse_host_port(value: str) -> tuple[str, int | None] | None:
    """Host 헤더를 (정규화된 호스트명, 포트)로 파싱합니다. / Parse a Host header into (hostname, port)."""
    raw = value.strip()
    if not raw or not raw.isascii() or not _HOST_CHAR_RE.fullmatch(raw):
        return None
    try:
        parsed = urlsplit("http://" + raw)
    except ValueError:
        return None
    if parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
        return None
    hostname = parsed.hostname
    if not hostname:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    return hostname.lower(), port


def _allowed_hosts() -> tuple[set[str], set[tuple[str, int]]]:
    """허용 호스트 목록을 계산합니다. / Compute allowed hostnames and host:port pairs."""
    hosts: set[str] = set(LOOPBACK_HOSTS)
    host_ports: set[tuple[str, int]] = set()
    configured = os.environ.get("JJOKGYEOL_ALLOWED_HOSTS", "")
    for item in configured.split(","):
        entry = item.strip()
        if not entry:
            continue
        parsed = _parse_host_port(entry)
        if parsed is None:
            continue
        hostname, port = parsed
        if port is None:
            hosts.add(hostname)
        else:
            host_ports.add((hostname, port))
    return hosts, host_ports


def validate_host_header(host: str | None) -> tuple[str, int | None] | None:
    """DNS Rebinding 방어를 위해 Host 헤더를 검증합니다. / Validate the Host header against DNS rebinding."""
    if host is None:
        return None
    parsed = _parse_host_port(host)
    if parsed is None:
        return None
    hostname, port = parsed
    hosts, host_ports = _allowed_hosts()
    if hostname in hosts or (port is not None and (hostname, port) in host_ports):
        return parsed
    return None


def _default_port(scheme: str, port: int | None) -> int | None:
    if port is not None:
        return port
    if scheme == "http":
        return 80
    if scheme == "https":
        return 443
    return None


def validate_post_csrf(
    headers: Mapping[str, str],
    host_parts: tuple[str, int | None],
    scheme: str,
) -> tuple[int, str] | None:
    """POST API 요청의 CSRF 헤더와 Content-Type을 검증합니다. / Validate CSRF headers and Content-Type on POST APIs."""
    fetch_site = headers.get("sec-fetch-site")
    if fetch_site is not None and fetch_site.strip().lower() not in ALLOWED_FETCH_SITES:
        return 403, "허용되지 않은 교차 출처 요청입니다."

    origin = headers.get("origin")
    if origin is not None:
        raw_origin = origin.strip()
        if not raw_origin or raw_origin.lower() == "null":
            return 403, "허용되지 않은 출처(Origin) 요청입니다."
        try:
            parsed = urlsplit(raw_origin)
            origin_port = parsed.port
        except ValueError:
            return 403, "허용되지 않은 출처(Origin) 요청입니다."
        origin_scheme = parsed.scheme.lower()
        origin_host = (parsed.hostname or "").lower()
        req_scheme = scheme.lower()
        if (
            origin_scheme != req_scheme
            or origin_host != host_parts[0]
            or _default_port(origin_scheme, origin_port) != _default_port(req_scheme, host_parts[1])
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            return 403, "허용되지 않은 출처(Origin) 요청입니다."

    content_type = headers.get("content-type")
    if content_type is not None:
        media_type = content_type.split(";", 1)[0].strip().lower()
        if media_type in SIMPLE_CSRF_CONTENT_TYPES:
            return 415, "지원하지 않는 요청 형식입니다."
    return None


class SecurityMiddleware:
    """Host·CSRF 검증과 보안 응답 헤더를 강제하는 순수 ASGI 미들웨어.

    Pure ASGI middleware enforcing Host/CSRF checks and security response headers.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers: dict[str, str] = {}
        host_values: list[str] = []
        for raw_key, raw_val in scope.get("headers", ()):
            key = raw_key.decode("latin-1").lower()
            val = raw_val.decode("latin-1")
            if key == "host":
                host_values.append(val)
            headers[key] = val

        # 중복 Host 헤더 거부 / Reject duplicate Host headers.
        if len(host_values) != 1:
            response = JSONResponse(
                {"code": "forbidden", "field": None, "message": "허용되지 않은 호스트 요청입니다."},
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
            await self._send_with_headers(response, scope, receive, send)
            return

        host_parts = validate_host_header(host_values[0])
        if host_parts is None:
            response = JSONResponse(
                {"code": "forbidden", "field": None, "message": "허용되지 않은 호스트 요청입니다."},
                status_code=403,
                headers={"Cache-Control": "no-store"},
            )
            await self._send_with_headers(response, scope, receive, send)
            return

        method = scope.get("method", "GET").upper()
        path = scope.get("path", "")
        if method == "POST" and path.startswith("/api/"):
            csrf_error = validate_post_csrf(headers, host_parts, scope.get("scheme", "http"))
            if csrf_error is not None:
                status_code, message = csrf_error
                code = "unsupported_media_type" if status_code == 415 else "forbidden"
                response = JSONResponse(
                    {"code": code, "field": None, "message": message},
                    status_code=status_code,
                    headers={"Cache-Control": "no-store"},
                )
                await self._send_with_headers(response, scope, receive, send)
                return

        async def send_wrapped(event: dict[str, Any]) -> None:
            if event["type"] == "http.response.start":
                event = self._augment_headers(event)
            await send(event)

        await self.app(scope, receive, send_wrapped)

    async def _send_with_headers(self, response: JSONResponse, scope: dict[str, Any], receive: Any, send: Any) -> None:
        async def send_wrapped(event: dict[str, Any]) -> None:
            if event["type"] == "http.response.start":
                event = self._augment_headers(event)
            await send(event)

        await response(scope, receive, send_wrapped)

    @staticmethod
    def _augment_headers(event: dict[str, Any]) -> dict[str, Any]:
        raw_headers: list[tuple[bytes, bytes]] = list(event.get("headers", ()))
        existing = {k.lower() for k, _ in raw_headers}
        content_type = b""
        for k, v in raw_headers:
            if k.lower() == b"content-type":
                content_type = v.lower()
                break
        for k, v in SECURITY_HEADERS:
            if k not in existing:
                raw_headers.append((k, v))
        if content_type.startswith(b"text/html") and b"content-security-policy" not in existing:
            raw_headers.append((b"content-security-policy", HTML_CSP))
        return {**event, "headers": raw_headers}


def validate_outbound_url(raw: str, *, has_token: bool = False) -> SplitResult:
    """외부 AI 엔드포인트 URL의 스킴·호스트·토큰 전송 정책을 검증합니다.

    Validate outbound AI endpoint URL scheme, host, and plaintext bearer token policy.
    """
    try:
        parsed = urlsplit(raw)
        _ = parsed.port
    except ValueError as error:
        raise ValueError("연결 URL 형식이 올바르지 않습니다.") from error
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    if (
        scheme not in ("https", "http")
        or not hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.query
    ):
        raise ValueError("연결 URL 형식이 올바르지 않습니다.")
    if scheme == "http":
        if hostname in LOOPBACK_HOSTS:
            return parsed
        if not has_token and hostname in DOCKER_LOCAL_HOSTS:
            return parsed
        if has_token:
            raise ValueError("인증 토큰이 설정된 원격 엔드포인트는 HTTPS만 허용됩니다.")
        raise ValueError("로컬 외 원격 엔드포인트는 HTTPS만 허용됩니다.")
    return parsed


def worker_env(
    role: str,
    *,
    tmpdir: str | None = None,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """역할에 필요한 최소 환경변수만 화이트리스트로 구성합니다.

    Build a minimal allowlisted environment dictionary for a specific child process role.
    """
    if role not in _ROLE_ENV_KEYS:
        raise KeyError(f"unknown worker role: {role}")
    env: dict[str, str] = {}
    for key in (*_BASE_ENV_KEYS, *_ROLE_ENV_KEYS[role]):
        value = os.environ.get(key)
        if value is not None:
            env[key] = value
    if tmpdir is not None:
        env["TMPDIR"] = tmpdir
    if extra:
        env.update(extra)
    return env


def harden_worker(*, max_file_bytes: int, memory_bytes: int | None = None) -> None:
    """격리 워커의 권한·umask·POSIX 리소스 제한을 적용합니다.

    Apply non-root check, restrictive umask, and hard+soft POSIX resource limits for a worker.
    """
    if os.name == "posix":
        import resource

        if os.geteuid() == 0:
            raise RuntimeError("unprivileged worker required")
        os.umask(0o077)
        _, fsize_hard = resource.getrlimit(resource.RLIMIT_FSIZE)
        fsize_limit = max_file_bytes if fsize_hard == resource.RLIM_INFINITY else min(max_file_bytes, fsize_hard)
        resource.setrlimit(resource.RLIMIT_FSIZE, (fsize_limit, fsize_limit))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        if memory_bytes is not None and sys.platform == "linux":
            _, as_hard = resource.getrlimit(resource.RLIMIT_AS)
            as_limit = memory_bytes if as_hard == resource.RLIM_INFINITY else min(memory_bytes, as_hard)
            resource.setrlimit(resource.RLIMIT_AS, (as_limit, as_limit))


def sanitize_user_error(error: BaseException, fallback: str) -> str:
    """내부 영문 오류·환경변수명 노출을 막고 한국어 사용자 메시지만 통과시킵니다.

    Prevent leaking internal English error details or env var names; allow only Korean user messages.
    """
    text = str(error).strip()
    if text and _HANGUL_RE.search(text) and "JJOKGYEOL_" not in text:
        return text
    return fallback


class SafeZipArchive:
    """위조된 ZIP 헤더를 신뢰하지 않고 스트리밍 바이트·압축률을 강제하는 래퍼.

    ZipFile wrapper enforcing actual streamed uncompressed bytes and compression ratio limits.
    """

    def __init__(
        self,
        data: bytes,
        *,
        max_entries: int,
        max_uncompressed_bytes: int,
        max_compression_ratio: int,
        limit_message: str,
        duplicate_or_encrypted_message: str,
        compression_message: str,
    ) -> None:
        self._data = data
        self._max_entries = max_entries
        self._max_uncompressed_bytes = max_uncompressed_bytes
        self._max_compression_ratio = max_compression_ratio
        self._limit_message = limit_message
        self._duplicate_or_encrypted_message = duplicate_or_encrypted_message
        self._compression_message = compression_message
        self._zip: ZipFile | None = None
        self._total_read = 0
        self._cache: dict[str, bytes] = {}

    def __enter__(self) -> SafeZipArchive:
        z = ZipFile(BytesIO(self._data))
        try:
            entries = z.infolist()
            if (
                len(entries) > self._max_entries
                or sum(e.file_size for e in entries) > self._max_uncompressed_bytes
                or any(
                    e.file_size > max(1, e.compress_size) * self._max_compression_ratio
                    for e in entries
                )
            ):
                raise ValueError(self._limit_message)
            if len(entries) != len(z.NameToInfo) or any(e.flag_bits & 1 for e in entries):
                raise ValueError(self._duplicate_or_encrypted_message)
            if any(e.compress_type not in (ZIP_DEFLATED, ZIP_STORED) for e in entries):
                raise ValueError(self._compression_message)
        except Exception:
            z.close()
            raise
        self._zip = z
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._zip is not None:
            self._zip.close()
            self._zip = None

    @property
    def NameToInfo(self) -> dict[str, ZipInfo]:
        assert self._zip is not None
        return self._zip.NameToInfo

    def infolist(self) -> list[ZipInfo]:
        assert self._zip is not None
        return self._zip.infolist()

    def read(self, name: str | ZipInfo) -> bytes:
        """엔트리를 스트리밍으로 읽으며 실제 압축 해제 크기와 압축률 상한을 강제합니다.

        Stream an archive member while enforcing actual decompressed byte and ratio bounds.
        """
        assert self._zip is not None
        info = name if isinstance(name, ZipInfo) else self._zip.getinfo(name)
        key = info.filename
        if key in self._cache:
            return self._cache[key]
        entry_limit = min(
            self._max_uncompressed_bytes - self._total_read,
            max(1, info.compress_size) * self._max_compression_ratio,
        )
        if entry_limit < 0:
            raise ValueError(self._limit_message)
        buf = bytearray()
        with self._zip.open(info, "r") as stream:
            while True:
                chunk = stream.read(_ZIP_CHUNK_BYTES)
                if not chunk:
                    break
                if len(buf) + len(chunk) > entry_limit:
                    raise ValueError(self._limit_message)
                buf.extend(chunk)
        self._total_read += len(buf)
        result = bytes(buf)
        self._cache[key] = result
        return result
