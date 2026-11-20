# 머리글·바닥글과 자동 쪽 번호 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development`. Nash가 통합하고 담당자들은 아래 계약으로 병렬 개발한다. 진행 단위는 `- [ ]`로 기록한다.

**Goal:** 참고 보고서의 머리글·바닥글을 골라 수정하고, 원고 분량이 바뀐 뒤에도 각 쪽에 올바른 문구와 번호를 인쇄한다.

**Architecture:** 기존 제목 경로와 양식의 구간 규칙에 머리글과 바닥글을 추가한다. 참고 파일 판독기와 직접 편집기는 같은 규칙을 만들고, LuaLaTeX가 확정한 실제 쪽에서 해당 구간의 문구와 번호를 배치한다. 미리보기와 다운로드는 기존 PDF 조판 경로를 공유한다.

**Tech Stack:** Python, FastAPI, 브라우저 JavaScript, LuaLaTeX, Poppler, pytest, Playwright, Docker, 설치된 LM Studio.

**Spec:** [승인된 머리글·바닥글 출력 설계](../specs/2026-11-19-running-heads-design.md)

## Global Constraints

- 공통값과 고유 제목 경로를 사용한다. 서식 적용 전후의 마크다운 원고와 참고 원본의 바이트를 보존한다. 저자가 선택해 확정한 머리글·바닥글 문구만 양식에 저장한다.
- 첫 물리적 쪽은 1이고 숨긴 표지도 센다. 한 쪽의 첫 원고 줄이 양쪽 영역의 구간을 정한다. 각주만 있는 쪽은 마지막으로 나온 원고 줄의 구간을 이어받는다.
- 길이는 저장 시 정수 sp이며 편집 시 크기는 pt, 위·아래 종이 끝에서 영역까지의 거리는 mm로 표시한다. 기존 양식에서 생략된 영역은 꺼져 있다. 칸은 한 줄이고 세 칸의 글꼴·크기·색은 영역 안에서 공유한다.
- 작업별 실패 테스트를 먼저 실행해 실패 원인을 기록하고 구현 후 같은 테스트와 기존 회귀 테스트를 통과시킨다. 현재 작업 폴더에는 Git 메타데이터가 없어 Nash가 담당 파일·검토 결과·테스트 명령을 기준으로 변경을 통합한다.

## Review Focus

1. 숨긴 부모 아래에서 자식이 문구만 바꾸거나 빈칸을 지정하는 경우. 독립적인 표시 여부, 빈 문자열, 상속 복귀를 작업 1·5에서 검사한다.
2. 서로 다른 첫 쪽·기본 파트의 같은 문구, 분리된 PAGE 필드의 저장값, 문자 그대로의 중괄호. 작업 2에서 원본 변형과 필드 의미를 각각 검사한다.
3. PDF 한 쪽만 재분석해 다쪽 번호 근거가 약해지는 경우. 작업 3·5에서 기존 근거와 사용자의 선택을 보존하고 충돌을 표시하는지 검사한다.
4. 앞 장으로 시작해 부록으로 끝난 쪽 다음에 각주 전용 쪽이 오는 경우. 작업 4에서 첫 원고 줄과 마지막 원고 줄에 서로 다른 기대값을 둔다.
5. 한 자리 번호는 맞고 두 자리 번호만 옆 칸을 침범하는 경우. 작업 4·5·6에서 10쪽의 영역 오류와 직전 정상 PDF 보존을 검사한다.

## 작업 경계와 공통 계약

| 담당 | 소유 파일과 산출물 |
|---|---|
| Bernoulli · 양식 | `app/style.py`, `app/publication.py`, `app/config/publication.json`, `app/reference_rules.py`, 새 `app/running_heads.py`, 양식 테스트 |
| Nietzsche · 문서 판독 | `app/reference_office.py`, Office 표본과 판독 테스트 |
| Dalton · PDF·스캔 판독 | `app/reference_media.py`, 필요 시 `app/config/reference.json`의 분석 지시문, 매체 판독 테스트 |
| Galileo · 조판 | `app/document.py`, `app/typeset.py`, `app/tex/book.tex`, `app/tex/layout.lua`, 새 `app/tex/running_heads.lua`, PDF 회귀 테스트 |
| Darwin · 편집기 | `app/static/index.html`, `reference.js`, `editor.js`, `style-file.js`, `editor.css`(모두 `app/static/`), 브라우저 테스트 |
| Gibbs · 독립 인수 검증 | `tests/test_running_heads_acceptance.py`, `tests/fixtures/`, `proofs/running-heads/`, 검증 기록. Nash가 공용 파일과 패키징, `SPEC.md` 반영 및 최종 이미지 통합을 맡는다. |

- **편집·저장.** 기존 `section_rules` 항목의 `path: []`는 문서 전체, 예를 들어 `["부록"]`은 해당 제목 구간이다. `role`은 `header` 또는 `footer`다. `values`에 지정된 키만 덮어쓴다. `visible`은 엄격한 불리언, `left/center/right`는 표현식 문자열, `font`는 기존 글꼴 ID, `color`는 기존 색 형식이다. 편집용 `font_pt/offset_mm`을 저장용 `font_sp/offset_sp`로 변환한다. 무지정·빈 문자열·숨김을 구별한다. 기본 표시 여부·크기·위치는 출판 설정 한 곳에서 읽고 기존 `editor_bootstrap()`으로 편집기에 전달한다.
- **번호 표현식.** `app/running_heads.py`의 `parse_running_text(text: str) -> list[tuple[str, str]]`은 일반 글자를 `("text", value)`, 번호를 `("page", "")`로 전달한다. `escape_running_literal(text: str) -> str`는 원본 글자의 중괄호를 인용한다. `{{현재쪽}}`은 글자이고 `{현재쪽}`은 필드다. 오류 위치를 편집기의 해당 칸과 연결한다.
- **판독 후보.** 기존 `role/key/value/basis/evidence`를 유지한다. 머리글·바닥글 후보에만 출처별 `source_group`과 `variant`를 더한다. 같은 파일의 재시도에서 구역·원본 변형·위치가 같으면 같은 그룹을 사용한다. 원본 변형 값은 `default/first/even/odd/unknown`이다. 단일 스캔의 모호한 숫자에는 `basis="confirm"`과 원문·자동 번호 두 선택지를 전달한다. 후보 출처와 판독 근거는 양식에 저장하지 않는다.
- **조판 입력.** `app/reference_rules.py`의 `running_values(blocks, style, *, fenced_blocks=()) -> list[dict]`가 원고 블록 순서대로 두 영역의 유효 속성을 전달한다. 본문용 `block_values()`와 동일한 제목 경로를 쓰되, 머리글 변경만으로 기존 `render_layout()`의 줄 결합이 달라지지 않도록 한다. 조판 담당자가 줄 분할·블록 결합 이후의 실제 줄에 구간을 전달하는 방법을 결정한다.

## 1. 양식 저장과 제목 구간 상속

- [x] `tests/test_running_head_rules.py`에 `visible is False`, `center == ""`, 빈칸 지정 제거 뒤 `center == "{현재쪽}"`를 서로 다른 단계에서 검사한다. 고유하지 않거나 사라진 제목, 첫 제목 앞, 기존 양식 재저장, 문자 그대로인 중괄호와 알 수 없는 필드의 위치, 음수 위치·잘못된 색·불리언을 숫자로 보낸 입력도 포함한다.
- [x] `pytest -q tests/test_running_head_rules.py`에서 새 역할·표현식·상속 테스트가 실패하는 것을 확인한다.
- [x] 기존 `SectionRule` 변환·검증 및 제목 경로 계산을 확장하고 공통 계약의 함수들을 연결한다. 직접 설정·참고 적용·저장·열기가 같은 검증을 사용하게 한다. 실제 글자 폭 검사는 조판 시 수행한다.
- [x] 위 테스트와 `pytest -q tests/test_style_file.py tests/test_publication.py tests/test_reference_rules.py`를 통과시킨다. 출판 설정에서 기본 크기를 한 번 바꿨을 때 새 양식의 화면 초기값과 API 유효값을 확인할 수 있는 표본을 작업 6에 전달한다.

## 2. DOCX·HWPX에서 실제 문구와 필드 판독

- [x] `tests/test_running_heads_office.py`에 분리된 DOCX run과 단순 필드로 각각 `2026 보고서 · {현재쪽}`을 얻는 표본을 만든다. 저장된 PAGE 값 17, 서로 다른 관계 ID의 기본·첫 쪽·짝수 쪽 파트, 앞 구역에서 상속한 파트, HWPX 하위 문단·자동 번호·독립된 쪽 번호 위치 설정을 검사한다. 본문 숫자와 머리말 본문이 서로 섞이지 않아야 한다.
- [x] `pytest -q tests/test_running_heads_office.py`에서 현재의 ‘지원하지 않음’ 후보와 기대 문구가 다름을 확인한다.
- [x] 기존 `analyze_office(data, fmt, settings)` 경로에서 구역과 연결된 실제 파트를 읽는다. 읽을 수 있는 정렬·글꼴·크기·색·위치는 속성별로 제안한다. 표·그림·여러 줄·전체 쪽 수는 조정이 필요한 부분으로 표시하고 편집 가능한 근거를 남긴다.
- [x] 새 테스트와 `pytest -q tests/test_reference_office.py tests/test_reference_analysis.py`를 통과시킨다.

## 3. PDF·스캔 후보와 쪽별 재분석

- [x] `tests/test_running_heads_media.py`에 같은 위치의 `2026 보고서`와 `－ 17 －`, `－ 18 －` 표본을 넣는다. 연도는 글자, 뒤의 수열은 `－ {현재쪽} －`이고, 단독 이미지의 숫자는 명시적 선택을 요구하는지 검사한다. 다른 위치·불연속 숫자, 혼합 PDF, 다시 요청한 한 쪽의 출처 그룹도 대조한다.
- [x] `pytest -q tests/test_running_heads_media.py`에서 기존의 위치 표시만으로는 문구·번호 테스트를 통과하지 못하는지 확인한다.
- [x] 기존 `analyze_media(data, fmt, settings)`의 줄 좌표·물리적 쪽 순서와 비전 응답 검증기를 확장한다. 좌우·가운데 위치와 문자·필드 근거를 전달한다. 재분석 응답은 어느 쪽의 어느 그룹인지 식별할 수 있어야 한다.
- [x] 새 테스트와 `pytest -q tests/test_reference_media.py`를 통과시킨다. 실제 모델의 결과는 작업 6에서 별도로 기록한다.

## 4. LuaLaTeX 실제 쪽에 출력

- [x] `tests/test_running_heads_typeset.py`에서 본문 첫 줄, 쪽 중간 제목, 첫 줄 제목, 긴 문단의 계속 줄, A장→B장 뒤 한 쪽 이상 이어지는 각주 전용 쪽을 재현한다. 표지의 바닥글이 숨겨져도 다음 쪽은 2여야 한다. 펜스 뒤 밑줄형 제목·기존 각주를 담은 원고는 영역 꺼짐과 켜짐 모두 기존 PDF의 본문·각주 글자 좌표와 비교한다.
- [x] `pytest -q tests/test_running_heads_typeset.py`에서 실제 PDF에 기대한 위·아래 문구가 없는 실패를 확인한다. 새 조판기를 적용하기 전에 Gibbs가 기존 배포 이미지로 좌표 기준본을 확보한다.
- [x] 기존 `typeset(doc, style, workdir)` 경로에서 실제 쪽이 확정될 때 물리적 번호와 첫 원고 줄 구간을 쓰고, 다음 쪽에 넘길 마지막 원고 구간을 따로 유지한다. 기존 본문 폭과 여백 안에서 세 칸을 배치한다. 선택 글꼴의 실제 너비·높이로 칸 충돌과 여백 초과를 판정하고 기존 `layout-error.txt` 경로로 쪽·영역 오류를 반환한다. 일반 글자는 문자로 출력한다.
- [x] 새 테스트에서 9쪽은 맞고 10쪽만 겹치는 표본, 종이 밖·본문 쪽으로 넘치는 표본을 검사한다. `pytest -q tests/test_running_heads_typeset.py tests/test_reference_typeset.py tests/test_typeset.py`가 통과해야 한다.

## 5. 편집·시험·확정 및 복구

- [x] `tests/test_running_heads_editor.py`로 공통 머리글 수정, 표지 숨김, 부록 한 칸 변경, 빈칸과 상속 복귀, 사라진 제목 다시 연결을 조작한다. 출처별 선택과 재분석 후 사용자가 고친 문구·번호 선택이 보존되는지 검사한다.
- [x] `pytest -q tests/test_running_heads_editor.py`에서 필요한 조작 항목이 없어 실패하는지 확인한다.
- [x] 직접 편집과 참고 후보가 같은 영역 편집기를 열도록 연결한다. 숨겨진 영역에 참고 후보를 고르면 ‘표시’ 변경을 시험 전에 제시한다. 모호한 숫자와 재분석 충돌을 사람이 결정하게 한다. 머리글만 선택했을 때 기존 바닥글을 지키고, 기존 시험·적용·취소·양식 저장·열기 흐름을 재사용한다.
- [x] 유효한 PDF를 연 뒤 10쪽 충돌을 만들어 시험·확정·현재 상태 다운로드의 오류와 이전 정상 미리보기 보존을 확인한다. 취소 후 양식과 원고를 비교한다. `pytest -q tests/test_running_heads_editor.py tests/test_reference_editor.py tests/test_editor.py`를 통과시킨다.

## 6. 독립 인수 검증과 배포 이미지

- [x] Gibbs는 구현과 병행해 기존 서비스에서 기준 PDF를 생성한다. 쪽 나눔 지시 없이 표지·본문·부록이 있는 원고에 고유한 추적 문장을 넣고, 중간에 고정된 문단을 추가한 두 번째 원고를 만든다. 본문 좌표만으로 추적 문장이 정확히 **9쪽 → 10쪽**이 되는지 보정한 다음 두 원고를 고정한다. 제품의 구간 결정 코드와 독립적으로 각 쪽의 본문·상단·하단 기대표를 작성한다.
- [x] 기존 서비스 대상으로 `pytest -q tests/test_running_heads_acceptance.py`를 실행해 위·아래 문구 또는 가져오기에서 실패하는지 확인한다. 기존 서비스와 후보 서비스를 구분하는 주소와 이미지 ID를 기록한다.
- [x] Nash가 `docker build --platform linux/arm64 --target test -t jjokgyeol:running-heads-test .`와 `docker run --rm --network none jjokgyeol:running-heads-test pytest -q`로 오프라인 전체 회귀를 확인한다. 새 정적 표본이 필요하면 해당 경로만 `.dockerignore` 허용 목록에 추가한다. 이어서 `docker build --platform linux/arm64 --target local -t jjokgyeol:running-heads .`로 배포 후보를 빌드한다.
- [x] 호스트에서 소스 마운트 없는 후보 이미지의 실제 주소를 `JJOKGYEOL_REFERENCE_URL`, 해당 이미지 ID를 `JJOKGYEOL_TEST_IMAGE`로 지정한다. 기존 호스트 Playwright 환경에서 `pytest -q tests/test_running_heads_acceptance.py tests/test_reference_acceptance.py`를 실행한다. 실물 DOCX·HWPX·PDF와 LM Studio를 거친 스캔을 가져와 기관명을 고치고, 최종 양식에는 저자가 확정한 머리글·바닥글 문구만 들어가고 참고 본문·이미지·판독 근거는 빠지는지 검사한다. 이전 동작을 검사하던 테스트 중 머리글 ‘미지원’을 기대하던 항목만 새 동작으로 교체한다.
- [x] 전후 모든 물리적 쪽에서 고정된 기대표와 PDF 글자·좌표를 대조한다. 9→10 이동, 표지 숨김, 구간 전환과 쪽 번호 연속성, 시험·확정 미리보기·다운로드의 동일 바이트, 양식 재개방, 원본 보존과 기존 지면을 검사한다. 별도 설정 표본에서는 기본 크기 한 곳을 바꾸고 화면·API·PDF 실제 글자 크기를 대조한다.
- [x] Nash는 통과한 이미지로 로컬 편집기를 열 수 있게 하고 실제 서비스 URL과 이미지 ID·빈 마운트 목록을 재확인한다. `proofs/running-heads/`에 비교용 원고·양식·전후 PDF를, `docs/superpowers/running-heads-acceptance.md`에 쪽별 대조와 실행 명령·결과를 남긴다. `SPEC.md`에는 현행 출력 규칙과 참고 문구 저장 범위를 반영한다.

[최종 Docker 검증 및 PDF](../running-heads-acceptance.md)
