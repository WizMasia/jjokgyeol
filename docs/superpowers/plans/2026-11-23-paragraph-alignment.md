# 본문·제목 단계별 서식과 문단 정렬 Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development and track completed checks here.

**Goal** 웹·Mac에서 일반 본문과 제목 1~6의 서식 및 특정 문단 정렬을 실제 PDF에 반영한다.

**Architecture** 기존 Style과 Markdown 블록에서 조판까지 하나의 값을 전달한다. 웹 API와 Mac 로컬 명령은 같은 Python 원고 편집 함수를 호출한다.

**Tech Stack** markdown-it-py, LuaTeX, Python, JavaScript, Tauri/Rust.

**Spec** [제목줄 서식과 개별 문단 정렬](../specs/2026-11-23-paragraph-alignment-design.md)

## Global Constraints

0단계는 표시 없는 일반 본문이고 크기는 기존 body_pt/body_sp를 공유한다. 기존 양식과 원고는 같은 지면을 유지한다. 새 의존성을 추가하지 않는다.

## Review Focus

가짜 주석이 든 코드, 구분 빈 줄 없는 주석 해제, 맞쪽 여백과 틀 제목, 비동기 선택 변경, 소수 크기 및 저장 왕복을 각각 검사한다.

### 1. 원고 구문 및 범위

- [ ] tests/test_paragraph_alignment.py에서 코드·표·날짜·Setext·중복 주석·혼합 개행에 대한 실패를 확인한다.
- [ ] app/document.py에서 Block.align을 선택 값으로 제공한다. app/paragraph_edit.py의 edit_alignment(payload)에서 source, first_line, last_line과 선택 속성 align을 검증한다. align 생략은 조회, null은 해제, 문자열은 left/center/right. 응답은 {source, align, supported, message}이고 align은 직접 지정한 값 또는 null이다. 잘못된 요청은 InputError, 유효한 선택이지만 대상이 없으면 supported=false.
- [ ] 신규 테스트와 기존 조 제목·표·원고 테스트를 실행하고 확인한다.

### 2. 역할 양식과 조판

- [ ] tests/test_alignment_engine.py에서 규칙·모델의 실제 PDF 위치 및 단계별 크기, 우선순위, 기존 지면을 검증하는 실패 테스트를 작성한다.
- [ ] app/style.py, app/typeset.py, app/tex에서 text_styles.body/heading1~6.align과 Block.align을 적용한다. 0단계 정렬은 보통 문단에만 적용한다. 기존 font_sp/body_sp 및 줄 번호를 보존한다. 명시적 정렬 문단은 모델 후보에서 제외한다.
- [ ] 기본과 명시값을 왕복하고 실제 PDF 글자 좌표·크기를 비교한다.

### 3. 웹·Mac 편집 화면

- [ ] 원고 한 문단 편집과 설정창의 단계 전환·역할별 크기 변경, 문서 왕복을 브라우저 테스트로 재현한다.
- [ ] app/static/{index.html,editor.js,typography.js} 및 필요 시 공용 스타일 코덱과 네이티브 설정 창을 연결한다. /api/paragraph/align 또는 desktop.paragraphAlign(payload) 호출은 같은 계약을 사용한다. 선택 범위, 실행 취소, 원고 수정 상태, 늦은 응답을 검증한다.
- [ ] 기존 편집기 및 오프라인 설정창 테스트와 신규 테스트를 실행한다.

### 4. 웹·네이티브 전달 및 시험판

- [ ] 웹 API와 로컬 워커의 조회/변경/잘못된 입력 테스트가 먼저 실패하는지 확인한다.
- [ ] app/server.py와 desktop/worker, desktop/src, desktop/gui/src 및 desktop/gui/frontend/desktop.js에 같은 원고 편집 작업을 연결한다. 네이티브 명령 이름은 paragraph_align, JS 공개 함수는 paragraphAlign이다.
- [ ] HTTP 및 오프라인 호출, 저장·재열기, 규칙·모델 PDF, 기존 기준 PDF를 확인한다. 통과한 변경을 main 및 네이티브 브랜치에 통합하고 로컬 웹·Mac 시험판을 제작한다.
