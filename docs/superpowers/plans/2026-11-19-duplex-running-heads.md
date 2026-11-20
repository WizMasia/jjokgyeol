# 양면 머리글·바닥글 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development`. Nash가 통합하고 각 담당자가 실패 테스트부터 구현한다. Gibbs가 최종 배포본을 독립 검증한다.

**Goal:** 참고 문서의 첫 면·홀수 면·짝수 면 서식을 골라 적용하고, 원고가 늘면 쪽 번호가 새 면의 바깥쪽으로 이동하게 한다.

**Architecture:** 기존 구간 양식에 선택적인 면 구분을 더한다. 판독기는 원본의 근거와 추천 적용 면을 전달하고, 편집기는 저자가 확정한 면을 저장한다. LuaLaTeX가 물리적 쪽을 확정하는 시점에 원고 첫 줄의 제목 경로와 해당 쪽의 홀짝으로 희소 속성을 합친다.

**Tech Stack:** Python, FastAPI, JavaScript, LuaLaTeX, pytest, Playwright, PDFMiner, Docker, 로컬 LM Studio.

**Spec:** [승인된 양면 머리글·바닥글 설계](../specs/2026-11-19-duplex-running-heads-design.md)

## Global Constraints

- 표지 포함 물리적 1쪽부터 연속해서 센다. 왼쪽 제본의 바깥쪽은 홀수 오른쪽, 짝수 왼쪽이다. 한 쪽의 첫 원고 줄이 그 쪽의 구간을 정한다.
- 각 속성은 문서 → 상위 제목 → 하위 제목 순으로, 각 범위 안에서는 공통 → 실제 홀짝 → 해당 첫 쪽 순으로 병합한다. 명시적 빈 문자열과 숨김도 값이다. 길이는 정수 sp, 문구와 번호는 기존 식 문법을 쓴다.
- 직접 편집·참고 적용·저장·열기에는 같은 양식 검증기를 쓴다. 기존 양식의 JSON과 지면, 마크다운 원고, 참고 원본을 보존한다.
- 담당별 RED를 확인한 뒤 구현하고 같은 검사와 기존 회귀를 GREEN으로 만든다. Git 메타데이터가 없는 현재 작업공간에서는 Nash가 변경 파일과 검증 결과로 인수한다.

## Review Focus

1. 중간 제목·동일한 속성의 형제 제목·부모와 자식의 첫 면이 같은 경우. 작업 1·3이 실제 경로별 1회 적용과 이후 홀짝 복귀를 검사한다.
2. 각주 전용 쪽의 홀짝이 바뀌거나 원고가 비어 있는 경우. 작업 3이 이전 마지막 원고 구간, 첫 면 소비 여부, 빈 문서의 물리적 1쪽을 검사한다.
3. DOCX의 비활성 파트·이전 구역에서 상속한 파트·실제 빈 파트·그림만 있는 파트. 작업 2가 숨김 제안과 수동 확인을 구별한다.
4. 한 쪽 재판독·제목 다시 연결에서 저자가 정한 적용 면이 사라지거나 홀짝이 서로 충돌하는 경우. 작업 4가 편집 초안부터 양식 재개방까지 검사한다.
5. 9→10쪽에서 바뀐 번호가 가운데에도 남거나 다른 칸과 겹치는 경우. 작업 3·5가 실제 글리프 수·좌표와 오류 시 직전 PDF를 대조한다.

## 병렬 작업 계약

| 경계 | 담당 파일과 인터페이스 |
|---|---|
| 양식과 제목 경로 · Bernoulli | `app/style.py`, `app/running_heads.py`, `app/reference_rules.py`. `SectionRule`에 기본값이 있는 `face`를 추가해 기존 생성자와 호출을 유지한다. `header/footer` 규칙의 `face` 값은 `default/first/odd/even`. 공통은 JSON에서 생략한다. 같은 경로·역할·면·속성만 중복이다. |
| 참고 판독 · Nietzsche, Dalton | `app/reference_office.py`, `app/reference_media.py`. 기존 `variant`와 `source_group`은 원본 정보다. 머리글·바닥글 후보의 `suggested_face`는 네 면 중 하나 또는 `null`이다. 후자는 사용자가 직접 선택한다. 비활성·추정 근거는 기존 `basis/evidence`에 표시한다. |
| 쪽 선택 · Galileo | `app/typeset.py`, `app/tex/layout.lua`, `app/tex/running_heads.lua`. `app/reference_rules.py`의 `running_contexts(blocks, style, *, fenced_blocks=())`에서 **규칙이 있는 경로별 영역·면·속성**, **블록별 해당 경로 ID의 순서열**을 받는다. 문서 공통 경로는 항상 먼저 온다. 블록마다 속성 사본을 보내지 않고 경로와 규칙을 한 번씩 정의한다. 현재 원고 줄의 식별자를 넘기는 `Document.render_layout(..., running_ids=...)`를 재사용한다. |
| 편집기 · Darwin | `app/static/index.html`, `app/static/reference.js`, 필요 시 `app/static/editor.css`. 직접 편집과 후보 선택에서 동일한 `(path, role, face)`를 사용한다. 저장 JSON에는 추천 근거 대신 확정한 규칙만 들어간다. |
| 인수와 통합 · Gibbs, Nash | Gibbs는 `tests/test_duplex_acceptance.py`, `proofs/duplex-running-heads/`를 맡는다. Nash는 담당별 동결·검토, 패키징과 Docker, `README.md`, `SPEC.md`, `docs/superpowers/duplex-running-heads-acceptance.md`를 맡는다. 기존 단면 증거 파일은 독립 기준으로 쓴다. |

## 1. 양식 저장과 경로 식별

- [x] `tests/test_duplex_rules.py`에 홀·짝 같은 칸의 공존, 같은 면 중복 오류, 비지원 역할·면 오류, `first`에서 `visible=False`, 빈칸과 생략, 저장·열기 왕복을 명시한다. `assert old == export(open_style(old))`로 현재 단면 표본을 검사한다. `pytest -q tests/test_duplex_rules.py`에서 새 면을 거부하거나 구분하지 못하는 RED를 확인한다.
- [x] `SectionRule.to_fields()`, `_section_rules()`와 기존 속성 검증 경로를 확장한다. `running_contexts`의 반환 형태는 `(scopes, chains)`로 고정한다. `scopes`는 1부터 부르는 경로 목록으로 각 항목은 `{path, header, footer}`, 영역별 사전은 면별 희소 속성이다. `chains`는 각 블록에 적용할 범위 ID 목록이다. 문서 공통은 ID 1이며 블록이 없어도 존재한다. 원고에 유일하게 대응하는 제목만 등록한다. 해당 경로와 면에 적용 가능한 규칙이 없는 영역은 숨김으로 둔다. 적용할 규칙이 있으면 설치 기본값에서 시작해 각 범위와 면에서 명시한 속성만 차례로 덮어쓴다. 기존 `running_values()` 소비자의 공통값 조회 결과를 유지한다.
- [x] 같은 값을 지정한 형제 두 경로가 서로 다른 ID를 얻고, 부모·자식 순서가 보존되는지 검사한다. `pytest -q tests/test_duplex_rules.py tests/test_running_head_rules.py tests/test_reference_rules.py tests/test_style_file.py` 통과 후 작업 3·4에 계약을 인계한다.

## 2. 원본의 면과 추천 적용 면 판독

- [x] Office 담당은 `tests/test_duplex_office.py`에 DOCX의 홀짝 사용 설정, 구역별 첫 면 설정(명시적 꺼짐 포함), 서로 다른 관계 ID, 이전 구역 상속, 빈 표지와 그림만 있는 표지를 넣는다. HWPX 바깥 번호에 대해 `{("odd","right"),("even","left")}`를 기대한다. 매체 담당은 `tests/test_duplex_media.py`에서 교대로 반복되는 PDF 문구, 첫 쪽 근거, 스캔 한 장의 미확정 면과 숫자 선택지를 검사한다. 각 파일을 단독 실행해 `suggested_face` 기대가 실패하는지 확인한다.
- [x] Office는 원본의 활성 설정과 구역 관계를 먼저 해석한 뒤 값을 제안한다. DOCX 기본 파트는 홀짝 활성 시 홀수, 비활성 시 공통이다. 활성 첫 면·짝수 면에 내용이 실제로 없으면 그 면의 `visible=false`를 제안한다. 비활성 파트와 판독에 실패한 파트는 근거를 남긴다. HWPX 양면 위치는 기존 판독기에서 만든 두 후보를 각 면에 연결한다.
- [x] 매체는 기존 반복 좌표와 숫자의 진행 근거에서 추천 면을 도출한다. 단독 스캔의 `suggested_face`는 `null`이며 숫자의 원문·자동 번호 선택지를 유지한다. 같은 쪽 재분석에서 출처와 선택을 다시 연결할 수 있게 한다.
- [x] `pytest -q tests/test_duplex_office.py tests/test_duplex_media.py tests/test_running_heads_office.py tests/test_running_heads_media.py`가 통과해야 한다. 기존 파일 처리 한도와 부분 실패 동작도 유지한다.

## 3. 실제 쪽에서 첫 면·홀짝 선택

- [x] `tests/test_duplex_typeset.py`에 실제 PDF로 제목이 쪽 첫 줄·중간에 오는 경우, 한 쪽 중간에서 끝나는 짧은 구간, 다른 제목 아래에서 재개하는 상위 구간, 같은 값을 가진 형제, 동시에 처음 적용되는 부모·자식을 넣는다. 최초 한 쪽만 첫 면이 되는지 검사한다. `pytest -q tests/test_duplex_typeset.py`로 새 선택 규칙의 RED를 확인한다.
- [x] `_running_sections()`에서 경로별 희소 속성의 표현식과 글꼴을 준비하고 경로 체인에 줄 식별자를 연결한다. `running.select(box)`에서 실제 출력 쪽 수를 세고, 그 쪽의 첫 원고 줄 체인과 이미 사용한 첫 면 경로 집합으로 속성을 합성한다. 같은 쪽에서 적용한 첫 면 경로들을 함께 기록한다. 각주만 있는 쪽은 마지막 원고 줄의 체인을 쓰되 첫 면을 소비하지 않는다. 재조판 시 기록을 새로 시작한다.
- [x] 빈 원고 1쪽, 마지막 원고 구간과 각주 전용 쪽, 두 자리 번호에서 한 면만 충돌하는 예를 검사한다. 기존 여백·글자 크기·칸 충돌 및 오류 응답 경로를 사용한다. `pytest -q tests/test_duplex_typeset.py tests/test_running_heads_typeset.py tests/test_reference_typeset.py`에서 단면 PDF와 펜스 뒤 제목의 좌표 회귀까지 통과한다.

## 4. 직접 편집과 참고 후보 적용

- [x] `tests/test_duplex_editor.py`에서 편집 대상으로 문서·제목, 머리글·바닥글, 공통·첫 쪽·홀수 쪽·짝수 쪽을 선택한다. 두 면의 같은 칸에 다른 문구를 넣고 시험 요청의 규칙이 둘 다 존재하는지 검사한다. `pytest -q tests/test_duplex_editor.py`로 면 선택과 초안 구별이 없는 RED를 확인한다.
- [x] 기존 영역 편집기에 면 선택을 추가한다. 후보는 출처 면과 적용 면을 나란히 표시하고, `suggested_face=null`이면 저자가 면을 지정한 뒤 시험한다. 스캔의 숫자 선택은 별도 입력이다. 첫 면 편집 중 홀짝에 따라 상속값이 다른 칸은 양쪽 경우를 표시한다. 참고 파일에서 선택한 숨김은 `visible=false`로 보존하고, 기존의 자동 ‘표시’ 제안이 이를 뒤집지 않게 한다.
- [x] `draftStyle`, 누락 제목 다시 연결, 오류 칸 포커스, 부분 재분석, 취소에 면 정보를 연결한다. 한 번에 고른 홀·짝 후보가 공존하고, 동일 면·속성의 충돌은 사람이 결정하며, 표지 바닥글 숨김 뒤 2쪽에는 번호가 나오는지 검사한다. `pytest -q tests/test_duplex_editor.py tests/test_running_heads_editor.py tests/test_reference_editor.py`가 통과해야 한다.

## 5. 독립 PDF 인수와 Docker 배포

- [x] 구현과 병행해 기존 `proofs/running-heads/before.md`, `after.md`와 기준 PDF를 고정한다. 현재 서비스에서 가운데 번호를 확인하고, 바깥 번호 인수를 먼저 실행해 실패를 기록한다. 후속 검사에 사용할 양식에는 홀수 오른쪽과 짝수 왼쪽에 `{현재쪽}` 하나씩을 두고 머리글에는 면마다 다른 문구를 넣는다.
- [x] `tests/test_duplex_acceptance.py`에서 클릭으로 원고와 참고 파일을 열고, 문구 수정 → 시험 → 적용 → 다운로드 → 양식 재개방을 실행한다. 11쪽 원고의 추적 문장 옆 번호 ‘9’ 오른쪽 끝은 **323.150 PDF pt**, 12쪽 원고의 ‘10’ 왼쪽 끝은 **39.685 PDF pt**에 대해 기존 PDF 좌표 검사 허용 오차 0.04pt 안이어야 한다. 번호에는 부호를 붙이지 않고 숫자의 첫·마지막 글리프를 PDF에서 직접 읽는다. 전후 모든 쪽에서 반대편·가운데 중복 번호, 머리글, 본문·각주 글리프 좌표를 점검한다.
- [x] DOCX·HWPX·텍스트 PDF와 실제 LM Studio가 읽은 스캔을 시험한다. 추천 면 수락·재지정, 실제 빈 표지의 숨김, 제목 첫 면과 홀짝 상속, 한쪽에서만 발생하는 충돌의 쪽·영역 오류와 정상 PDF 보존, 미리보기와 다운로드의 SHA-256 일치를 검사한다.
- [x] Nash가 `docker build --platform linux/arm64 --target test -t jjokgyeol:duplex-test .` 및 `docker run --rm --network none --shm-size=512m jjokgyeol:duplex-test pytest -q`로 전체 회귀를 확인한다. `docker build --platform linux/arm64 --target local -t jjokgyeol:duplex .`로 배포 이미지를 만들고 소스 마운트 없이 실행한다. 호스트에서 이미지 ID와 로컬 URL을 지정해 `pytest -q tests/test_duplex_acceptance.py tests/test_running_heads_acceptance.py tests/test_reference_acceptance.py` 및 기존 호스트 전용 검사를 실행한다.
- [x] 최종 컨테이너에서 내려받은 전후 PDF·양식, 원고 링크, 쪽별 측정값, 실제 이미지 ID와 테스트 결과를 검증 기록에 남긴다. 편집기 주소와 전후 PDF를 열어 확인한 뒤 인계한다.

[최종 Docker 검증 기록](../duplex-running-heads-acceptance.md)
