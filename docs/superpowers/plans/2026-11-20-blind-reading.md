# T14 블라인드 판독 구현 계획

Superpowers executing-plans와 red → green TDD를 적용한다. 독립 검토자는 총괄이다.

**목표**　편집자가 받아 읽을 수 있는 자간 실험과 T13 연결 후의 모델 실험을 같은 로컬 도구로 준비한다. [사전 규약](../specs/2026-11-20-blind-reading-design.md)

**범위**　`evaluation/`에 전용 원고·도구·안내를 둔다. 테스트는 `tests/test_evaluation.py` 한 파일이다. 잠긴 Linux ARM64 test 이미지의 FastAPI TestClient, Poppler, Pillow, pypdf, pdfminer를 사용한다.

### Task 1 — 원고 분리와 자간 실행

- [ ] 테스트에 원고 그룹·정규화 본문 중복, 실제 PDF 열두 개의 글꼴·자간, 무작위 대응표·익명 열람본·빈 CSV 계약을 적고 RED를 확인한다.
- [ ] `evaluation/corpus/`, `evaluation/packet.py`, `evaluation/reading-sheet.csv`를 만든다. API 시간 측정, 원본 보관, 무손실 익명화, 실행 중 오류 복구를 구현한다.
- [ ] 대상 테스트 GREEN을 확인하고 커밋한다.

### Task 2 — 기준 지면과 T13 계약

- [ ] 편집자 수정 이력과 최종 승인, T13 부재·복귀·동일 지면·원고 누락 거절, 모델 쌍 구성과 실제 PDF 줄 대조 테스트를 작성하고 RED를 확인한다.
- [ ] `evaluation/model.py`에서 기준 PDF·사유를 보존하고, 버전이 고정된 T13 공급자의 원고·선택·PDF·검측 기록을 연결한다. 실제 T13 없이 제품 경로에서 대체 출력을 만들지 않는다.
- [ ] 대상 테스트 GREEN을 확인하고 커밋한다.

### Task 3 — 사람 응답과 인계

- [ ] 불완전·중복·타 실행 표, 동률, 기준 대비 잔여 수정, 시간 수용과 채택 경계를 인공 단위시험으로 적고 RED를 확인한다.
- [ ] `evaluation/results.py`, `evaluation/__main__.py`, `evaluation/README.md`에서 생성·기준 확정·응답 등록·시간 요약·채점을 제공한다.
- [ ] 오프라인 `pytest -q tests/test_evaluation.py`와 실제 자간 CLI를 실행한다. 익명 자료와 비공개 대응표를 로컬에 보관하고 커밋·명령·결과를 총괄에 넘긴다.

**검토 초점**　PDF 메타데이터 누출, 원고 재사용, 메타데이터만 다른 쌍, 부분 응답의 오채택, 측정과 사람 승인 혼동을 확인한다.

