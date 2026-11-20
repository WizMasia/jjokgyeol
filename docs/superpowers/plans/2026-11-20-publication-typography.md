# T7 출판 양식 구현 계획

> **For agentic workers:** Use superpowers:subagent-driven-development and check off each verified step. Follow the existing A·B·D ownership and independent acceptance review.

**목표** — 새 양식에서 보고서 본문, 제목 단계, 주와 풀이의 글자 모양을 직접 정하고, 이전 양식은 열기·편집·저장 후에도 같은 방식으로 조판한다.

**구조** — 기존 `Style`이 편집 단위와 정수 파일 사이를 변환한다. 조판기는 문서 공통 서식과 기존 구간 규칙을 합친 값을 출력한다. 편집기는 새 탭을 초기화할 때만 여섯 단계 제목 크기를 명시적으로 받는다.

**기술** — Python, FastAPI, 기존 브라우저 JavaScript, LuaLaTeX·luatexko, pytest, Playwright와 Linux ARM64 테스트 이미지. [승인된 설계](../specs/2026-11-20-publication-typography-design.md)

## Global Constraints

- 기존 `Style()`, 이전 API 요청과 저장 파일에서 생략한 제목 크기는 본문에 비례한다. 원고는 UTF-8 마크다운, 양식의 길이는 정수 sp, 비율은 0.01% 단위다.
- 기존 본문 글꼴·크기, 제목 공통 글꼴, 참고 서식의 역할·구간 규칙, 양면 머리글·바닥글 필드는 기존 인터페이스를 유지한다. PDF 비교는 제품 설정과 독립적인 기대값을 쓴다.
- T7은 주와 풀이의 서식을 저장하고 각주를 출력한다. 미주·풀이 원고의 실제 출력은 T9, 줄마다 다른 자간 선택은 T11에서 인수한다.

## Review Focus

1. 이전 파일에서 제목 한 단계만 고치면 나머지 다섯 단계는 본문 크기를 따른다.
2. 화면에 기본값을 표시하는 것만으로 예전 양식에 새 속성이 생기지 않는다.
3. 본문과 제목의 글꼴이 같아도 굵기·기울기·색이 각주, 목록, 다음 문단으로 새지 않는다.
4. 참고 문서 시험 조판이나 취소에서 단계별 서식과 미확정 입력을 보존한다.
5. 자간 하한·기본·상한은 반올림 전후 모두 유효하고 범위를 벗어난 입력은 해당 필드에서 거부한다.

### 1. A — 양식 모델과 저장 파일

**파일** — `app/style.py`, `app/publication.py`, `app/config/publication.json`, `tests/test_publication.py`, `tests/test_style_file.py`.

**계약** — 선택 필드 `text_styles`는 역할별 속성표다. 역할은 `body`, `heading1`…`heading6`, `note`, `gloss`다. `font`, `font_pt`(저장 `font_sp`), `color`, `weight`(normal/bold), `slant`(normal/italic)을 받는다. 본문 글꼴과 크기는 계속 `body_font`, `body_pt`에서 지정한다. 최종 자간의 선택 필드는 `ko_tracking_bounds_pct`(저장 `ko_tracking_bounds_per_10000`) 두 값이다. 생략하면 기본 자간으로 고정한다. `Style.to_stored()`·`to_editor()`는 명시한 역할과 속성만 내보낸다.

- [ ] `test_new_style_keeps_heading_sizes`에서 `editor_bootstrap()["new_text_styles"]`로 시작한 양식의 본문을 11→16pt로 바꿔도 1단계 기본 크기는 1,153,434sp, 명시적으로 18pt를 지정하면 1,179,648sp임을 단언한다. 이전 파일은 열기·저장 왕복 시 새 필드가 없어야 한다. 한 단계만 편집한 경우와 설치 기본값·배율 변경도 검사한다.
- [ ] `test_tracking_bounds_round_trip`에서 기본 −7%와 범위 [−7, 0]이 −700과 [−700, 0]으로 저장되는지 확인한다. 하한 −7%, 기본 −7.001%는 반올림 전에 거부하고 기존 양식이 보존되어야 한다. 색 형식, 미설치 글꼴, 알 수 없는 역할·속성·굵기, 1sp 미만 크기도 오류 필드와 함께 검사한다.
- [ ] `pytest -q tests/test_publication.py tests/test_style_file.py`에서 새 동작의 실패를 확인한다. `apply_style(current, patch)`와 `from_stored_style(fields)`에서 기존 숫자·글꼴·색 검증을 사용한다. 바뀐 자간 한계는 문서 공통 크기와 유효한 구간 크기에도 조판 가능해야 한다.
- [ ] 이전 API의 `defaults`를 보존하고 새 편집기용 `new_text_styles`와 요소 편집에 필요한 출판 기본값을 부트스트랩에 제공한다. 설치 설정을 바꾼 테스트와 앞 명령을 통과시킨 뒤 커밋한다.

### 2. B — 실제 PDF에 적용

**파일** — `app/typeset.py`, `app/reference_rules.py`, `app/tex/layout.lua`, `app/tex/book.tex`, `tests/test_typeset.py`, `tests/test_reference_typeset.py`. 인수 진입점은 `tests/test_publication.py::test_report_style`이다.

- [ ] 본문 명조 11pt `#222222`, 제목 고딕 굵게 18pt `#17365D`, 마크다운 기울임, 각주 색·크기와 머리글·쪽 번호를 실제 PDF 글자에서 읽는다. 크기는 TeX pt에서 PDF pt로 변환해 비교한다. 같은 계열의 글꼴을 쓰는 경우와 색이 다른 각주 참조·각주 번호도 검사한다. 기존 출력이 새 단언을 충족하지 못함을 확인한다.
- [ ] 문서 공통 역할 값 위에 기존 `block_values`의 전역·구간 값을 속성별로 합친다. 원래 블록과 렌더용 블록의 대응, 울타리 안 예제, 조 제목 재번호의 기존 의미를 유지한다. Lua에서 허용된 글꼴·색·굵기·기울기만 선택하며 각주 번호와 내용을 같은 각주 서식으로 그룹화한다. 목록 표지 상자 재사용 키에는 실제 글자 모양을 반영한다.
- [ ] `pytest -q tests/test_publication.py::test_report_style tests/test_reference_typeset.py tests/test_typeset.py tests/test_list_typeset.py`를 통과시키고, 기본·이전 저장 양식의 PDF 좌표 서명을 `test_prechange_geometry`와 대조해 커밋한다.

### 3. D — 편집기와 참고 서식 검토

**파일** — `app/static/index.html`, `app/static/editor.js`, `app/static/reference.js`, 필요 시 `app/static/editor.css`, `tests/test_editor.py`, `tests/test_style_file.py`.

- [ ] 브라우저에서 새 양식의 1단계 제목을 18pt로 설정하고 본문만 16pt로 바꾼다. 역할을 바꿨다 되돌아와도 설정이 유지되고 저장·재개방 후 같은 JSON을 얻는지 검사한다. 기존 파일은 열기만 해서 받은 JSON이 원본과 일치해야 한다. 참고 서식 시험 적용·취소와 잘못된 자간 범위 입력 후 직전 미리보기 보존도 검사한다.
- [ ] 새 테스트가 실패하는지 확인한 뒤 `styleForm.get()/set()`에 역할별 입력과 선택적 자간 한계를 연결한다. 새 탭에만 `new_text_styles`를 공급한다. 참고 서식의 현재 값은 구간 값, 문서 전체 규칙, 역할별 직접 지정, 기존 기본값의 순서로 읽는다. 이전 부트스트랩 응답도 연다.
- [ ] `pytest -q tests/test_editor.py tests/test_style_file.py tests/test_reference_editor.py`로 검증하고 커밋한다.

### 4. 독립 검토와 인수

- [ ] 위 세 작업 각각에 대해 스펙 충족과 코드 품질을 독립 검토한다. 새 양식·저장 양식을 실제 서비스의 브라우저에서 열어 원고 보존, 최신 미리보기와 다운로드의 SHA-256 일치, 잘못된 입력 후 복구를 확인한다.
- [ ] 변경 소스가 들어간 서비스·테스트 이미지를 빌드하고 네트워크를 끈 전체 pytest 및 기존 호스트 전용 인수를 실행한다. 예전 입력의 줄·쪽·글자 좌표, 양면 제본, 참고 문서 시험 적용도 검증한다. 예제 원고·양식과 실제 전후 PDF를 사용 안내 및 인수 기록에 연결한다.
- [ ] T7의 통과 항목과 T9·T11에서 이어갈 실출력 검사를 전체 계획에 기록하고 커밋한다.
