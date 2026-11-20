# 쪽결 편집기와 작업 한도 리팩터링 계획

**목표**는 같은 원고·양식의 지면과 저장 결과를 유지하면서, 양식 오류를 표시하는 곳과 십진수 비교·서버 한도를 결정하는 곳을 명확히 하는 것이다.

**구조와 실행 방식**은 기존 Python/FastAPI, 브라우저 JavaScript, LuaLaTeX를 유지한다. 한 작업자가 `superpowers:executing-plans`에 따라 세 변경을 순차적으로 검증하고 각각 커밋한다. 기준 커밋은 `002170c`다. [설계](../specs/2026-11-20-editor-contract-refactor-design.md) · [개발 원칙](../../../AGENTS.md)

## 공통 제약과 검토할 경계

| 사용자가 겪는 상황 | 보존할 동작 | 담당 단계 |
|---|---|---|
| 조 번호 표현식이나 제본 여백이 잘못됨 | 미리보기와 양식 저장이 해당 입력을 표시한다. 각 경로의 전체 오류 문구와 마지막 정상 PDF를 유지한다. | 1 |
| 저장 요청 도중 입력을 고침 | 뒤늦은 오류가 고친 값에 붙지 않는다. 성공한 저장은 클릭 당시 양식을 내려받는다. | 1 |
| `+025.400` 입력 후 mm·pt를 왕복하거나 1sp만 남는 판형을 다시 엶 | 입력 철자와 저장 정밀도를 보존하고, 절반 sp의 양쪽 값과 정확한 경계에서 기존 반올림을 유지한다. | 2 |
| 조판 중 원고를 가져오거나 느린 전송을 중단함 | 공유 슬롯의 포화 응답, 기한과 연결 해제 뒤의 재시도가 유지된다. | 3 |
| 모델·규칙 지면을 전환하거나 9단계 목록·양면 줄 번호를 다른 원고에 다시 씀 | 각 방식의 줄 끝과 어절, 글자 위치, 목록 표지, 물리적 쪽별 줄 번호가 기준판과 같다. 다운로드는 현재 선택한 PDF와 같다. | 인수 |

## 기준판 확보

- [x] 계획 작성 시 `002170c`에서 대표 검사 8건을 먼저 실행했다. 수정 후 도착한 저장 오류, 조 번호·제본 여백 오류, 1sp 판형, 반 sp 들여쓰기, 지연된 PDF 전송, 느린 원고 가져오기, 홀짝 줄 번호 양식 왕복이 모두 통과했다. 아래의 실제 PDF 기준 수집은 구현 시작 시 수행한다.
- [ ] `002170c`에서 기준 서비스와 테스트 이미지를 준비하고 이미지 ID, 글꼴 잠금 파일, 모델 식별값과 사용한 양식을 기록한다. 기존 사용자의 편집기와 별도 포트에서 실행한다. 후보에도 같은 환경을 사용한다.
- [ ] 아래 인수 원고를 기준 서비스로 조판하고 임시 디렉터리에 PDF와 `tests/pdf_metrics.py`의 `read_pages` 결과를 보관한다. 기존 예제 PDF를 새 출력으로 덮어쓰지 않는다. 모델을 선택할 수 있는 비교 원고에서는 응답의 두 PDF를 모두 보관한다.

## 1. 양식 오류를 해당 입력에 표시하는 코드 통합

- [ ] **현행 동작 고정.** `tests/test_legal_options_editor.py`, `tests/test_mirrored_margin_editor.py`, `tests/test_style_file.py`에서 미리보기·저장의 조 번호, 자간 및 여백 오류와 오래된 저장 응답을 확인한다. 빠진 경우만 추가한다. 미리보기에서 여백 외 오류가 오면 이전 여백 표시가 지워지고, 저장에서 온 여백 외 오류는 기존 여백 표시를 직접 지우지 않는 차이도 기록한다.
- [ ] **수정.** `app/static/editor.js`에 `showStyleFieldError(field, message)`를 두고 `app/static/style-file.js`에서도 호출한다. 여백 필드 판별, 조 번호 패널 열기와 활자 오류 전달만 공유한다. 전체 문구, 여백 오류 초기화, 저장 스냅샷 비교, 미리보기 버전 검사는 기존 호출자가 맡는다. 잘못된 파일을 열 때의 오류는 현재 편집 중인 양식의 오류와 구분한다.
- [ ] **검증 후 커밋.** `pytest -q tests/test_editor.py tests/test_legal_options_editor.py tests/test_mirrored_margin_editor.py tests/test_style_file.py tests/test_proof_choice_editor.py`. 실제 입력의 `aria-invalid`, 정상 PDF 유지, 성공한 저장의 내용까지 통과해야 한다.

## 2. 편집기 여러 곳에서 쓰는 십진수 비교 함수 분리

- [ ] **현행 동작 고정.** `tests/test_paper_presets.py`와 `tests/test_indent_units.py`에서 동등한 지수 표기와 사용자 지정 판형, mm·pt 왕복, 빈 입력, 매우 큰 지수, 반 sp 경계를 확인한다. 순수 함수의 구별이 기존 검사로 드러나지 않는 경우만 보강한다. 예를 들어 `sameDecimal("+025.400", "2.54e1")`은 참이고 `decimalKey("1x")`는 `null`이다.
- [ ] **수정.** `app/static/editor.js`의 `decimalKey(value)`, `sameDecimal(left, right)`, `compareDecimal(left, right)`를 그대로 `app/static/decimal.js`로 옮긴다. `app/static/index.html`에서 소비자보다 먼저 로드하고 `tests/test_editor.py`의 정적 파일 목록에 추가한다. 들여쓰기 변환은 `indent-units.js`, 입력 중 상태는 각 컨트롤, 저장 정수 검증은 Python `Style`에 둔다.
- [ ] **검증 후 커밋.** `pytest -q tests/test_paper_presets.py tests/test_indent_units.py tests/test_reference_editor.py tests/test_mirrored_margin_editor.py tests/test_editor.py`. 첫 화면의 조판 요청을 확인하고 판형·자간·참고 서식의 기존 호출자가 동일한 비교 결과를 받는지 검사한다.

## 3. 서버가 요청별로 사용할 한도를 한 곳에서 선택

| 작업 | 바이트 한도 | 동시 작업 슬롯 | 작업 제한 시간 |
|---|---|---|---|
| 조판 | `MAX_BODY` | `SLOTS` | `jobs.TIMEOUT` |
| 원고 가져오기 | `IMPORT_MAX_BYTES` | `SLOTS` | `jobs.TIMEOUT` |
| 참고 자료 분석 | `REFERENCE["max_bytes"]` | `REFERENCE_SLOTS` | `REFERENCE["timeout_seconds"]` |
| 참고 원고의 적용 대상 조회 | `MAX_BODY` | `TARGET_SLOTS` | `jobs.TIMEOUT` |
| AI 양식 제안 | `SUGGESTION["max_request_bytes"]` | `SUGGESTION_SLOTS` | `SUGGESTION["timeout_seconds"]` |

- [ ] **현행 동작 고정.** `tests/test_jobs.py`, `tests/test_import_api.py`, `tests/test_reference_analysis.py`, `tests/test_style_suggestion_api.py`에서 다섯 행을 대조한다. 교차 작업 포화 검사가 없으면 조판이 공유 슬롯을 점유한 상태에서 정상 이름의 가져오기 요청이 429를 받고 해제 뒤 성공하는 경우를 추가한다.
- [ ] **수정.** `app/server.py`의 `DocumentJob`에 요청 시점의 `limits(self)`를 두어 바이트 한도, 기존 세마포어, 시간(초)을 반환한다. `__call__`이 이 세 값을 받아 본문 읽기와 용량 오류 안내에 같은 상한을 전달한다. 기존 설정 파일에서 값을 읽고 요청 사이에는 슬롯 객체를 공유한다. 응답 전송까지 슬롯을 점유하며, 실패 응답을 보내는 별도 기한과 작업별 오류 코드·메시지를 유지한다.
- [ ] **검증 후 커밋.** `pytest -q tests/test_jobs.py tests/test_import_api.py tests/test_reference_analysis.py tests/test_style_suggestion_api.py tests/test_operations.py`. 선언된 길이와 실제 스트림 길이, 늦은 다운로드, 포화·타임아웃 뒤 재시도를 확인한다.

## 최종 인수

- [ ] 기준판과 후보판에서 같은 입력을 출력한다. 양끝맞춤은 `proofs/justified-publication/manuscript.md`의 줄 끝·어절·좌우 폭을, 일반 보고서는 `proofs/general-report/manuscript.md`와 `app/static/general-report.json`의 제목·단계 표지를 대조한다. 목록은 `proofs/numbering/outline-9.md`와 같은 이름의 JSON을, 양면은 `proofs/numbering/manuscript.md`와 `odd-even.json`을 사용한다. `read_pages`로 쪽 수, 줄 문자열, 글꼴·글자 좌표와 여백 숫자를 비교한다.
- [ ] 테스트 이미지에서 네트워크 없이 `pytest -q`를 실행한다. 호스트에서는 후보 주소로 `JJOKGYEOL_REFERENCE_URL`과 `JJOKGYEOL_DECISION_TEST_URL`을 지정한 뒤 `pytest -q tests/test_justified_publication_acceptance.py tests/test_proof_choice_acceptance.py tests/test_general_report_acceptance.py tests/test_numbered_proof_editor.py`를 실행한다. 모델·규칙 둘 모두 선택·다운로드하고, 각 다운로드가 해당 미리보기 바이트와 같아야 한다. 모델 미연결로 건너뛴 항목은 통과로 집계하지 않는다.
- [ ] 예상 밖 차이가 생기면 해당 단계를 분리하고 원인을 기록한다. 기준 PDF나 테스트 기대값을 후보 출력으로 바꿔 통과시키지 않는다. 검증 결과와 커밋을 연결해 기록한다.
