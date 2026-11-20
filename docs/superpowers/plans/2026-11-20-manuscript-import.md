# T10 원고 가져오기 구현 계획

**목표** 다섯 형식에서 새 마크다운을 반환하는 로컬 라이브러리를 Linux ARM64에서 검증한다. [기능 명세](../specs/2026-11-20-manuscript-import-design.md)

### Task 1: 다섯 판독기와 실물 대조

- [ ] `tests/fixtures/imports/`에 제공된 원본 6개, TXT·HTML 교정 원고, 출처와 SHA-256을 기록한다. 저장소 Office 증빙은 기존 경로에서 읽는다.
- [ ] `tests/test_imports.py`에 형식별 기대 문단, 제목·목록 깊이·강조 구간, 본문 외 제어 배제, 손상·한도·외부 참조 단언을 작성한다. 복합 HWP의 셀 문구와 그림 경고, 같은 ‘3-2-1’의 서로 다른 역할도 검사한다.
- [ ] `docker run --rm --network none --shm-size=512m -v "$PWD:/opt/jjokgyeol:ro" jjokgyeol:mirrored-margins-test pytest -q tests/test_imports.py`로 미구현 실패를 확인한다.
- [ ] `app/importers/read.py`에서 입력 검증·분기·압축 한도를 맡고 공통 출력부에서 문단·단계·경고를 모은다. `text_html.py`, `docx.py`, `hwpx.py`, `hwp.py`를 구현한다.
- [ ] olefile의 로컬 배포물과 라이선스·해시를 고정하고 Docker 서비스·테스트 양쪽에 설치한다. 이미지에 테스트 원본과 대조 자료를 포함한다.
- [ ] 새 의존성을 포함한 오프라인 Linux 이미지에서 같은 pytest 선택을 다시 실행하고 기존 Office·입력·런타임 시험을 확인한다. 원본 해시와 커밋 범위를 대조한 뒤 총괄의 독립 검토로 인계한다.

**검토 초점** 문단 번호와 UTF-16 위치의 혼동, 출력 중간의 머리글/필드 혼입, 깊이 점프에서 가짜 목록 항목 생성, 누락된 그림 뒤 본문 손실, 비정상 입력에서 부분 결과 반환.
