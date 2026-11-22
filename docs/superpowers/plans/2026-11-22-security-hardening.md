# 저장소 전반 보안 강화 구현 계획 (Security Hardening Implementation Plan)

> **For agentic workers:** Execute task-by-task using TDD (failing test -> minimal implementation -> passing test -> commit).

**Goal:** 웹앱, 네이티브 데스크톱(Tauri/Rust/Python 워커), 빌드·CI, 평가 CLI 전반의 보안 취약점을 공통 경계 모듈과 최소 변경으로 해소하고 전수 회귀 검증한다.

**Architecture:** `app/security.py`에 HTTP Host/CSRF/헤더, 아웃바운드 URL 검증, 역할별 환경변수 화이트리스트, 스트리밍 ZIP 리더, 워커 경화를 통합한다. Rust 영역은 `desktop/src/lib.rs`에 워커 환경 화이트리스트·오류 정제·PID 재사용 안전한 프로세스 그룹 종료를 공용화하고 `desktop/gui/src/main.rs`가 이를 재사용한다.

**Tech Stack:** Python 3.12 (FastAPI/Starlette, pytest), Rust (Tauri 2, libc), Node/JS (pdfjs-dist), PowerShell/Bash/GitHub Actions.

---

### Task 1: `app/security.py` + `app/server.py` (HTTP 보안 미들웨어, `style_file` 오프로드, `article_pin` 격리)
- **Create:** `app/security.py`, `tests/test_security.py`
- **Modify:** `app/server.py`, `app/article_edit.py`, `app/static/reference.js`, 기존 `http://local` / `TestClient(app)` 테스트 호출부
- **Verify:** Docker `pytest tests/test_security.py tests/test_jobs.py tests/test_style_file.py tests/test_article_edit.py tests/test_operations.py`

### Task 2: `app/style.py` DoS 상한 + 워커 환경변수/RLIMIT/아웃바운드 HTTPS 정책 및 오류 정제
- **Modify:** `app/style.py`, `app/jobs.py`, `app/typeset.py`, `app/reference.py`, `app/reference_media.py`, `app/ai/style.py`, `app/ai/style_worker.py`, `app/import_worker.py`
- **Test:** `tests/test_security.py` 확장 + 기존 `tests/test_style_file.py`, `tests/test_reference*.py`, `tests/test_style_suggestion*.py`, `tests/test_import_api.py`

### Task 3: ZIP Bomb 스트리밍 방어 및 HTML/HWP 파서 경계 수정
- **Modify:** `app/security.py` (`SafeZipArchive`), `app/importers/read.py`, `app/importers/docx.py`, `app/importers/hwpx.py`, `app/reference_office.py`, `app/importers/text_html.py`, `app/importers/hwp.py`
- **Test:** `tests/test_security.py`에 위조 `file_size` ZIP Bomb, 비허용 압축 방식, 중첩 `<svg>`, `<a href>` 이스케이프, HWP 범위 밖 shape 인덱스 테스트 추가

### Task 4: 네이티브 데스크톱 (Rust 러너 · Tauri GUI · Python 워커 · 프런트엔드)
- **Modify:** `desktop/src/lib.rs`, `desktop/gui/src/main.rs`, `desktop/gui/src/safety.rs`, `desktop/worker/worker.py`, `desktop/gui/frontend/desktop.js`, `desktop/gui/frontend/test_desktop.py`, `desktop/tests/runner.rs`, `desktop/worker/tests/test_worker.py`
- **Verify:** `cargo test --locked --manifest-path desktop/Cargo.toml`, `cargo test --locked --manifest-path desktop/gui/Cargo.toml`, `pytest desktop/worker/tests/test_worker.py`, `desktop/gui/frontend/build.py` + `test_build.py`

### Task 5: 빌드·패키징·CI 공급망 강화
- **Modify:** `desktop/qa/runtime/setup.py`, `desktop/qa/runtime/test_setup.py`, `desktop/qa/runtime/verify.ps1`, `desktop/qa/linux/proof.sh`, `desktop/gui/bundle.py`, `desktop/gui/test_bundle.py`, `.github/workflows/native-linux.yml`, `.github/workflows/native-proof.yml`
- **Create:** `desktop/qa/requirements-compare.lock` (해시 고정 비교 도구 락파일)
- **Verify:** `unittest` for `desktop/qa/runtime/test_setup.py`, `desktop/gui/test_bundle.py`, `desktop/qa/test_compare_pdfs.py`

### Task 6: 평가 CLI 입력 경계 + `README.md` 컨테이너 경화 가이드 + 최종 전수 검증
- **Modify:** `evaluation/packet.py`, `README.md`
- **Verify:** Docker 전체 `pytest` 스위트, Rust 전체 테스트, 수정 전후 조판 PDF `desktop/qa/compare_pdfs.py` 대조
