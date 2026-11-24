# 하단 문단 서식 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal** 웹과 데스크톱에서 문단 서식을 하단 패널로 조정하고, AI 간격 선택을 검증된 교정본에 적용한 뒤 항목별로 고정한다.

**Architecture** 기존 Style 및 문단 편집 경로에서 요청값을 검증한다. LuaLaTeX의 조판과 재현 결과에서 적용값을 얻어 현재 교정본과 함께 전달한다. 웹과 데스크톱은 같은 편집기 구성과 명령을 사용한다.

**Tech Stack** Python, LuaLaTeX, 브라우저 JavaScript와 CSS, Tauri, pytest, Playwright, Cargo.

**Spec** [하단 문단 서식과 AI 간격 조판](../specs/2026-11-24-docked-paragraph-controls-design.md)

## Global Constraints

- 기존 원고, 양식 JSON, 쪽결 문서와 그 지면을 재현한다.
- 양식 수치는 Style에서 검증해 정수 sp로 저장한다. 명시적 0, 생략, 자동은 서로 구별한다.
- 하단 패널을 접거나 펼칠 때 넓은 화면의 원고와 PDF 상단 위치와 너비를 유지한다.
- 모델에는 수치와 구조, 허용 후보 ID만 전달한다. 현재 원고와 양식으로 검증한 결과만 채택하거나 고정한다.
- 웹과 데스크톱의 진입점과 기존 오류·이전 교정본 동작을 검증한다.

## Review Focus

- 혼합 개행과 BOM이 있는 원고에서 줄 간격을 편집하거나 강조한 뒤 실행 취소하면 원본 바이트를 복원한다. 작업 2와 4.
- 설치 기본값을 바꿔도 이전 저장 양식의 생략과 명시적 0이 각 의미를 보존한다. 작업 1.
- 틀 제목, 높이가 다른 글줄 및 페이지를 넘는 문단의 검증 가능한 측정값만 제공한다. 작업 2와 3.
- 타임아웃 후 전달되는 직전 교정본은 같은 조판의 측정값을 함께 사용한다. 작업 3과 4.
- PDF 전환이나 파일 교체 중 도착한 응답으로 다른 문단을 고정하지 않는다. 작업 4.

## 작업 1. 문서 기본 문단 간격

**Files** app/style.py, app/typeset.py, app/tex/layout.lua, app/ai/spacing.py, tests/test_spacing_style.py, tests/test_spacing_engine.py.

**Interfaces** 편집기 양식의 선택 필드 paragraph_gap_pt, 정수 양식과 Style의 paragraph_gap_sp. 생략은 None으로 보존한다. 설치값과 범위는 기존 publication.PARAGRAPH_SPACING을 사용한다.

- [x] **RED.** tests/test_spacing_style.py에 입력 0의 저장값이 0이고, 생략한 저장값은 키가 없으며, 6.25pt가 409600sp로 왕복하는 테스트를 추가한다. -1, 73, auto는 필드 오류로 확인한다. tests/test_spacing_engine.py에 명시적 0pt가 기존 4pt와 비교해 기준선 사이의 거리를 정확히 4 TeX pt 줄이는 PDF 테스트를 추가한다.
- [x] **실패 확인.** pytest -q tests/test_spacing_style.py tests/test_spacing_engine.py에서 새 케이스가 미지원 필드 때문에 실패하는지 확인한다.
- [x] **구현.** Style의 편집 및 저장 변환을 확장한다. typeset에서 해석된 값을 Lua의 parskip, 경계 기본값과 리플레이 검증에 사용한다. 생략한 경우 기존 4pt의 PDF와 후보 ID를 재현한다.
- [x] **GREEN.** 같은 명령을 다시 실행하고 tests/test_style_file.py와 tests/test_spacing_decision.py를 확인한다.
- [x] 작업 단위로 커밋한다.

## 작업 2. 개별 문단 줄 간격과 조판 측정

**Files** app/style.py, app/document.py, app/paragraph_edit.py, app/typeset.py, app/tex/layout.lua, app/tex/paragraph_spacing.lua, tests/test_spacing_manuscript.py, tests/test_spacing_engine.py, tests/test_paragraph_transport.py.

**Interfaces** 기존 paragraph align 요청과 응답에 leading_pt를 선택적으로 추가한다. 숫자는 현재 줄 간격의 유효 범위를 따르고, auto는 조판 시 규칙값 또는 AI 후보를 뜻하며, null은 행 제거를 뜻한다. 파서의 Block·ParagraphRange 필드는 leading_sp로 둔다. 원고의 표기는 쪽결 주석 leading_sp=1179648 또는 leading_sp=auto를 사용한다. 기존 간격 조회를 요청하면 leading_pt도 돌려준다.

- [x] **RED.** 선행 BOM과 CRLF/CR이 섞인 본문에서 18pt를 지정하면 선택 위치에만 leading_sp=1179648이 추가되고, auto 및 null로 왕복하며 이하 원문 마커와 개행이 보존되는 테스트를 작성한다. 기존 정렬 전용 응답과 범위를 유지한다. 본문과 제목의 수동 줄 간격 및 이전 원고의 실제 PDF를 대조한다. 줄 높이가 섞인 문단과 줄이 한 개뿐인 문단도 넣는다.
- [x] **실패 확인.** pytest -q tests/test_spacing_manuscript.py tests/test_spacing_engine.py tests/test_paragraph_transport.py에서 새 필드와 실측에 대한 실패를 확인한다.
- [x] **구현.** 기존 문단 편집과 Style의 수치 변환을 재사용한다. 개별 수치를 본문·제목의 Lua 줄 간격에 적용한다. 규칙값, 후보 선택 대상, 지면 안의 실제 기준선 위치를 문단 ID와 원고 줄에 연결한다. typeset(..., leading_choices=None)에서 문단 ID 문자열을 정수 sp에 매핑한다. 줄 간격 요청이 있거나 간격 측정이 요청된 조판에서 leading.json에 id, line, leading_sp, source, auto, font_sp, lines, remaining_sp, valid를 내보낸다. 단일 간격을 확인할 수 없는 보고 행에는 leading_sp를 null로 둔다. 일반 문단과 제목 틀의 내부 글줄을 비교하며 페이지 경계를 넘는 기준선 간격은 제외한다.
- [x] **GREEN.** 같은 명령과 tests/test_paragraph_alignment.py, tests/test_alignment_engine.py를 실행한다.
- [x] 작업 단위로 커밋한다.

## 작업 3. AI 줄 간격 후보와 교정본 전달

**Files** app/ai/decision.py, app/ai/model.py, app/ai/providers.py, app/config/decision.json, app/jobs.py, desktop/worker/worker.py, desktop/worker/tests/test_worker.py, desktop/src/decision.rs, tests/test_spacing_decision.py, tests/test_spacing_transport.py, desktop/tests/runner.rs.

**Interfaces** 줄 간격 지원 모델에 predict_leading(options, *, timeout)을 추가한다. 검증 후 기존 간격 응답 객체에 선택 필드 leading을 붙이고, 그 안의 rule/model 목록에는 해당 PDF의 line, leading_sp, source를 담는다. 기존 spacing.rule과 spacing.model 목록은 유지한다. 웹 multipart와 데스크톱 바이너리 헤더 모두 기존 간격 요청 플래그를 사용한다.

- [x] **RED.** 두 줄 이상으로 인쇄되는 자동 문단에서 고정 시험 모델이 선택한 합법 후보의 기준선 거리와 leading_sp가 일치하는지 검사한다. 다른 후보 ID, 모델 미지원, 타임아웃, 모델 결과로 쪽 경계가 달라지는 지면의 측정값, 이전 교정본을 돌려주는 데스크톱 러너의 메타데이터도 검사한다. 웹 plain/paired 및 Rust 바이너리 헤더의 줄 간격 수신과 이전 클라이언트 응답 형태를 포함한다.
- [x] **실패 확인.** pytest -q tests/test_spacing_decision.py tests/test_spacing_transport.py와 cargo test --locked --manifest-path desktop/Cargo.toml에서 모델 호출 또는 전달 누락으로 새 케이스가 실패하는지 확인한다.
- [x] **구현.** 규칙 줄 간격 및 그 95%, 105%에 해당하는 안전 후보를 공유 출판 정책에서 읽고 중복을 제거한다. 전체 간격 요청에 기존 문서별 호출 한도와 기한을 적용한다. 기존 측정과 비교해 확정된 줄바꿈과 수평 배치를 보존한 경우에만 새 지면을 채택한다. 선택값과 실제 비교 가능한 기준선 거리가 모두 일치해야 적용값을 공개한다. 검증 실패나 기한 초과에는 마지막으로 확정한 PDF와 그 측정 파일을 함께 반환한다. 웹과 데스크톱은 검증된 간격·줄 간격 측정만 전달하며 이전 설정 파일도 읽는다.
- [x] **GREEN.** tests/test_spacing_decision.py, tests/test_spacing_transport.py, tests/test_provider_render.py, desktop/worker/tests/test_worker.py와 cargo test --locked --manifest-path desktop/Cargo.toml을 실행한다.
- [x] 작업 단위로 커밋한다.

## 작업 4. 하단 패널, 편집 메뉴와 진행 상태

**Files** app/static/index.html, app/static/editor.css, app/static/editor.js, app/static/paragraph-spacing.js, app/static/typography.js, desktop/gui/frontend/desktop.css, tests/test_spacing_editor.py, tests/test_paragraph_alignment_editor.py, desktop/gui/frontend/test_layout.py, desktop/gui/frontend/test_decision_settings_native.py.

**Interfaces** 기존 styleForm을 통해 paragraph_gap_pt를 저장하고, paragraph align 요청에 leading_pt를 포함한다. 표시 중인 표식과 파일에서 읽은 설정에 해당하는 표시·조판 버전에서만 고정을 허용한다. AI로 조판은 기존 render 경로를 호출하고 현재 동작에서만 모델 결과를 먼저 표시한다.

- [x] **RED.** 브라우저에서 문단 패널이 초기에는 접히고, 열기와 닫기 전후 원고·PDF의 top 및 width가 그대로인지 확인한다. 굵게·기울임의 선택, 해제, 실행 취소를 검증한다. 보류된 조판 응답으로 AI 조판 중 상태와 이전 PDF를 확인하고, 최신 응답의 줄 간격만 고정한다. 파일이나 선택, PDF를 바꾼 뒤 이전 응답이 다른 대상에 적용되지 않는지 웹과 데스크톱에서 검사한다.
- [x] **실패 확인.** 초기 웹 회귀에서 원고와 PDF 표면 y 차이 46.17px, 빈 강조 선택 후 textarea 포커스 상실, 새 leading 입력 부재를 확인했다.
- [x] **구현.** 기존 문단 작업에 연결한 접이식 하단 패널과 상태 표시줄을 배치한다. 파일·편집·서식·조판 메뉴에서 기존 파일, 원고, 양식, 조판을 호출한다. 선택 변경, 저장, 상태 발표, 키보드와 포커스를 기존 편집 수명 주기에 연결한다. 최신 버전에서 측정값의 sp를 반올림한 화면 수치와 구분하고, 현재 문단의 한 항목만 직접 값으로 저장한다.
- [x] **GREEN.** dock, spacing, alignment, editor, jjok, proof-choice 브라우저 테스트와 데스크톱 test_layout, test_decision_settings_native, test_desktop을 실행했다.
- [x] 작업 단위로 커밋한다.

## 통합 검증

- [ ] 같은 LuaLaTeX 환경에서 이전 원고의 규칙 PDF와 고정 모델 PDF를 기준 결과와 비교한다. 쪽 수, 어절 끝, 글자 좌표, 여백 번호를 대조한다.
- [ ] Docker 웹 전체 테스트, 데스크톱 Python 및 Cargo 테스트를 실행한다. PDF에 적용된 새 줄 간격과 기존 원고의 왕복 결과를 확인한다.
- [ ] 데스크톱과 웹에서 하단 패널과 상태줄을 넓은 화면과 좁은 화면에서 확인한다.
- [ ] 검토 결과를 반영하고 README의 조판 및 문단 편집 안내를 갱신한다.
