# 일반 보고서 양식 구현 계획

Superpowers subagent-driven-development와 TDD로 [양식 설계](../specs/2026-11-20-general-report.md)를 구현한다. 기존 Style 모델, LuaLaTeX 조판기, HTML 편집기의 저장·열기 흐름을 사용한다.

- [ ] 제목에 한해 선택 속성 `text_styles.heading1.frame="box"`를 검증·저장하고 PDF에서 사각 테두리와 다행 제목을 검사한다. 기본 제목의 지면과 각주도 확인한다.
- [ ] 목록 단계에 선택 속성 `font`, `font_pt`(저장 시 `font_sp`), `weight`, `mark_weight`, `indent_origin="margin"`을 지원한다. 목록 본문·표지에 반영하고 원래의 들여쓰기, 양식 파일 왕복과 입력 검증을 검사한다.
- [ ] `proofs/general-report/`에 일반 보고서 JSON과 실제 출력 예시를 제공한다. 호스트 Chromium으로 열고 저장한 JSON을 원본과 비교하고, 다운로드 PDF의 글꼴·크기·기호·위치를 설계와 독립적으로 측정한다. 최종 이미지 회귀와 검토 뒤 사용법을 기록하고 비공개 저장소에 푸시한다.
