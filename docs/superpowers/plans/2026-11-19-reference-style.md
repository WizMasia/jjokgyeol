# 참고 문서 서식 가져오기 구현 계획

> **For agentic workers:** Use superpowers:subagent-driven-development. Nash가 통합 검토를 맡고 독립 인수 담당자가 브라우저와 PDF를 대조한다.

**목표** — DOCX·HWPX·PDF·스캔에서 추출한 서식 중 필요한 속성을 선택해 현재 원고의 원하는 구간에 적용한다. [승인된 설계](../specs/2026-11-19-reference-style-design.md)

**구성** — 형식별 판독기가 근거를 공통 자료 구조로 전달한다. 편집기는 시험 양식을 기존 렌더 API로 출력하고, 확정된 값은 기존 양식 JSON에 저장한다. Python·FastAPI·정적 JavaScript·LuaLaTeX를 유지하고 PDF 판독·래스터화와 운영자 지정 비전 모델을 연결한다.

## 공통 계약

- 참고 파일과 원고는 별도 입력이다. 참고 파일의 본문이나 분석 이미지는 원고나 저장 양식으로 복사되지 않는다. 입력·압축 해제량·쪽수·픽셀·처리 시간을 제한한다.
- `POST /api/reference/analyze`는 파일 바이트를 받아 `{units, warnings}`를 반환한다. 순서대로 배치한 각 단위는 `{location, section, excerpt, candidates}`이며 대표 이미지가 있으면 `preview`를 포함한다. `section`은 최초 구간 제안이며 사용자가 연속 범위의 시작과 끝을 바꿀 수 있다.
- 후보 필드는 `{role, key, value, basis, evidence}`다. 역할은 `document/heading/body/list`, 판독 상태는 `read/estimated/confirm/unsupported`를 쓴다. 길이는 편집 단위(mm·pt) 또는 스캔 폭 대비 비율로 단위를 명시한다. 근거는 해당 쪽·문단·영역을 가리킨다. 설치되지 않은 글꼴에는 사람이 지원 글꼴을 선택한다.
- 양식의 선택 필드 `section_rules`는 `{path, role, values}`의 배열이다. `path`는 원고 제목 문자열의 경로이고 빈 배열은 문서 전체의 해당 역할을 뜻한다. `heading`은 지정한 제목, `body/list`는 해당 구간의 문단/목록에 적용한다. 더 구체적인 하위 경로가 같은 속성을 지정하면 우선한다. 같은 경로·역할의 속성 충돌은 편집기에서 해결한다. 실제 렌더에는 동일 검증기를 통과한 정수 길이·비율을 쓴다. 기존 JSON은 종전대로 왕복한다.

## 분담과 인수 순서

담당자는 단계마다 명시한 실패 사례를 먼저 실행한 후 구현하고 같은 검사를 다시 실행한다.

- [x] **1. 양식과 원고 대상.** `app/style.py`와 구간 규칙 모듈에서 글자 크기·행간·글꼴·색, 들여쓰기, 목록 단계 변경을 검증·저장한다. `POST /api/reference/targets`로 조판과 같은 Markdown 파서에서 얻은 고유·중복 제목 경로를 전달한다. `tests/test_reference_rules.py`에서 부모·자식 우선순위, 누락·중복 대상 재연결, 다른 문서로의 양식 왕복, 금지된 속성과 잘못된 값을 확인한다.
- [x] **2. 실제 지면.** `app/typeset.py`와 `app/tex/`에서 블록별 확정 서식을 적용한다. 목록 표지와 본문의 폭은 실제 적용 글꼴로 측정한다. `tests/test_reference_typeset.py`에서 지정 본문만 11→14pt, 표제 하나만 24pt, 한 구간의 목록 내어쓰기·색 변경을 PDF 글리프와 좌표로 재검사한다.
- [x] **3. DOCX·HWPX.** `app/reference_office.py`에서 컨테이너 구조·구역 순서·문단/문자 스타일과 직접 지정값·번호 정의를 읽는다. `tests/test_reference_office.py`에서 대표 문단, 서로 다른 run, 변경한 구간 경계, 부정확한 쪽 번호 방지, 손상되거나 한도를 넘는 문서를 검사한다.
- [x] **4. PDF·스캔·분석 API.** PDF는 쪽별 텍스트와 지면 정보를 읽고 이미지 쪽과 PNG/JPEG는 운영자가 연결한 비전 모델로 분석한다. `app/reference_media.py`, `app/server.py`, 운영 설정 및 Docker 패키징이 담당 범위다. 텍스트·스캔 혼합, 실물 치수 미상과 수동 환산, 실패 후 재시도·부분 성공을 `tests/test_reference_analysis.py`에서 확인한다. 비전 모델에는 참고 지면만 전달한다.
- [x] **5. 편집기.** `app/static/`에서 참고 파일 열기, 근거 확인, 구간 경계 변경, 속성·대상 선택, 직접 수정, 시험 조판·적용·취소·재연결을 제공한다. `tests/test_reference_editor.py`로 같은 속성 충돌, 잘못된 입력, 비동기 응답 중 원고 변경, 취소 후 기존 PDF, 확정 후 양식 저장·열기를 확인한다.
- [x] **6. 독립 인수와 배포.** 직접 만든 참고 파일과 다른 내용의 대상 원고로 DOCX·HWPX·텍스트 PDF·스캔·혼합 PDF를 실제 브라우저에서 시험한다. 로컬 LM Studio에 실제 이미지를 보내고 반환된 후보를 검토한다. 앞에 문단을 추가한 뒤에도 선택한 구간에만 값이 적용되는지, 참고 문구가 섞이지 않는지, 확정 미리보기와 다운로드 파일이 바이트 단위로 같은지 확인한다. 최종 Docker 이미지로 기존 지면 회귀를 실행하고 예시 원고·양식·PDF와 인수 기록을 남긴다.

최종 Docker 배포본에서 오프라인 회귀 429건, 호스트 인수 22건이 통과했다. [측정값과 재현 방법](../reference-style-acceptance.md)
