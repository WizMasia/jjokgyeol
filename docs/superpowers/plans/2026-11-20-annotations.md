# T9 원고 주석 구현 계획

**목표** — [주석 설계](../specs/2026-11-20-annotations-design.md)에 정한 네 가지 표기를 기존 편집기와 조판기에 연결한다. 구현 단위별로 먼저 실패하는 테스트를 확인한 뒤 변경과 독립 검토를 진행한다.

## Global Constraints

원본 원고의 개행과 문자, 실제 각주 배치, 기존 양식 검증을 보존한다. 기존 참조 표기와 이름에 종류를 부여하지 않는다. 길이·비율의 기본값은 T7 출판 설정을 사용한다.

## Review Focus

중복 ID, 같은 주 반복 참조, 제목·목록 안의 주석, 같은 단어가 두 번 쓰이는 문장, 판면보다 긴 풀이를 각각 확인한다.

### Task 1. 원고 모델
- [ ] `tests/test_annotations.py`에서 `parse_md` 결과가 각주·미주·메모의 세 정의 집합과 각각의 참조, 풀이가 붙은 단어를 구별하는지 검사한다. 이름이 겹치거나 참조 대상이 없으면 원고 오류여야 한다. 기존 `end-`·`memo-` 각주와 CRLF 원고도 검사한다. 테스트를 먼저 실행해 실패를 확인한다.
- [ ] `app/annotations.py`와 `app/document.py`의 기존 토큰 파싱에 정의 종류와 인라인 풀이를 연결한다. `Document.footnotes/endnotes/memos`는 ID와 문자열의 사전이다. 미주와 메모 참조는 `Inline.kind=endnote_ref/memo_ref`, 풀이는 `kind=gloss`, `children=[단어의 text 인라인]`, `text=풀이`로 표현한다. 제목·조 번호·구간 선택 및 울타리 처리에서 원고를 다시 파싱할 때도 종류를 유지한다.
- [ ] `pytest -q tests/test_annotations.py tests/test_input.py tests/test_article_edit.py tests/test_reference_rules.py`와 관련 회귀를 통과시키고 커밋한다.

### Task 2. 조판
- [ ] 실제 PDF에서 미주 순서와 재인용 번호, 쪽 하단 각주, 메모 생략, 두 번째 단어의 작은 풀이, 좁은 폭에서의 줄바꿈을 검사하고 실패를 확인한다.
- [ ] `app/typeset.py`, `app/tex/layout.lua`, 기존 어절 배치에 주석을 연결한다. T7의 주·풀이 서식을 사용한다. 출력에서 쓰지 않는 메모 내용은 TeX 입력 데이터에서 제외한다. 사용자가 적은 글자는 기존 literal 경로로 출력한다.
- [ ] `pytest -q tests/test_annotations.py tests/test_typeset.py tests/test_breaks.py`를 통과시키고 커밋한다.

### Task 3. 인수
- [ ] `test_reopen_and_reflow`에서 실제 브라우저의 원고 저장·열기와 PDF 다운로드를 검사한다. 파일 재개방 뒤 종류·내용과 원고 해시를 대조한다. 기존 쪽 경계 원고에서 본문 크기를 바꿔 각주의 이동을 확인한다.
- [ ] 네 종류를 함께 쓴 예시와 미리보기·다운로드 PDF를 보관하고 사용법을 README에 반영한다. 이미지 전체 회귀와 독립 스펙·품질 검토 후 완료 항목을 기록한다.
