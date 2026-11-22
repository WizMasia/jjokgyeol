# 저장소 전반 보안 강화 설계 (Repository-Wide Security Hardening Design)

- 작성일: 2026-11-22
- 기준 브랜치: `feature/native-desktop` (`ea9cd29`)
- 작업 브랜치: `security/repo-hardening`
- 범위: 웹앱(`app/`), 네이티브 데스크톱(`desktop/`), 빌드·패키징·CI(`desktop/qa/`, `desktop/gui/bundle.py`, `.github/workflows/`), 평가 CLI(`evaluation/`), 운영 가이드(`README.md`)
- 제외 범위: `Dockerfile`의 고정 Debian 스냅샷 갱신(조판 재현성 유지; 컨테이너 실행 경화 플래그는 `README.md`에 문서화)

---

## 1. 목표 및 원칙

`AGENTS.md`의 개발 원칙("설정값은 한 곳에서 관리", "데이터와 동작은 책임에 맞춰 묶음", "같은 기능은 같은 인터페이스로 처리")에 따라 중복 보안 로직을 공통 경계 모듈로 통합하고, 조판 결과(쪽 수, 줄 끝, 글자 좌표, 여백 번호)와 파일 형식 계약을 변경하지 않으면서 점검에서 식별된 High·Medium·Low 보안 취약점을 모두 해소한다.

---

## 2. 영역별 설계

### 2.1 공통 보안 경계 모듈 (`app/security.py`)

웹앱 서버, 워커, 임포터, 참고 서식 분석기, AI 클라이언트가 공유하는 방어 로직을 단일 모듈로 모은다.

1. **HTTP 요청/응답 경계 (`SecurityMiddleware` 또는 헬퍼 + 미들웨어)**
   - **Host 검증 (DNS Rebinding 차단)**:
     - 기본 허용 호스트: `127.0.0.1`, `localhost`, `[::1]` (포트 무관).
     - 추가 허용 호스트: 환경변수 `JJOKGYEOL_ALLOWED_HOSTS`(쉼표 구분, 호스트명 또는 `host:port`)로 운영자가 명시적으로 확장 가능.
     - `Host` 헤더가 누락되었거나 형식이 잘못되었거나 허용 목록 밖이면 `403 Forbidden` JSON 오류 반환.
   - **CSRF 차단 (`POST /api/*`)**:
     - `Sec-Fetch-Site` 헤더가 존재하면 `same-origin` 또는 `none`만 허용(그 외 `403`).
     - `Origin` 헤더가 존재하면 요청의 스킴·호스트·포트(`Host` 헤더 기준)와 정확히 일치해야 함(불일치 시 `403`).
     - 브라우저 단순 폼 요청에 쓰이는 `Content-Type`(`application/x-www-form-urlencoded`, `multipart/form-data`, `text/plain`)을 거부(`415` 또는 `403`; 프런트엔드의 모든 `POST`는 `application/json` 또는 `application/octet-stream`을 전송하며 `app/static/reference.js`의 `/api/reference/analyze` 업로드에도 `Content-Type: application/octet-stream`을 명시).
   - **보안 응답 헤더**:
     - 모든 응답에 `X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`, `Referrer-Policy: no-referrer` 부여.
     - HTML 응답(`text/html`)에 `Content-Security-Policy` 부여:
       `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; frame-src 'self' blob:; object-src 'self' blob:; connect-src 'self'; font-src 'self' data:; base-uri 'none'; form-action 'self'; frame-ancestors 'self'`
2. **자식 프로세스 환경변수 화이트리스트 (`worker_env`)**
   - `os.environ` 전체 전달 금지.
   - 기본 허용 변수: `PATH`, `LANG`, `LC_ALL`, `LC_CTYPE`, `HOME`, `TZ`, `SYSTEMROOT`, `WINDIR`, `COMSPEC`, `TMPDIR`(격리 작업 디렉터리로 덮어씀).
   - 역할별 허용 확장 변수:
     - `render`: `JJOKGYEOL_FONT_DIR`, `JJOKGYEOL_DECISION_HOST`, `JJOKGYEOL_DECISION_PORT`, `JJOKGYEOL_DECISION_MODEL_DIR`, `TEXMFCACHE`(지정 시).
     - `reference`: `JJOKGYEOL_VISION_URL`, `JJOKGYEOL_VISION_MODEL`, `JJOKGYEOL_VISION_TOKEN`.
     - `suggest`: `JJOKGYEOL_STYLE_URL`, `JJOKGYEOL_STYLE_MODEL`.
     - `engine`(`lualatex`), `poppler`, `import`, `pin`: 시크릿·AI 변수 및 외부 TeX 오염 변수(`TEXINPUTS`, `TEXMFCNF`, `TEXMFHOME`, `TEXMFLOCAL`, `TEXMFVAR`, `TEXMFCONFIG`, `LUAINPUTS`) 완전 제외.
3. **아웃바운드 AI URL 정책 (`validate_outbound_url`)**
   - `https://`는 모든 유효 호스트 허용.
   - `http://`는 루프백(`localhost`, `127.0.0.1`, `::1`)에만 허용. 단, Bearer 토큰(`has_token=True`)이 없는 경우에 한해 Docker 로컬 호스트 게이트웨이(`host.docker.internal`)를 허용.
   - 그 외 평문 `http://`, 사용자 정보(`user:pass@`), 쿼리/프래그먼트가 포함된 URL은 거부.
4. **ZIP 스트리밍 안전 읽기 (`SafeZipArchive`)**
   - Central Directory의 `e.file_size`를 신뢰하지 않고 `z.open(path)`에서 고정 청크(64 KiB)로 읽으며:
     - 엔트리별 압축 형식 검사(`ZIP_DEFLATED`, `ZIP_STORED`만 허용), 암호화 플래그 및 중복 경로 거부.
     - 엔트리별 실제 해제 바이트가 `max(1, compress_size) * max_compression_ratio`를 초과하면 즉시 중단.
     - 아카이브 전체 누적 실제 해제 바이트가 `max_uncompressed_bytes`를 초과하면 즉시 중단.
   - `app/importers/read.py`와 `app/reference_office.py`가 동일한 `SafeZipArchive`를 공유.

### 2.2 스타일 DoS 차단 및 웹 워커 격리 (`app/style.py`, `app/server.py`, `app/*worker*.py`)

1. **`app/style.py` DoS 상한**:
   - `_number()`: 문자열 입력 길이 상한(`MAX_NUMBER_CHARS = 64`), `Decimal` 자릿수 상한 및 `context.prec` 고정 상한(`MAX_DECIMAL_PREC = 160`) 적용으로 누적 정밀도 폭증 차단.
   - `_list_levels()`: 최대 단계 수 상한(`MAX_LIST_LEVELS = 32`).
   - `_section_rules()`: 최대 구간 규칙 수 상한(`MAX_SECTION_RULES = 256`).
2. **`app/server.py` 엔드포인트 격리**:
   - `/api/style/open`, `/api/style/export`: 전용 슬롯(`STYLE_SLOTS = BoundedSemaphore(LIMITS.render_slots)`) 논블로킹 획득(`429`), 전체 요청에 `asyncio.timeout(jobs.TIMEOUT)` 적용, JSON 파싱 및 스타일 변환을 `asyncio.to_thread`로 오프로드해 이벤트 루프 블로킹 제거.
   - `/api/article/pin`: 기존 `PIN_WORKERS` `CapacityLimiter` 계약을 유지하되, **슬롯 획득 후** 본문 읽기 수행, `-IB` 플래그 + `TemporaryDirectory` + `limit_worker_files()` + `worker_env("pin")`로 프로세스 격리 강화.
3. **워커 공통 경화 (`app/import_worker.py`, `app/reference.py`, `app/ai/style_worker.py`, `app/article_edit.py`)**:
   - 공통 `harden_worker(memory_bytes=None)`로 `os.geteuid() == 0` 차단, `umask(0o077)`, `RLIMIT_FSIZE`/`RLIMIT_CORE` 및 Linux `RLIMIT_AS`(soft·hard 모두 하향) 통일.
   - `import_worker.py`와 `reference.py`는 내부 `ValueError`가 한국어 사용자 메시지가 아닌 경우 일반화된 오류 메시지로 치환하여 환경변수명·내부 파싱 오류 노출 방지.
4. **임포터 파서 엣지 케이스 (`app/importers/text_html.py`, `app/importers/hwp.py`)**:
   - `text_html.py`: `self.skip`을 `(tag, depth)`로 추적해 중첩 `<svg>`/`<iframe>` 안전 처리; `<a href>` URL 출력에 `literal_text()` 적용.
   - `hwp.py`: `paras[shape]`, `chars[shape]` 인덱스 경계 검사 추가(`IndexError` → `ValueError`).

### 2.3 네이티브 데스크톱 (`desktop/`)

1. **외부 런타임 오버라이드 제한 (`desktop/gui/src/main.rs`)**:
   - `runtime()`에서 `JJOKGYEOL_NATIVE_RUNTIME` 환경변수는 `#[cfg(debug_assertions)]`일 때만 허용. 릴리스 빌드에서는 항상 번들 `resource_dir().join("runtime")`만 사용.
2. **Tauri IPC 창 검증 및 원시 바이트 저장 (`desktop/gui/src/main.rs`, `desktop/gui/frontend/desktop.js`)**:
   - `render_proof`: `require_window(&window, "main")?`.
   - `style_convert`, `save_bytes`: `require_any_window(&window, &["main", "settings"])?`.
   - `save_bytes`: `tauri::ipc::Request<'_>`로 원시 바이트(`InvokeBody::Raw`)와 `x-file-name` 헤더 수신 + `state.slots` 동시성 제한 적용(JSON 숫자 배열 직렬화 메모리 폭증 제거).
   - `desktop.js`: `save()`는 `Uint8Array` + `x-file-name` 헤더 전송; `pdfjs.getDocument`에 `isEvalSupported: false` 명시.
3. **공용 워커 실행/오류/환경변수 경계 (`desktop/src/lib.rs`, `desktop/gui/src/main.rs`)**:
   - `jjokgyeol_proof::sanitize_worker_error(&mut File) -> String` 및 `jjokgyeol_proof::apply_worker_env(&mut Command, Option<&Path>)`를 공용 API로 노출해 Rust CLI 러너와 Tauri GUI `convert()`가 동일한 환경변수 화이트리스트와 16 KiB `invalid_input` JSON 오류 필터링을 사용.
   - `run_worker`에도 `stderr` 크기 상한 감시 적용.
   - **Unix PID 재사용 레이스 제거**: `waitid(P_PID, pid, ..., WEXITED | WNOWAIT | WNOHANG)`로 자식이 종료되었지만 아직 회수(reap)되기 전 상태에서 `kill(-pgid, SIGKILL)`을 수행한 뒤 `child.wait()`로 최종 회수하여 회수 후 PID 재사용 시그널 오발송을 원천 차단. `i32::try_from(child.id())` 사용.
4. **Windows 안전성 (`desktop/worker/worker.py`, `desktop/gui/src/safety.rs`)**:
   - `prepare_external_engine()`에서 `SetDllDirectoryW("")` 호출로 CWD DLL 검색 제거(PyInstaller DLL 디렉터리도 해제되면서 CWD 복원 방지).
   - `limit_worker_files()`에서 `RLIMIT_FSIZE` soft/hard 모두 하향 및 `RLIMIT_CORE = (0, 0)` 설정.
   - `safety::safe_name()`: 끝의 `.` 및 공백 제거, Windows 예약 장치명(`CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`, 확장자 포함) 차단.

### 2.4 빌드·패키징·CI 공급망

1. **`desktop/qa/runtime/setup.py`**:
   - TeX Live 2022 historic `tlnet-final`의 `texlive.tlpdb.xz` 및 5개 패키지(`kpathsea`, `kpathsea.win32`, `luahbtex.win32`, `luatex.win32`, `latex-bin.win32`)의 고정 SHA-512 체크섬(`PACKAGE_SHA512`)을 저장소 코드에 핀닝하고, `tlpdb` 체크섬과 아카이브 체크섬을 이중 검증.
   - tar 추출 헬퍼 `safe_extract_target(root, member_name)`에서 `..`, 절대경로, 드라이브 문자, 정규화 후 루트 탈출 차단.
2. **해시 검증 설치 및 액션 핀닝**:
   - `desktop/qa/runtime/verify.ps1`, `desktop/qa/linux/proof.sh`: `pip install`을 모두 `--require-hashes --only-binary=:all:` + 락파일로 통일(`desktop/qa/requirements.lock` 추가 또는 기존 락파일 조합).
   - `desktop/gui/bundle.py`: `desktop/gui/frontend/node_modules/.bin/tauri` 로컬 설치 우선 및 `npm exec` 시 `--no` 옵션 또는 락파일 핀닝 검증.
   - `.github/workflows/native-linux.yml`, `.github/workflows/native-proof.yml`: 모든 `actions/*`를 40자 커밋 SHA로 핀닝.

### 2.5 평가 CLI 및 운영 가이드

1. **`evaluation/packet.py`**: `blind_pdf()`에 입력 PDF 바이트 상한(`MAX_PDF_BYTES = 64 MiB`), 최대 페이지 수 상한(`MAX_PDF_PAGES = 200`), `PIL.Image.MAX_IMAGE_PIXELS` 명시 검사 추가.
2. **`README.md`**: 컨테이너 런타임 경화 플래그(`--read-only`, `--tmpfs /tmp`, `--cap-drop=ALL`, `--security-opt=no-new-privileges:true`, `--pids-limit`, `--memory`, `--network none`) 및 `JJOKGYEOL_ALLOWED_HOSTS` / HTTPS 정책 안내 추가.

---

## 3. 검증 기준

1. 신규 보안 회귀 테스트(`tests/test_security.py`, `desktop/worker/tests/test_worker.py`, `desktop/qa/runtime/test_setup.py`, `desktop/tests/runner.rs`, `desktop/gui/src/main.rs`, `desktop/gui/src/safety.rs`, `desktop/gui/frontend/test_desktop.py`) 모두 통과.
2. 기존 Python 테스트 스위트 전체(Docker 테스트 이미지 내 `pytest`) 통과.
3. Rust 테스트(`cargo test --locked` on `desktop/Cargo.toml` 및 `desktop/gui/Cargo.toml`) 및 프런트엔드 빌드·테스트 통과.
4. 기준 원고·양식으로 생성한 전후 PDF를 `desktop/qa/compare_pdfs.py`로 대조해 조판 결과 불변 확인.
