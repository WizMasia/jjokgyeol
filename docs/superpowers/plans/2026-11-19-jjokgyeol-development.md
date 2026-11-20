# 쪽결 개발 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**진행 상태** — 원고 편집, 출판 양식, 주석, 다섯 형식 가져오기, 문단 조판과 두 로컬 모델을 통합했다. 기본 출력에는 규칙 조판을 사용한다. T14는 어절을 보존한 양끝맞춤 출력으로 편집자 기준 지면·익명 판독·갱신 시간 수용을 평가한다. [출판 지면 인수](2026-11-20-justified-publication.md) [첫 편집기 인수](../t1-t6-status.md) · [주석 인수](2026-11-20-annotations.md) · [실모델 검증](../decision-acceptance.md) · [사람 판독 절차](../../../evaluation/README.md)

**Goal:** 저자가 한영 혼합 마크다운과 출판 양식을 편집하고, 실제로 조판한 한 PDF를 옆 창에서 읽고 내려받을 수 있게 한다.

**Architecture:** 한 백엔드가 정적 작업 화면을 제공하고 요청마다 독립된 디렉터리에서 stock LuaLaTeX + luatexko를 실행한다. 첫 단면은 엔진 규칙으로 출력한다. 같은 엔진의 공개 문단 처리 지점에서 배치를 지정할 수 있는지는 첫 단면에서 실측하고, 학습 모델 연결은 그 결과를 본 뒤 진행한다.

**Tech Stack:** Python·FastAPI·Uvicorn 웹 프로세스 1개, markdown-it-py, 정적 HTML/CSS/JavaScript, 브라우저 내장 PDF 뷰어, Linux OCI 이미지와 Docker를 쓴다. 같은 언어에서 파일 변환과 모델을 붙이고, 첫 실행에 프론트 빌드 서버·DB·작업 큐가 필요 없도록 한다. 테스트는 pytest, pdfminer.six의 문자 좌표, Poppler의 PDF/글꼴 정보, Playwright를 사용한다.

**Spec:** [SPEC.md](../../../SPEC.md) · [비교 원고](../../../proofs/engine-comparison/manuscript.md) · [LuaLaTeX](../../../proofs/engine-comparison/LuaLaTeX.pdf) · [Typst](../../../proofs/engine-comparison/Typst.pdf) · [좌우 비교](../../../proofs/engine-comparison/side-by-side.pdf)

## Global Constraints

- 사용자 승인에 따라 T7·T9·T10·T11·T12·T13·T14의 구현과 인수를 마칠 때까지 진행한다. 추가 스펙 결정과 구현 방식은 작업자가 추천하는 1안을 채택하고 그 이유와 검증 결과를 해당 설계·인수 기록에 남긴다. 실제 편집자의 선호·수정 의견과 기준 지면은 사람이 제공한 자료로 검증한다.

- 편집의 기준 원고는 마크다운(`.md`)이다. 원본과 출판 양식을 별도로 유지하고 조판으로 생긴 개행을 원고에 쓰지 않는다.
- 웹판과 로컬판의 엔진·패키지·글꼴을 함께 고정한다. InDesign 설치·구독·API, 호스트 TeX 및 OS 글꼴 없이 같은 이미지로 실행한다.
- 기본 엔진을 포크하지 않는다. 제품이 소유하는 조판 코드는 형식이 정해진 데이터 변환, 양식, 공개 확장 지점에 한정한다. [luatexko 패키지](https://ctan.org/pkg/luatexko)
- 본문 크기는 포인트 단위이며 14pt 이상도 지원한다. 비교 후보는 한글 자간 0%, −3%, −7%, 공백은 글꼴 기본 폭의 70~130%다. 실험값과 제품 기본값 결정은 구분한다.
- 기존 두 엔진의 A5·나눔명조 11pt 견본은 0%에서 본문 16줄의 끝이 일치하고 −7%에서 둘째 문단 셋째 줄이 달라진다. 이 자료로 강제 배치 재현, 모든 공백의 한계 준수, 각주 이동, 모델 우위까지 입증할 수는 없다.
- 검증은 로컬과 루프백 웹 환경에서 수행한다. 이 계획의 배포 검증 대상은 같은 이미지로 실행한 웹 서비스와 로컬 설치판이다.

## Review Focus

1. 미리보기 요청 두 개가 역순으로 끝나거나 편집 직후 내보내도 현재 입력과 일치하는 PDF를 받는다(T4·T5).
2. 제어문처럼 보이는 한글 원고·각주·양식 문자열이 문자로 인쇄되거나 필드 오류가 되며 실행 경계를 넘지 않는다(T1·T3).
3. 두 저자가 동시에 출력하거나 한 엔진이 멈춰도 서로의 내용이 섞이지 않고 다음 요청이 성공한다(T4).
4. 간격 수치나 엔진 로그가 정상이더라도 최종 PDF에서 줄 끝이 어긋나거나 각주가 이전 쪽에 남으면 실패다(T3·T6).
5. 로컬에만 있는 글꼴 또는 암묵적 대체 글꼴이 지면 차이를 숨기면 빌드를 거부한다(T2·T6).

---

## 총괄의 배정표와 인수 규칙

아래 표는 **승인된 파일 소유권**이다. 경로의 소유자는 한 명이다. 다른 담당 영역에 변경이 필요하면 총괄이 계약 예제와 함께 해당 소유자에게 돌려준다. 현재 자료와 이 계획은 구현 worker의 쓰기 대상에서 제외한다.

|담당|첫 산출물과 전용 파일|후속 소유권|
|---|---|---|
|A 원고·양식|T1 `app/document.py`, `app/style.py`, `tests/test_input.py`|주석 문법, 목록 표현식, 양식 데이터|
|B 조판|T3 `app/typeset.py`, `app/tex/book.tex`, `app/tex/layout.lua`, `tests/test_typeset.py`|PDF 표현과 실제 배치 후보|
|C 백엔드|T4 `app/server.py`, `app/jobs.py`, `tests/test_jobs.py`|변환·AI HTTP 연결|
|D 작업 화면|T5 `app/static/index.html`, `app/static/editor.js`, `app/static/editor.css`, `tests/test_editor.py`|가져오기·양식·주석·AI 조작|
|E 배포·검측|T2 `Dockerfile`, `requirements.lock`, `runtime.lock`, `tests/pdf_metrics.py`, `tests/test_runtime.py`|글꼴·변환기·모델 버전 및 독립 PDF 판독|
|총괄·인수|T6 `tests/test_slice.py`, `tests/fixtures/page-edge.md`, `tests/fixtures/layout-cases.json`|계약 변경, 통합 순서, 편집자 검수와 릴리스 판정|
|F 가져오기 / G AI|후속 표의 `app/importers/` / `app/ai/`와 지정 테스트|서로의 코드와 A~E 소유 파일 대신 테스트된 함수를 인계|

**실행 순서** — 사용자 승인 → 총괄이 실제 SPEC 버전과 아래 계약·검측 표본을 고정 → A·E·D 병렬 시작(D는 HTTP 응답 대역 사용), C는 엔진 대역으로 작업 → A·E 인계 후 B는 실엔진으로 작업 → A+B → C → D → T6 순으로 통합한다. 선행 담당 수정 중에는 인계본을 읽기 전용으로 쓴다. T6 전에는 모델이나 변환기로 첫 단면 실패를 우회하지 않는다.

**모든 과제의 한 주기** — 아래 각 체크를 별도로 수행한다. 지정 테스트를 먼저 작성하고 같은 명령에서 의도한 단언/미구현 실패(RED)를 확인한다. 최소 구현 뒤 같은 명령의 GREEN을 확인하고 리팩터링 후 재실행한다. 담당은 경로 목록, RED/GREEN 출력, 샘플 산출물, 한계를 인계한다. 총괄은 각 과제마다 독립 검토자에게 스펙 충족 → 코드 품질 순으로 검토를 맡기고 수정·재검 후 수락한다. 현재 폴더에는 Git 이력이 없다. 총괄은 착수 시 기준 파일을 기록하고, 각 담당의 변경분과 테스트 결과를 함께 인계받는다.

## 첫 수직 단면

**사용자가 수행할 한 작업.** 로컬 웹 주소를 열고 비교 원고를 입력한다. A5 양식에서 갱신을 누르고 옆의 읽기 전용 PDF를 확인한다. 제목을 고치고 본문을 11pt에서 16pt로 바꾼 직후 다운로드한다. 화면과 파일 양쪽에서 고친 제목·본문 크기·달라진 지면을 확인한다. 페이지 끝 각주가 있는 별도 표본으로 같은 흐름을 반복한다. 이 단면의 끝은 T6 인수다.

### T1 · A — 원고와 양식을 안전한 데이터로 읽는다

**계약.** `parse_md(source: str) -> Document`와 `apply_style(current: Style, patch: dict) -> Style`. Document는 순서 있는 blocks(heading의 level, paragraph), 각 블록의 inlines(text, emphasis, strong, note_ref의 id), id별 footnotes를 갖는다. `[^지도]`와 `[^지도]: 주 내용`을 첫 각주 표기로 제안한다. 원문 문자열은 편집기에 그대로 둔다. 초기 Style의 JSON 키는 `page_mm`(폭·높이), `margins_mm`(상·우·하·좌), `body_pt`, `leading_pt`, `body_font`, `heading_font`, `ko_tracking_pct`, `space_pct`(최소·최대)다. 초기 예제는 A5 148×210mm, 좌우 18mm, 상하 20mm, 11/17pt, 자간 0%, 공백 [100,100]. 16pt 표본은 행간도 충분히 크게 지정한다. UI·AI·API가 같은 검증을 호출한다.

- [x] `tests/test_input.py`에 `test_order_and_note`(제목→문단, 강조 범위, 참조 ID와 본문 일치), `test_style_atomic`(`assert current == before`, 140mm 폭에 좌우 75mm는 해당 필드 오류), `test_literal_or_rejected`(본문의 제어문 모양 텍스트 보존, 경로형 font·비유한 수·미정의 키·미해결 각주 거부)을 작성한다.
- [x] `pytest -q tests/test_input.py`에서 위 사례가 구현 부재로 실패하는지 확인한다.
- [x] 소유 파일에 파서와 검증기를 구현한다. HTML 해석과 raw TeX를 끄고 토큰을 허용한 요소로 옮긴다. 글꼴 ID는 E의 목록으로 제한한다. 잘못된 patch는 전부 폐기하고 필드별 원인을 반환한다. [markdown-it-py 입력 지침](https://markdown-it-py.readthedocs.io/en/latest/security.html)
- [x] 같은 명령 PASS 후 B·C·D에 JSON 예제와 오류 예제를 전달한다.

### T2 · E — 같은 배포 이미지와 PDF 판독 기준을 만든다

**계약.** `tests/pdf_metrics.py`의 `read_pages(pdf: bytes) -> list[Page]`는 쪽 크기와 줄별 순서 있는 문자·좌표·글꼴·크기를 PDF에서 읽는다. B가 전한 줄 끝과 별개로 측정한다. 배포 테스트는 이후 모두 같은 이미지의 test 타깃에서 수행한다.

- [x] `test_runtime.py`에 `test_locked_runtime`(실제 바이너리·패키지 버전 및 글꼴 해시 일치), `test_pdf_ruler`(위치를 아는 두 글자 사이의 4 PDF pt 공백을 0.05pt 이내로 복원), `test_embedded_faces`(앱 없이 고정 TeX 교정 표본을 출력해 명조·고딕 지정, 필요한 face 임베딩, 대체·누락 글리프 0)을 작성한다. 일부러 잠금 해시 하나를 바꾼 경우도 실패해야 한다.
- [x] `pytest -q tests/test_runtime.py`가 잠금/판독기 부재로 실패함을 확인한다.
- [x] 고정 Linux 베이스 digest, 저장소 스냅숏, TeX Live·LuaHBTeX·luatexko·luaotfload 및 의존 패키지 버전, 재배포 가능한 나눔명조·고딕의 정확한 파일·버전·SHA-256·라이선스를 `runtime.lock`에 기록하고 빌드가 검사하게 한다. 런타임에 설치·다운로드 없이 사용한다. Python 의존성도 버전·해시로 고정한다. 기본 이미지에 앱을 담고 test 타깃에만 PDF 판독·브라우저 도구를 둔다.
- [x] `docker build --target test -t jjokgyeol:test .` 후 `docker run --rm --network none jjokgyeol:test pytest -q tests/test_runtime.py` PASS. 이후의 pytest 명령은 이 test 환경 기준이다. E는 측정기 교정 PDF와 이미지 식별자를 넘긴다.

### T3 · B — 규칙 조판과 두 배치의 실측

**계약.** `typeset(doc: Document, style: Style, workdir: Path) -> bytes`는 완성된 PDF를 반환한다. 운영 입력에는 줄 끝 강제 지정 옵션을 두지 않는다. 테스트 전용 고정 후보 A/B는 같은 문단에서 허용된 서로 다른 어절 끝 및 간격으로 구성하고 하나는 한 줄 더 쓰게 한다. B는 신뢰된 Lua 모듈의 공개 문단 처리 지점에서 선택된 후보를 재현한다. 후보 ID만 바꿔 비교하고, 디시전 모델은 이 단계에 필요 없다.

- [x] `test_typeset.py`에 `test_literal_pdf`(예약문자·역슬래시·중괄호·퍼센트·제어문 모양의 각주가 글자로 출력되고 작업 폴더에 예상 밖 파일 0), `test_two_layouts`(`assert ends_a == expected_a and ends_b == expected_b and ends_a != ends_b`), `test_note_follows_reference`(바뀐 줄 수로 참조가 다음 쪽으로 이동하며 각주 본문은 그 쪽 하단에 한 번만 존재)을 쓴다.
- [x] `pytest -q tests/test_typeset.py`에서 실제 PDF가 없거나 지정한 배치와 달라 실패하는지 확인한다.
- [x] 고정된 `book.tex`와 `layout.lua`가 구조화한 데이터 파일만 읽게 한다. 블록 종류를 미리 작성한 조판 동작에 매핑하고 원고 문자열은 제어문이 될 수 없는 문자 토큰으로 전달한다. 양식의 수치·색·글꼴 ID도 형식별로 전달한다. 원고·필드의 소스 삽입, Lua 평가, 파일명·명령 생성은 금한다. 셸 호출 없이 고정 argv, shell-escape 해제, 읽기 전용 템플릿과 요청별 쓰기 폴더를 쓴다. 제목·문단·강조·실제 각주와 규칙 기반 출력을 먼저 연결한다.
- [x] 같은 명령 PASS. PDF 문자 원점의 차이에서 같은 글꼴·크기의 기준 advance를 빼 한글 자간을 구한다. 어절 사이도 직전 글자 advance를 뺀 폭을 해당 글꼴 기본 공백과 비교한다. PDF 좌표 단위와 TeX pt를 환산한다. 양쪽 후보 모두에서 줄 끝·한글 자간·실제 공백 범위·영문 내부 간격·여백·각주 위치를 E 판독기로 대조한다. 수치 반올림 허용차 제안은 0.05 PDF pt이며 허용차보다 큰 위반을 중앙값으로 덮지 않는다. 지정한 결과를 재현할 수 없으면 그 PDF와 측정치를 총괄에 넘기고 모델 작업을 보류한다.

### T4 · C — 동시 요청과 실패를 격리하는 출력 API

**계약.** `POST /api/render`, JSON `{"source":"# 제목","style":{…}}` → 200 `application/pdf` 바이트, `Cache-Control: no-store`. 실패는 `{"code":"…","field":"…","message":"…"}`, 필드가 없으면 null. 422 입력 오류, 413 크기 초과, 429 처리 슬롯 부족, 504 시간 초과, 500 엔진 실패. 요청 하나가 PDF 하나를 소유한다. 서버에 공개 파일 주소를 만들 필요가 없다.

- [x] `test_jobs.py`에 `test_two_clients`(서로 다른 고유 문구로 동시에 요청해 각 PDF에 자기 문구만 존재), `test_capacity`(두 슬롯 점유 중 세 번째 429), `test_cleanup_and_retry`(엔진 비정상 종료·부분 PDF·시간 초과·요청 취소 각각에서 자식 프로세스와 임시 디렉터리 0, 다음 요청 200), `test_input_limit`(스트리밍 본문도 상한 초과 시 413)을 쓴다.
- [x] `pytest -q tests/test_jobs.py`가 실패하는지 확인한다. 느린 자식·비정상 종료는 합성 실행 파일과 동기화 신호로 재현한다.
- [x] `server.py`가 T1 검증 후 `jobs.py`를 거쳐 B를 호출한다. 제안 초깃값은 프로세스 1개, 동시 조판 2건, 요청 1MiB, 작업당 30초다. 감독 프로세스가 렌더 작업 전체와 자식 엔진을 한 프로세스 그룹으로 묶고 기한/취소 시 종료·회수한다. 제한된 임시 공간, 비특권 사용자, 오류 경로의 슬롯 반환과 원고·절대경로 없는 사용자 메시지를 검증한다.
- [x] 같은 명령 PASS. 통합 때 두 클라이언트 검사는 실엔진으로도 돌린다.

### T5 · D — 편집 화면이 최신 PDF 한 개를 공유한다

**계약.** D는 T4의 HTTP만 사용한다. 원고·양식이 한 번이라도 바뀌면 편집 버전을 증가시킨다. 최신 성공 응답의 Blob URL을 읽기 전용 PDF 프레임과 다운로드 링크가 공유한다. 변경 뒤 내보내면 해당 버전의 렌더를 기다려 양쪽을 같이 바꾼다.

- [x] `test_editor.py`에 `test_export_dirty`(버튼 갱신 없이 제목·크기 수정 → 현재 값으로 요청 1회, 뷰어와 다운로드 SHA-256 동일), `test_out_of_order`(이전 성공/오류가 뒤늦게 와도 최신 지면 유지), `test_failed_export`(오류 표시, 이전 지면에 이전 버전 표시, 잘못된 다운로드 0)을 쓴다.
- [x] `pytest -q tests/test_editor.py`에서 마크다운 저장·재개방 후 원문 보존 및 브라우저 동작 단언의 RED를 확인한다.
- [x] 정적 화면에 UTF-8 마크다운 열기·편집·저장, 양식 입력, ‘미리보기 갱신’, ‘PDF 다운로드’, 상태 메시지, 옆의 PDF 프레임을 연결한다. 같은 버전의 진행 중 요청은 공유하고 오래된 Blob URL은 프레임 교체 후 해제한다.
- [x] 같은 명령 PASS. 테스트 대역으로 응답 순서를 바꿔도 성공해야 한다. T6에서 실제 내장 PDF 뷰어로 열어 한국어 본문까지 읽는다.

### T6 · 총괄 — 첫 단면 인수

- [x] `test_slice.py`의 `test_real_edit_export`에서 실제 서버에 비교 원고를 넣고 A5 크기, 수정 전후 제목·본문 11/16pt, 뷰어 Blob과 받은 파일의 해시 일치, 다운로드 직전 수정 반영을 단언한다. 페이지 차이가 확실한 긴 표본도 사용한다.
- [x] `test_deployment_parity`에서 같은 이미지 digest를 독립 컨테이너 두 개(로컬 실행/웹 서버 실행 모사)로 띄워 같은 입력의 줄 끝 배열·쪽별 배치·글꼴 일치를 검사한다. 별도 실행 간에는 이 지면 서명을 비교하고, 한 요청 안에서는 PDF 바이트를 비교한다.
- [x] `test_pdf_gate`에서 `page-edge.md`에 각주 참조 앞의 텍스트를 고정하고 `layout-cases.json`에 A/B별 기대 줄 끝, 목표 간격, 참조·각주 쪽을 먼저 적는다. T3과 같은 문단·판형·글꼴·크기에서 후보만 바꿔 줄 수 및 각주 이동을 실측한다. 별도로 11→16pt에서 참조·각주 모두 2→3쪽, 2쪽에 잔류 0건을 확인한다. 기대값을 실행 결과에서 복사해 합격 처리하지 않는다.
- [x] `pytest -q tests/test_slice.py`의 미연결 상태 RED를 남기고 A+B→C→D를 연결한다. 총괄은 T3 착수 전에 이 과제의 표본과 기대값부터 B에게 인계한다. A/B에서 조판한 두 PDF는 T3 테스트 경로에서 생성하고 브라우저에서는 제품의 규칙 경로만 사용한다.
- [x] 같은 명령 및 `pytest -q` PASS 후 일반 브라우저에서 PDF 쪽 넘김, 한글 글리프, 각주 하단을 육안 확인한다. 이미지 digest, 글꼴 해시, 두 배치 PDF·쪽별 측정표, 두 요청 격리 결과와 실패 후 복구 기록이 있어야 첫 단면을 수락한다. 웹 서버 모사는 공개 운영 검증과 구분해 기록한다.

## 첫 단면 이후의 작업과 검증

각 행은 독립적으로 수락할 과제다. 한 행 안에 여럿이 참여할 때 왼쪽 담당이 데이터/함수를 인계한 뒤 오른쪽 담당이 자기 소유 파일에 연결한다. 아래의 `tests/test_이름.py`마다 **표에 적힌 단언 작성 → `pytest -q tests/test_이름.py` RED → 명시한 산출물 구현 → 같은 명령 GREEN → 독립 검토·인계**를 적용한다. 모든 사용자 기능은 D의 작업 화면과 실제 PDF 또는 다시 연 원고로도 확인한다. 변환과 모델도 T4와 같은 시간·입력·격리 경계 안에서 실행한다.

|과제·선행|담당·산출물·인터페이스|수락 테스트와 기준|
|---|---|---|
|[x] T7 출판 양식 · T6|A→B→D. 기존 Style과 폼을 확장한다. 판형·사방 여백·행간·기본 자간·최종 자간 한계·공백 한계를 분리하고 본문/제목 단계/주/풀이별 family·size·color·weight·style, 머리글·바닥글의 텍스트·정렬·쪽 번호를 지원한다.|A 소유 `tests/test_publication.py`의 `test_report_style`. 본문 명조 11pt #222222, 제목 고딕 굵게 18pt #17365D, 이탤릭 강조, 기관명과 각 쪽 번호를 PDF에서 확인한다. 판형/14pt/16pt 변경 시 원고 동일·지면 변경, 불가능한 여백은 원자적으로 거부한다.|
|[x] T8 단계별 목록 · T7|A→B→D. `app/list_marks.py`의 `format_mark(expr: str, ordinal: int) -> str`. 원고의 목록 종류·순서·부모·깊이는 Document에, 단계별 표현식·들여쓰기·표지 뒤 간격은 Style에 둔다. 조 제목은 원고의 제목으로 작성한다. [조 제목과 가지번호 후속 계획](2026-11-19-article-numbering.md)에서 조 제목 표현식과 개별 번호 고정을 다룬다. [목록 인수 기록](../list-acceptance.md)|A 소유 `tests/test_lists.py`의 `test_legal_report_and_literals`. 제1조 ①→1.,2.→가.→(1)→(가), ②의 1.과 제2조의 ①, 쪽 넘김 번호 연속을 확인한다. `ㅁ/ㅇ/-` 반복, 긴 항목 둘째 줄 x좌표, `[붙임 2호]`에서 `(나)`로 교체, 공백·구두점과 `{{`·`}}` 출력, 잘못된 중괄호/토큰의 단계 오류·기존 양식 보존을 검사한다.|
|[x] T9 주석 네 종류 · T6, 양식 연결 T7|A→B→D. `app/annotations.py`에 인라인 참조와 정의를 파싱한다. 참조는 `[^id]`, 정의는 종류에 따라 `[^id]: 내용`, `[^id]{미주}: 내용`, `[^id]{메모}: 내용`이다. 풀이는 `[판형]{풀이=책의 크기}`다. 중복 정의와 정의 없는 참조는 원고 오류로 표시한다. T5의 마크다운 열기·저장 경로로 주석의 왕복 보존을 검증한다. 미주는 문서 끝에 싣고 메모는 원고에서만 표시한다.|A 소유 `tests/test_annotations.py`의 `test_reopen_and_reflow`. 저장·재개방 후 네 종류·내용·대상 일치, 각주 쪽 이동과 미주 끝 배치, 메모 재열람. 두 번째 ‘판형’만 작은 ‘(책의 크기)’와 묶어 다음 줄로 보낸다. 한 줄보다 긴 묶음은 풀이 안에서만 나누고 일반 괄호는 본문 크기를 유지한다.|
|[x] T10 가져오기 · T6, 구조 검증 T8·T9|F→C→D. `app/importers/read.py`의 `import_file(name: str, data: bytes) -> ImportResult`는 새 markdown과 요소별 warnings를 반환한다. `POST /api/import`에 파일을 올리고 결과를 새 .md로 연다. ASCII/UTF-8 TXT·HTML → DOCX → HWPX → 바이너리 HWP 순서를 제안한다. 형식별 reader는 `text_html.py`, `docx.py`, `hwpx.py`, `hwp.py`에 둔다. DOCX는 Pandoc reader 후보, HWPX는 ZIP/XML, HWP는 실제 바이너리를 읽는 로컬 변환기 후보를 샘플로 선정·고정한다. [Pandoc 입력 형식](https://pandoc.org/MANUAL.html)|F 소유 `tests/test_imports.py`의 `test_format_sample`을 다섯 형식별로 실행한다. 실제 원본 각각의 해시 보존, DOCX/HWPX/HWP 각각 제목 단계·중첩 순서·굵게 범위·머리글/자동 쪽수 배제, HTML 링크 문구+목적지, TXT 빈 줄 두 문단·짧은 첫 줄 보존. 그림 누락 경고와 남은 본문, 손상/과대 압축 파일 오류, 외부 자원 미취득, 실패 시 편집 중 원고 보존. 실물과 대조하지 못한 형식은 미완료로 남긴다.|
|[x] T11 혼합 조판 규칙 · T6·T7|B와 E. `app/tex/layout.lua`가 실제 글꼴로 허용 후보를 측정하고 규칙으로 문단 전체를 선택한다. 원고 공백과 문단 경계를 보존한다. B는 후보별 줄 끝·공백 폭·기본값+줄별 보정 후 자간·줄별 남은 폭을 G에 인계한다.|B 소유 `tests/test_mixed_layout.py`의 `test_spacing_limits_and_breaks`. 공백 100%·줄별 보정 끔에서 0/−3/−7%별 한글 실측, 영문 내부 자간 동일. 기본 공백 4pt의 교정 표본에서 고정 4pt와 가변 2.8~5.2pt를 개별 검사하고 좁혀 영단어를 앞줄에 넣는 후보도 확인한다. 기본=하한 −7%이면 더 좁아지지 않는다. 어절 우선, 일반 영단어 보존, 긴 단어·URL 내부 개행, 여닫는 부호 금칙, 마지막 줄 비확장, 제목과 다음 본문 첫 줄의 쪽 일치를 두 본문 폭에서 검증한다.|
|[x] T12 양식 AI · T7·T8|G→A→C→D. `app/ai/style.py`의 `suggest_patch(request: str, current: Style) -> dict`를 위한 로컬 추론 어댑터를 붙인다. 자연어 요청에서 얻은 제한된 patch를 T1의 같은 검증기로 검사하고 변경 필드를 보여 준다. `POST /api/style-suggestion`은 검토용 patch를 반환하고 저자가 적용/재편집한다.|G 소유 `tests/test_style_ai.py`의 `test_partial_patch`. ‘본문은 16pt로 하고 바닥글 가운데에 쪽 번호’ 적용 → 직접 14pt 수정 후 바닥글 유지. ‘본문만’은 제목·여백 보존, 목록 표현식 검증 동일, 무효 patch·모델 실패 시 기존 값 유지. 고정 대역으로 회귀 검사하고 선정된 실제 로컬 모델에서도 위 요청이 성공해야 기능 수락.|
|[x] T13 문단 디시전 모델 · T3 실증·T11|B→G→B. `app/ai/decision.py`의 `choose(candidates: list[Candidate]) -> str`가 유효 후보 ID를 반환한다. Candidate는 ID, 줄별 끝 위치·남은 폭·최종 자간·공백 편차·이웃 줄 차이·어절/부호 특징으로 구성한다. 실제 모델 후보는 동의된 로컬 자료로 학습 또는 평가한다. B는 ID와 허용 범위를 재검사해 같은 글꼴로 출력한다.|G 소유 `tests/test_decision.py`의 `test_choice_reaches_pdf`. 서로 다른 두 유효 ID가 실제 PDF에서 지정한 줄 끝을 낸다. 위반 후보는 추론 입력에서 제거하고, 모델 없음·기한 초과·알 수 없는 ID는 결정적인 규칙 결과로 복귀한다. 원고·최종 간격 한계·각주 배치를 T3 판독기로 재검한다. 모델 파일 해시와 런타임을 E가 두 환경에 고정한다.|
|[ ] T14 사람 기준 품질 판정 · T11·T13|총괄·편집자, E 검측. `evaluation/reading-sheet.csv`에 익명 표본 ID·원고 그룹·선호/동률·붙어 보이는 위치·수정 문단과 이유를 기록한다. 평가 전에 학습과 겹치지 않는 원고, 순서 무작위화, 편집자가 같은 원고·글꼴·판형에서 만족할 때까지 손본 기준 지면, 채택 기준을 고정한다. 기존 InDesign 출력은 조건이 맞을 때만 참고한다.|총괄 소유 `tests/test_evaluation.py`의 `test_blind_set`에서 학습/평가 원고 중복 0, 평가자용 표에 엔진·모델·설정 노출 0을 단언한다. 사람이 양끝맞춤·가변 공백으로 글꼴 2종×11/16pt×기본 자간 0/−3/−7%의 검측된 지면을 비교하고, 별도 쌍에서 규칙/모델 선호·동률과 편집자 기준 대비 잔여 수정 대목을 기록한다. 제약 위반 수, 선택과 PDF 줄 끝 불일치 수, 갱신 시간 분포를 같이 보고한다. 사람 판정 확보 전 품질 달성 주장을 보류한다.|

**후속 통합 순서.** T6 뒤 F는 변환 표본부터, A·B·D는 T7부터 시작한다. T8과 T9는 A·B·D 공유 파일의 동시 편집을 피하도록 이 순서로 통합한다. T10 변환 함수는 형식별로 검토한 뒤 화면에 연결한다. T11의 B 작업과 T12의 G 작업은 계약 고정 후 병행할 수 있다. T13은 B·G 인계가 끝나야 시작하고 T14에서 모델 채택을 판단한다. 독립 전문 검토자는 원본·산출물·측정치에 의견만 남긴다.

**첫 버전 종료.** 총괄은 T7~T14를 포함한 전체 테스트와 T6의 두 실행 방식을 다시 확인하고, 두 AI의 실모델 결과 및 규칙 복귀, 다섯 형식 실물 대조, 편집자 검수표를 요구한다. 모델 기본 활성화 제안 조건은 문단 제약 위반·줄 끝 불일치 0, 블라인드 평가에서 모델 선호가 규칙 선호보다 많음, 측정한 응답시간을 사용자가 수용함이다. 미달하면 규칙을 기본으로 유지하고 모델 평가 결과와 잔여 과제를 보고한다.

**첫 버전 이후 결정.** PDF 인수 뒤 DOCX·ODT·HWPX 추가 출력, 이미지 크기·위치, 여러 쪽 표를 별도 의제로 검토한다. 특정 문장/문단 크기, CSS 원문 편집, 양식 파일 문법, 최종 기본 양식·간격 범위, 로컬 설치 도구와 완전 오프라인 범위는 사용성·품질 결과로 결정한다. 위 문법·스택·작업 한도와 임시 기본값은 이번 계획의 제안이다.

### 2026-11-20 후속 작업의 비교 기준

T7 변경 전 커밋 `446768a`의 출판 설정·양식 파일·참고 구간 규칙·목록·양면 여백·조판 검사 104건이 통과했다. Linux ARM64 서비스로 기본 양식과 저장 양식을 각각 재출력한 `test_prechange_geometry` 2건도 통과했다. 두 파일의 글자 수는 각각 452개이며, 기본 양식은 1쪽, 저장 양식은 2쪽이다. 줄별 글꼴·크기·기준선·좌표의 기대 해시는 기존 테스트에서 읽는다. [T7 설계안](../specs/2026-11-20-publication-typography-design.md)

T10의 DOCX·HWPX 입력은 저장소의 `proofs/`에서 각 3종을 확인했다. 바이너리 HWP 입력 후보는 아래 공개 표본의 원본 바이트를 읽어 OLE 서명과 SHA-256을 확인하고, 같은 리비전의 `.formatted.xml`과 대조했다. 실제 가져오기 결과를 비교하는 시험은 T10에서 수행한다. [원본 표본과 판독 결과](https://github.com/mete0r/pyhwp/tree/83239f0d3bdf438b2c9f7dcff455a6e841154a39/tests/hwp5_tests/fixtures)

| HWP 표본 | 원본 SHA-256 | 비교할 내용 |
| --- | --- | --- |
| `lists.hwp` | `054ef256c43dc611987c7c3577e30c7288b981c042b6fceb6587abeff2e5edee` | 번호 목록과 개요 제목에서 각각 0·1·2단계가 실제로 쓰인다. 본문 18번째와 45번째 문단은 둘 다 ‘3-2-1’이다. 앞은 3단계 목록 항목, 뒤는 3단계 개요 제목이어야 한다. |
| `charshape.hwp` | `8eb333ab110db6f91d7e426d507d41ca09cacd4d40d945188b5ec40663d7576b` | 두 번째 문단 ‘기울임진하게’에서 ‘기울임’에만 기울기가, ‘진하게’에만 굵기가 적용된다. |
| `headerfooter.hwp` | `8dcc2edecf32b86724819de3265529d57200b502b9cc5dbc0378daa041c274c8` | 머리글·바닥글·자동 번호 각 1개 |
| `sample-5017.hwp` | `ff3faae938610fcfbedc21c27c507ca0cc038b44872b65d10fcad0b55af67ba2` | 복합 문서의 문단 26개와 자동 번호 |

저장소의 `banks.docx`는 목록 두 항목 모두 1단계라서 중첩 보존을 판정하기 어렵다. 아래 두 원본의 본문 XML에서 실제 사용한 단계와 순서를 별도로 판독했다. 이 기대값은 T10 변환기 결과와 독립적으로 비교한다.

| 원본 | SHA-256 | 변환 전 확인한 순서와 깊이 |
| --- | --- | --- |
| [Pandoc의 lists.docx](https://github.com/jgm/pandoc/blob/de38596d097cdfca456f7b916c6e32deaf6913ca/test/docx/lists.docx) | `16deaa246fa4c3d06c6ef0764df0f3957b9f96b38cd7da5c1b94e99198970252` | 7~9번째 본문 문단 ‘two’, ‘three’, ‘four’는 1→2→3단계 목록이다. |
| [hwpxlib의 ParaHead.hwpx](https://github.com/neolord0/hwpxlib/blob/f9fd2255ac0fc57414e0b657d115e7de51d31c65/testFile/tool/textextractor/ParaHead.hwpx) | `ac351385ba4d084eb93e898274ea5eb6928cd7d39db6ab890539c368e7dfc96b` | ‘번호3’의 두 자식은 ‘번호3-1’, ‘번호3-2’다. ‘개요2’, ‘개요2-1’, ‘개요2-1-1’은 1→2→3단계 제목이다. ‘번호4’ 직후 항목은 1단계에서 4단계로 건너뛴다. 중간 항목을 원문으로 오인하지 않도록 변환 결과와 경고를 함께 검사한다. |

## 2026-11-20 통합 인수

검증한 제품 코드 커밋은 `358dbb6`이다. 실행 중인 Linux ARM64 컨테이너의 앱 파일 50개가 Git 파일과 SHA-256까지 일치했다. 아래 결과는 검사 환경별 집계다.

| 환경 | 확인 결과 |
| --- | --- |
| 네트워크를 끈 test 이미지 | `pytest -q` 900건 통과, 외부 서비스 등이 필요한 56건 건너뜀. Starlette TestClient 사용 중단 예고 1건. |
| 서비스에 연결한 호스트 Chromium·PDF 판독 | 65건 통과. 다섯 형식 가져오기, 원고·양식 저장과 열기, 주석, 양면 배치, 스캔 서식, 두 실행 환경의 지면, 실모델 문단 선택을 확인했다. |
| 실제 양식 제안 모델 | 본문 16pt·가운데 쪽 번호를 적용한 뒤 본문만 14pt로 고쳤다. 제목 18pt와 쪽 번호 중심 209.764 PDF pt가 유지됐다. [인수 기록](2026-11-20-style-suggestion.md) |
| 고정 평가 원고 두 편 | 두 원고의 규칙·모델 응답 네 개에서 제약 위반·줄 끝 불일치 0건. 보관된 영수증·PDF를 최종 test 이미지에서 재검증했다. [문단 선택 기록](../decision-acceptance.md) |
| 예제 출력 | 보고서 11pt·16pt PDF, 주석 11pt·16pt PDF 네 개의 글자·좌표가 저장소의 견본과 일치했다. [보고서](../../../proofs/publication-typography) · [주석](../../../proofs/annotations) |

T14의 자간 판독용 익명 PDF 12개와 입력 양식은 준비됐다. 편집자가 확정한 같은 조건의 기준 지면 두 개, 자간과 규칙·모델의 실제 판독표, 사용자의 갱신 시간 수용 판정은 대기 중이다. 제품의 기본 문단 선택은 규칙이다. [판독 안내](../../../evaluation/reader-guide.md) · [운영자용 인수 기록](../../../evaluation/acceptance.md)
