# 양면 보고서의 안쪽·바깥쪽·제본 여백 구현 계획

> **For agentic workers:** Use `superpowers:subagent-driven-development`. Nash가 병렬 개발을 통합하고 Gibbs가 배포본을 독립 검증한다. 체크박스는 검증을 마친 뒤 표시한다.

**Goal:** 원고가 홀수 쪽에서 짝수 쪽으로 밀리면 본문과 각주가 제본 방향에 맞춰 이동하고, 쪽 번호가 새 면의 바깥쪽 본문 경계에 놓이게 한다.

**Architecture:** 기존 `Style`의 정수 길이 검증과 저장 방식을 확장한다. LuaLaTeX에서 한 번 정한 본문 폭과 홀·짝 면의 왼쪽 시작점을 본문 조판과 머리글·바닥글 배치가 함께 사용한다. 편집기는 기존 갱신 버튼으로 만들어진 PDF를 표시하고 다운로드한다.

**Tech Stack:** Python/FastAPI, JavaScript, LuaLaTeX와 설치된 geometry 패키지, pytest, Playwright Chromium, PDFMiner, Linux ARM64 Docker.

**Spec:** [승인된 양면 여백 설계](../specs/2026-11-20-mirrored-margins-design.md)

## Global Constraints

- 왼쪽 제본의 실제 PDF 1쪽을 홀수로 센다. 실제 안쪽은 안쪽 + 제본이다. 위·아래 여백은 모든 면에 공통이다. 첫 면 예외와 각주 전용 쪽도 실제 홀짝을 따른다.
- 편집 요청은 mm, 저장과 조판은 정수 sp를 사용한다. 저장된 `gutter_sp` 생략은 고정 모드, 명시적 0은 제본분이 0인 양면 모드다. `margins_sp`는 양면에서 위·바깥쪽·아래·안쪽 순이다.
- 원고와 기존 교정 PDF는 읽기 전용 대조 자료로 쓴다. PDF 위치 허용 오차는 0.04 PDF pt다. 이전 양식은 필드와 지면 모두 비교한다.

## Review Focus

1. 제본 0인 양면 파일과 고정 파일을 각각 재개방한 뒤 비대칭 여백을 인쇄한다. 작업 1·2·4에서 서로 다른 짝수 면 위치를 검사한다.
2. 제본분을 더하면 본문이나 구간 들여쓰기에 남는 폭이 0이 되는 경우를 작업 1에서 반올림 전·후에 검사한다. 정상 양식의 마지막 1sp도 저장·열기 뒤에 보존한다.
3. 머리글 규칙 없이 조판하거나 각주만 다음 쪽에 이어져도 본문·각주가 실제 면을 따른다. 작업 2에서 세 칸의 머리글과 들여쓴 목록의 좌표도 검사한다.
4. 모드를 잠시 바꾼 뒤 되돌리는 동작, 고정 여백을 실제로 변경하는 동작, 다른 파일을 여는 동작을 작업 3에서 따로 검사한다. 이전 파일의 제본값이 새 파일로 넘어가면 실패다.
5. 참고 후보 적용이나 잘못된 여백으로 재조판하는 동안 화면의 양식, 마지막 정상 PDF, 다운로드 대상이 어긋나는지 작업 3·4에서 확인한다.

---

## 1. 양식과 API · Bernoulli

**담당** `app/style.py`, 출판 기본값이 정의된 `app/config/publication.json`, `tests/test_mirrored_margin_style.py`. 기존 공개 인터페이스는 `apply_style(current, patch)`, `from_stored_style(fields)`, `Style.to_stored()`, `Style.to_editor()`다.

- [x] `test_optional_gutter_roundtrip`을 작성한다. `apply_style(Style(), {"margins_mm":[15,10,15,12],"gutter_mm":"6"}).to_stored()`에서 `gutter_sp == 1118808`, 안쪽 값 `margins_sp[3] == 2237616`을 기대한다. 0, 생략, 비대칭 고정 파일도 왕복한다. 기존 단면 파일은 열고 다시 저장한 JSON이 같다.
- [x] `pytest -q tests/test_mirrored_margin_style.py`로 새 필드를 거절하는 실패를 기록한다. `test_gutter_reduces_available_width`에 음수, 폭 28mm에서 12+6+10mm, 구간 들여쓰기가 남은 폭을 전부 쓰는 경우, 반올림 후 0sp와 1sp가 되는 경계를 넣는다.
- [x] `Style` 끝에 `gutter_sp: int | None`을 붙인다. `gutter_mm`는 mm 입력의 선택 필드로 검증하고, 정수 파일의 `null`·소수·불리언을 거절한다. 누락된 저장 필드는 고정으로 연다. 기존 양면 객체에 다른 속성만 패치하면 제본을 보존한다. 설치 기본값은 기존 고정 여백 설정에서 읽고, 선택 필드를 모든 구형 파일의 필수 항목으로 만들지 않는다.
- [x] `_validate`, `_check_rule_fit`, `_editor_values`가 동일한 수평 여백 합을 쓰도록 한다. 단위 변환 전후의 폭 검증과 좁은 폭에서의 mm 역변환 정밀도를 유지한다. `/api/style/open`, `/api/style/export`, `/api/render`에서 필드별 422와 정상 입력을 검사한다. `pytest -q tests/test_mirrored_margin_style.py tests/test_style_file.py tests/test_integer_slice.py tests/test_publication.py`를 통과하면 계약을 인계한다.

## 2. 본문과 쪽 번호의 실제 좌표 · Galileo

**담당** `app/tex/layout.lua`, `app/tex/running_heads.lua`, `tests/test_mirrored_margin_typeset.py`. 입력은 작업 1의 저장 형식이다. `app/typeset.py`가 이미 `Style.to_stored()`를 엔진으로 전달한다.

- [x] `test_mirrored_text_and_folios`에 2쪽 이상인 원고를 둔다. 홀수·짝수 본문 시작점은 각각 18mm·10mm, 가운데 칸의 중심은 폭 128mm에서 각각 68mm·60mm여야 한다. 같은 원고로 머리글·바닥글 전체를 끈 사례, 끝까지 이어지는 각주, 다음 줄이 있는 목록, 안쪽·바깥쪽이 다른 제본 0 사례를 검사한다. `pytest -q tests/test_mirrored_margin_typeset.py`에서 기존 고정 위치로 실패함을 확인한다.
- [x] `layout.lua`에서 실제 안쪽과 본문 폭을 한 번 계산한다. 양면에서만 geometry의 양면 옵션과 실효 안쪽·바깥쪽을 전달한다. 제본은 한 번만 합산한다. 본문·목록·줄바꿈은 이 폭을 사용한다. `running.setup(settings, book_style, width, left_at_page)`에 같은 폭과 `left_at_page(physical_page)`를 넘겨 세 칸 배치와 충돌 검사도 일치시킨다.
- [x] `test_fixed_asymmetric_pages`에서 고정 좌우가 다른 문서는 모든 면의 시작점이 같은지, `test_gutter_changes_line_width`에서 제본만 늘리면 본문·목록의 줄바꿈이 실제 좁아진 폭을 따르는지 확인한다. `pytest -q tests/test_mirrored_margin_typeset.py tests/test_duplex_typeset.py tests/test_list_typeset.py tests/test_running_heads_typeset.py`를 통과시킨다.

## 3. 직접 편집·저장·참고 후보 · Darwin

**담당** `app/static/index.html`, `editor.js`, `style-file.js`, `reference.js`(모두 `app/static/`), `tests/test_mirrored_margin_editor.py`. 기존 `styleForm.get()`으로 전체 편집 양식을 읽고, 양식 파일을 여는 `set`과 참고 후보를 확정하는 `set`의 호출 목적을 구분한다.

- [x] Chromium에서 `test_toggle_restore_and_reopen`을 작성한다. 고정 왼쪽 12·오른쪽 10 → 양면 제본 6 → 고정 왼쪽 18 → 양면 12+6을 검사한다. 고정 상태에서 오른쪽을 9로 바꾸면 양면 18+0·바깥쪽 9가 된다. 위쪽만 바꾸거나 동치인 소수 표기를 입력한 경우에는 기존 분리를 보존한다. `pytest -q tests/test_mirrored_margin_editor.py`로 면 전환 컨트롤이 없는 실패를 확인한다.
- [x] 여백 모드와 제본 입력, 실제 안쪽 합계와 본문 폭을 표시한다. `margins_mm`의 기존 네 입력을 재사용하고 양면에서 좌우 라벨을 바꾼다. 양면일 때만 `gutter_mm`를 요청에 포함한다. 여백의 십진 입력을 이진 부동소수점 오차가 섞인 값으로 다시 저장하지 않는다. 모드 변경도 기존 미리보기 갱신 상태에 반영한다.
- [x] `test_reference_physical_margins`에서 양면이면 물리 여백 후보의 적용을 막고 고정 모드에서 쓸 수 있다고 표시한다. 머리글·판형 등 함께 선택한 유효 후보는 시험·확정할 수 있어야 한다. 고정 모드에서 참고 여백을 실제 변경한 뒤 양면을 켜면 새 치수와 제본 0을 사용한다. 시험 취소, 다른 양식 열기, 원고만 교체하기도 각각 확인한다.
- [x] `test_invalid_gutter_preserves_preview`에서 갱신·저장 시 서버의 제본 오류를 입력에 연결하고 직전 PDF 바이트와 원고를 대조한다. `pytest -q tests/test_mirrored_margin_editor.py tests/test_duplex_editor.py tests/test_style_file.py tests/test_reference_editor.py`가 통과해야 한다.

## 4. 독립 인수 및 Docker 배포 · Gibbs 검증, Nash 통합

**담당** `tests/test_mirrored_margin_acceptance.py`, `proofs/mirrored-margins/`, `docs/superpowers/mirrored-margins-acceptance.md`, `README.md`, `SPEC.md`. 작업 1~3과 병렬로 Gibbs가 기준 원고·PDF를 확보하고 새 인수 검사의 초기 실패를 기록한다.

- [x] 기존 [양면 양식](../../../proofs/duplex-running-heads/style.json)과 [11쪽](../../../proofs/duplex-running-heads/before.pdf)·[12쪽](../../../proofs/duplex-running-heads/after.pdf) PDF를 기준으로 고정한다. 두 원고의 추적 문장 첫 글자는 현재 모두 종이 왼쪽에서 39.685 PDF pt에 있다. 신규 인수는 현재 고정 좌우 배포본에서 실패하고 기존 표본 검사는 통과해야 한다.
- [x] 브라우저로 기존 양식과 [추가 전](../../../proofs/running-heads/before.md)·[추가 후](../../../proofs/running-heads/after.md) 원고를 연다. 안쪽 12·제본 6·바깥쪽 10mm를 입력해 미리보기 갱신, 다운로드, 양식 저장·다시 열기를 수행한다. 기존 `download_proof`와 `pdf_metrics.read_pages`를 재사용해 각 원고의 미리보기 바이트와 해당 다운로드의 SHA-256, 재개방 후 글자 배치를 대조한다.

| PDF에서 읽을 값 | 추가 전 | 추가 후 |
|---|---:|---:|
| 전체 쪽수 / 추적 문장이 실린 쪽 | 11 / 9 | 12 / 10 |
| 추적 문장의 첫 글자 x0, PDF pt | 51.024 | 28.346 |
| 바깥 번호의 가장자리, PDF pt | ‘9’의 x1 = 334.488 | ‘10’의 x0 = 28.346 |

- [x] 23면의 줄마다 텍스트·글꼴·크기·세로 좌표·쪽 나눔을 대응시킨 뒤 본문·각주 각 글자의 x0와 x1이 종전보다 홀수 +4mm, 짝수 −4mm 이동했는지 검사한다. 표지 번호는 숨고 나머지 각 쪽에는 해당 번호가 한 번만 출력돼야 한다. 9쪽과 10쪽의 실제 지면을 한 장에 담은 비교 PDF를 만든다.
- [x] Nash가 담당별 실패 → 통과 기록을 검토하고 동결한다. `docker build --platform linux/arm64 --target test -t jjokgyeol:mirrored-margins-test .`로 빌드해 고정 이미지 ID로 `docker run --rm --network none --shm-size=512m <테스트-이미지-ID> pytest -q`를 실행한다. `--target local`로 배포 이미지를 빌드하고 빈 로컬 포트에서 소스 마운트 없이 실행한다. Gibbs가 그 주소와 이미지 ID로 `JJOKGYEOL_REFERENCE_URL`·`JJOKGYEOL_TEST_IMAGE`를 설정하고 `pytest -q tests/test_mirrored_margin_acceptance.py tests/test_duplex_acceptance.py`와 나머지 기존 호스트 전용 검사를 실행한다.
- [x] 최종 브라우저 다운로드, 저장 양식, 쪽별 좌표, 미리보기 해시, 이미지 ID와 검사 결과를 인수 기록에 연결한다. 실행 중인 편집기·비교 PDF 링크를 실제로 연 뒤 인계한다. 현재 체크아웃에는 Git 메타데이터가 없어 단계별 인계는 변경 파일과 실제 검증 결과로 기록한다.

[최종 Docker 검증 기록](../mirrored-margins-acceptance.md)
