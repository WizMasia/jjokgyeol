# 문단 여백 Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development, TDD and verification.

**Goal** 웹·네이티브에서 문단의 위·아래 여백을 수치 또는 AI로 지정한다.

**Architecture** Style과 원고 주석이 요청값을 소유한다. 공통 조판 경로가 경계마다 값을 하나로 합치고, 규칙 PDF와 검증된 모델 PDF 양쪽에서 실제 적용 결과를 받는다.

**Tech Stack** Python, LuaTeX, JavaScript, Tauri, Docker.

**Spec** [문단 위·아래 여백과 디시전 모델](../specs/2026-11-23-paragraph-spacing-design.md)

## Global Constraints

공용 입력 space_before_pt/space_after_pt는 0~72 또는 "auto", 저장 및 내부 필드는 space_before_sp/space_after_sp이다. 생략은 상속이며 정수 0도 보존한다. 적용 위치는 두 블록의 경계이고 쪽 가장자리에서는 0이다. 기존 원고는 기존 PDF를 재현한다.

## Review Focus

혼합 줄 끝과 조 제목 번호, 다중 문단 목록, 틀 제목 및 긴 표, 수치 0과 자동의 충돌, 늦은 모델 응답 및 교정본 전환을 확인한다.

### 1. 양식·원고 계약

- [ ] tests/test_spacing_style.py로 입력, 저장, JSON 왕복 실패를 확인 후 app/style.py 및 app/config/publication.json의 역할별·목록별 정수 sp 변환과 설정 공개를 구현하고 통과시킨다.
- [ ] tests/test_spacing_manuscript.py로 양쪽 독립 지정 및 해제, 정렬과 함께 사용, 잘못된 값, 코드·목록 제외, 번호 고정, 줄바꿈 보존을 먼저 재현한다. app/document.py, app/paragraph_edit.py의 기존 align 작업에 space_before_pt/space_after_pt를 추가한다. 변경하지 않는 과거 호출은 동일하게 동작해야 한다.
- [ ] 개별 값은 Block.space_before_sp/space_after_sp, 모델용·저장용 값은 정수 또는 "auto", 조회 응답은 pt이다. 독립 단위 테스트 통과 후 커밋한다.

### 2. 경계 계산과 실제 PDF

- [ ] tests/test_spacing_engine.py에서 0/6/12pt, 제목 틀, 쪽 나눔과 표, 섞인 목록의 PDF 좌표를 비교하는 실패를 확인한다.
- [ ] app/typeset.py 및 app/tex에 각 역할과 개별 요청을 합쳐 경계마다 하나의 값만 적용한다. typeset(..., spacing_choices=None)에서 경계 키는 뒤 블록의 렌더 인덱스 문자열(1부터), 선택값은 정수 sp이다. 각 렌더 폴더 spacing.json에는 실제 경계의 목록을 쓴다. 레코드 필드는 id, before_line, after_line(앞·뒤 원고의 0기준 줄), gap_sp, source(manual/style/ai/rule/default), auto, page, remaining_sp, lines, left_kind, right_kind, valid이다. 쪽을 넘어가면 gap_sp=0이다.
- [ ] 규칙·모델 실측 테스트와 기존 기본값 PDF를 확인하고 커밋한다.

### 3. 모델 자동 선택

- [ ] tests/test_spacing_decision.py에서 수치 고정, 모델 선택·실패·기한·줄바꿈 복구·문서 전체 호출 한도에 대한 실패 기대값을 작성한다.
- [ ] app/ai에서 유효한 자동 경계만 기존 인증으로 구조 정보와 후보 ID를 보내고, 줄바꿈 검증 후 제한된 여백 재조판 한 번을 수행한다. 기존 구현·시험 모형 호출자와 호환되어야 한다. 채택한 폴더의 spacing.json을 사용해 결과를 제공한다. AI 미지원 시 규칙 폴백을 보존한다.
- [ ] 기존 제공자와 조판 모형 회귀 검사 및 새 테스트를 실행하고 커밋한다.

### 4. 공유 편집기 및 전달

- [ ] 브라우저에서 역할·목록·개별 위/아래 입력과 해제·저장 왕복·이전 교정본의 실제 여백 표시를 먼저 테스트한다.
- [ ] app/static, desktop/gui/frontend의 기존 양식·목록·문단 컨트롤 및 코덱을 확장한다. 개별 작업은 기존 paragraphAlign 호출로 양쪽에서 전달한다. 결과 내역은 선택한 PDF/원고의 세션에만 연결한다.
- [ ] 웹·네이티브 결과 메타데이터의 전달을 기존 PDF 응답 계약과 함께 지원한다. 오래된 응답·PDF 재열기·교정본 전환 시 값이 섞이지 않는지 실제 진입점에서 확인하고 커밋한다.

### 5. 배포

- [ ] 규칙/모델 PDF 좌표와 교환 문서를 검증하고 회귀 검사, 독립 검토를 수행한다.
- [ ] main 및 feature/native-desktop에 통합해 origin으로 일반 푸시한다. 기존 3개 플랫폼의 빌드·설치 검사를 통과한 다음 다음 preview 순번의 GitHub 시험판과 SHA256SUMS를 공개하고 릴리즈와 파일을 조회한다.
