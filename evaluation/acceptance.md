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

첫 실행 당시 빈 응답표와 T13 번들 부재는 각각 종료 코드 2로 거절됐다. 위 시간은 자간 PDF 생성 시간이다.

### 독립 검토 후 재검증

고정한 원고에서 실제 모델과 규칙이 같은 결정을 내린 경우도 판독 대상으로 삼는다. 테스트용 수신 기록과 PDF로 선택만 일치, 지면만 일치, 둘 다 일치하는 세 경우를 검증했다. 변경 전에는 세 경우 모두 쌍 생성 단계에서 실패했다. 변경 후에는 각 묶음의 두 표본을 판독표에 유지하고 시험 응답에 표시된 선호를 그대로 집계했다.

```sh
docker build --platform linux/arm64 --target test -t jjokgyeol:t14-test .
docker run --rm --network none --shm-size=512m jjokgyeol:t14-test pytest -q tests/test_evaluation.py
docker run --rm --network none --shm-size=512m -v "$PWD:/opt/jjokgyeol:ro" jjokgyeol:t14-test pytest -q
```

수정 전 test 이미지에서는 소스 마운트 없이 `test_split`이 평가 모듈 누락으로 실패했다. 수정 후 같은 방식으로 T14 테스트 33개가 통과했다(40.96초, TestClient 경고 1건). 이미지 안의 `evaluation/` 파일 10개를 허용 목록과 대조했다. 실행 폴더와 응답 키는 제외됐다. 서비스 단계 이미지에도 평가 폴더가 없었다. 기존 자간 실행은 읽기 전용으로 열어 익명 PDF 12장의 해시와 `awaiting_reader` 상태를 확인했다.

저장소를 읽기 전용으로 마운트한 전체 회귀는 643 passed, 47 skipped, 경고 1건이었다(373.10초).

### 고정 원고의 실모델 출력

두 원고를 규칙과 실모델로 각각 조판했다. 네 PDF 모두 여백·자간·공백 위반과 선택한 줄 끝의 불일치가 0건이다. 측정물은 대응표와 함께 운영자용으로 보관한다. 편집자가 두 기준 지면을 확정하면 익명 비교를 시작한다. 사람의 자간·지면 선호와 갱신 시간 수용 판정은 대기 중이다. [실행 환경과 응답 측정 기록](../docs/superpowers/decision-acceptance.md)

### 양끝맞춤 판독의 PDF 갱신 시간

새 판독 묶음의 렌더 요청 12회와, 별도 비교 원고 두 편에서 규칙·모델로 받은 PDF 네 개의 갱신 시간을 초로 표시한다. 요청부터 PDF 수신까지의 실측이며, p95는 최근접 순위로 구했다. 표본 수가 작아 각 조건에서 p95와 최댓값이 같다. [인쇄 지면·어절 검증](../docs/superpowers/plans/2026-11-20-justified-publication.md)

| 조건 | 요청 수 | 최소(초) | 중앙(초) | p95(초) | 최대(초) |
| --- | ---: | ---: | ---: | ---: | ---: |
| 판독 표본 전체 | 12 | 1.96 | 2.45 | 2.82 | 2.82 |
| 나눔고딕 11pt | 3 | 1.96 | 2.10 | 2.34 | 2.34 |
| 나눔고딕 16pt | 3 | 2.06 | 2.06 | 2.24 | 2.24 |
| 나눔명조 11pt | 3 | 2.56 | 2.61 | 2.82 | 2.82 |
| 나눔명조 16pt | 3 | 2.67 | 2.68 | 2.69 | 2.69 |
| 비교 원고 · 규칙 | 2 | 2.57 | 2.60 | 2.63 | 2.63 |
| 비교 원고 · 실모델 | 2 | 10.30 | 10.31 | 10.33 | 10.33 |

원자료는 로컬 실행 `evaluation/runs/justified-print/private/key.json`과 `evaluation/runs/t13-justified/{drawer,platform}/{rules,model}/measurement.json`에 있다. 지면 선호와 대기 시간 수용 여부는 별도로 판정한다.
