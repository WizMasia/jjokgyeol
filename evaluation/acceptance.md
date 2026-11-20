# T14 사전 실행 기록 / Pre-evaluation

2026-11-20, `feature/blind-reading`, 시작 커밋 `9c5081d`. Linux ARM64 이미지 `jjokgyeol:mirrored-margins-test`의 ID는 `sha256:285585be4a46449a1220d676331c17807801e6a9f1736ead3a73de94e5887954`다.

### 테스트

다음 함수는 t14 작업트리 루트에서 사용한다.

```sh
check() {
  docker run --rm --network none --shm-size=512m \
    -v "$PWD:/opt/jjokgyeol:ro" jjokgyeol:mirrored-margins-test pytest -q "$@"
}
check tests/test_evaluation.py
```

- 초기 RED는 모듈이 없어 1 failed, 1 error였다. 같은 명령으로 실제 API PDF 판독과 원고 분리 2 passed를 확인했다.
- 기준 지면·T13 경로 추가 시 `check tests/test_evaluation.py -k 'missing_provider or reference_history or model_'`가 2 failed, 10 errors였다. 구현 뒤 전체 14 passed였다. 총괄의 파일 인계로 바꾸며 `check tests/test_evaluation.py -k missing_bundle`가 함수 인수 불일치로 실패했고, 양쪽 실제 PDF·로그 입력 계약으로 전체 19 passed였다.
- `check tests/test_evaluation.py -k 'percentile or pdf_constraint_audit'`가 2 failed였다. PDF 위반 실측·응답·채점·시간 판정 연결 뒤 전체 29 passed였다. 분할 간 같은 개발 원고의 중복도 호스트에서 RED → GREEN으로 확인했다.

```sh
/private/tmp/jjokgyeol-dev/venv/bin/python -m pytest -q tests/test_evaluation.py -k test_split
check tests/test_evaluation.py tests/test_typeset.py::test_pdf_spacing tests/test_slice.py::test_tracking_scales_with_printed_type
```

최종 명령은 T14 30개와 기존 PDF 회귀 2개를 포함해 32 passed, 46.50초였다. 사방 여백을 벗어난 지면과 T13 응답 헤더 원값의 처리도 실패를 확인한 뒤 고쳤다. FastAPI TestClient와 잠금 httpx 조합에서 사용 중단 예정 경고 1건이 표시됐다.

단위시험의 작은 임시 CSV·PDF·로그는 프로그램의 경계 검사용이다. 실제 평가 자료와 인수 결과에 포함하지 않았다.

### 실제 자간 실행

```sh
mkdir -p evaluation/runs
docker run --rm --network none --shm-size=512m \
  -v "$PWD:/opt/jjokgyeol:ro" -v "$PWD/evaluation/runs:/runs" \
  jjokgyeol:mirrored-margins-test python -m evaluation spacing /runs/spacing-initial
/private/tmp/jjokgyeol-dev/venv/bin/python -c 'from pathlib import Path; import sys; sys.path.insert(0, "tests"); from test_evaluation import test_blind_set; test_blind_set(Path("evaluation/runs/spacing-initial")); print("12 blind PDFs and original measurements verified")'
/private/tmp/jjokgyeol-dev/venv/bin/python -m evaluation score evaluation/runs/spacing-initial
```

`evaluation/runs/spacing-initial/reader/`에 익명 1쪽 PDF 12개와 12행 응답표가 있다. 영문 글자 간격·한글 자간·글꼴·크기를 원본에서 대조하고, 열람 PDF의 판형·이미지·메타데이터를 따로 확인했다. 열람 폴더와 비공개 키는 로컬에 보관했다. 렌더 API 12회 실측은 최소 1,781ms, 중앙 2,230ms, p95·최대 2,959ms였다. 익명화 시간은 측정 구간 밖이다. 통합 테스트도 같은 호스트에서 실행 중이었다. 상태는 `awaiting_reader`다.

```sh
/private/tmp/jjokgyeol-dev/venv/bin/python -m evaluation capture evaluation/runs/spacing-initial evaluation/runs/spacing-initial/reader/reading-sheet.csv --reader empty-check
/private/tmp/jjokgyeol-dev/venv/bin/python -m evaluation pairs evaluation/runs/model-initial evaluation/runs/references evaluation/runs/t13-real
```

빈 응답표와 실제 T13 번들 부재는 각각 종료 코드 2로 거절됐다. 편집자 기준 지면, 실제 모델 쌍, 사람 판독, 사용자의 시간 수용은 대기 중이다. 현재 수치는 자간 PDF 생성 시간이다.
