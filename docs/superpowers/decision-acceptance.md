# T13 문단 선택 검증 기록

실행한 경로는 일반 조판 API와 격리 작업자다. `decision`을 생략하면 일반 조판기로 보낸다. 명시적인 `rule`은 `collect_candidates=True`로 규칙 지면을 수집한다. `model`은 같은 수집에서 시작해 새 폴더에 선택 ID를 재생하고 최종 로그를 검사한다. 운영자가 모델을 연결하지 않은 경우에도 명시적인 평가 요청은 실측 규칙 지면을 반환한다.

## 로컬 인수 표본

[이슬 원고](../../tests/fixtures/decision-dew.md)는 T13에서 새로 쓴 개발 표본이다. 학습은 수행하지 않았다. T14 평가 전에 고정한 모델과 프롬프트로 생성한 [규칙 PDF](../../proofs/decision-model/rule.pdf)와 [모델 PDF](../../proofs/decision-model/model.pdf)의 줄 끝이 달랐다.

| 실제 적용 | 선택 ID | 누적 끝 글리프 | 첫 줄 |
|---|---|---|---|
| 규칙 | b118af46d118839d2e7438ba07f4b084 | 9, 11 | 풀잎 사이 Dew 은빛 |
| 모델 | d8e82a2ce373d77c52420c6fdc9123c2 | 7, 11 | 풀잎 사이 Dew |

양쪽 모두 1쪽이며 마지막 어절 뒤의 참조 1과 ‘아침 수집함.’ 각주를 보존했다. PDF 판독기의 첫 각주 글자 원점은 양쪽 모두 (42.693, 28.346) PDF pt다. 본문 좌표로 한글 자간 −3%와 글꼴 고유 공백 100%를 0.04 PDF pt 오차 안에서 확인했다. 각 로그는 해당 요청의 PDF가 나온 패스에서 작업 폴더 정리 전에 복사했다. [규칙 로그](../../proofs/decision-model/rule.jsonl) · [모델 로그](../../proofs/decision-model/model.jsonl) · [규칙 해시·시간](../../proofs/decision-model/rule-measurement.json) · [모델 해시·시간](../../proofs/decision-model/model-measurement.json)

## 배포 검증

LM Studio 설정의 `downloadsFolder`와 `lms ls google/gemma-4-e2b --json`의 4bit 행을 따라 `lmstudio-community/gemma-4-E2B-it-MLX-4bit`를 두 배포판에 읽기 전용으로 마운트했다. `model.safetensors`는 4,339,886,573바이트이며 SHA-256은 `54dfb6c99a9778a68dd5f3300f13930e65b567dab1ac54340001086bcf65a692`다. `/api/v1/models`에서 활성 `google/gemma-4-e2b@4bit`, `lms runtime ls`와 실제 추론 응답에서 `mlx-llm-mac-arm64-apple-metal-nax-advsimd@1.13.1`을 확인했다. 가중치와 나머지 일곱 파일의 해시는 모델 작업마다 검사한다.

| 일반 HTTP 서버 | 이미지 ID | 규칙 / 모델 요청 시간 | 첫 줄 끝 |
|---|---|---|---|
| local | `8dc56a97c7b4b39ec8926e352ec133d1373c0664def83ed6a28ed7ce52d3556e` | 2.422 / 9.207초 | 9 / 7 |
| service | `514febd861570ddcacb9937da1871d4efdce8e84b8a66a632c59b5856bc1c502` | 2.417 / 9.468초 | 9 / 7 |

위 시간은 각 응답을 한 번씩 호스트에서 받아 PDF를 판독할 때까지 걸린 값이다. 별도로 각 임시 루프백 주소에서 `JJOKGYEOL_DECISION_TEST_URL=http://127.0.0.1:<port> python -m pytest -q tests/test_decision.py::test_real_local_model`이 통과했다. 모델을 내리거나 바꾸지 않았다.

`test_choice_reaches_pdf`는 실측 후보 두 개를 각각 선택시켜 두 PDF의 글자 원점·줄 끝·본문/각주를 판독한다. 오프라인 기한 시험은 세 문단의 느린 요청에서 두 번째 문단 처리 중 이미 만든 규칙 PDF로 복귀한다. 전체 오프라인 `pytest -q` 결과는 676 통과, 48 건너뜀이다. 전체 실행 도중 발견한 T7 초기 설정 키 기대값은 T7 커밋 `47c06e5`의 동일한 시험 수정으로 맞추었다.

## T14 인계

T14 `12790b6`의 고정 원고·양식과 T13 test 이미지로 지면을 만들었다. 평가용 MODEL_STYLE은 `ko_tracking_bounds_pct=[-7, 0]`을 명시하며 양쪽 모두 실측 레이아웃을 사용했다. 다음 예는 T13 작업 폴더에서 실행한다. 출력 상위 폴더는 컨테이너의 editor 사용자가 쓸 수 있어야 하고 마지막 경로는 새 이름이어야 한다. `model_dir`은 README의 LM Studio 설정 조회값이다.

```sh
docker build --platform linux/arm64 --target test -t jjokgyeol:t13-test .
docker run --rm -e JJOKGYEOL_DECISION_HOST=host.docker.internal \
  --mount "type=bind,source=$model_dir,target=/opt/decision-model,readonly" \
  -v "$(cd ../t14/evaluation && pwd):/opt/jjokgyeol/evaluation:ro" \
  -v "$(cd ../t14/evaluation/runs && pwd):/runs" \
  jjokgyeol:t13-test python packaging/decision_bundle.py /runs/t13-next
```

생성기는 T14와 동일한 TestClient로 정상 `POST /api/render`를 부르고 로컬 작업 폴더 정리만 로그를 읽을 때까지 미룬다. 반환 PDF와 그 패스의 `book.pdf`가 같은지 검사한다. `measurement.json`에는 응답 헤더 원값, 원고·PDF·로그·모델 해시, 양식과 요청 소요 시간이 있다. `outcome.json`에는 PDF 독립 판독 결과와 T14 측정물 검사 결과가 있다.

실행 결과는 T14 워크트리의 Git 제외 경로 `evaluation/runs/t13-real`에 있다. 평가용 두 원고는 프롬프트와 개발 표본 검증을 마친 후 사용했다. 두 원고 모두 규칙·모델 지면의 후보 목록이 같고, 모델 응답 헤더는 `model`이었다.

| 원고 | 규칙 / 모델 소요 시간 | 모델이 규칙과 다른 ID를 고른 문단 | PDF 제약 위반 / 줄 끝 불일치 |
|---|---|---|---|
| drawer | 2278.5 / 10576.0 ms | 0 / 3 | 양쪽 모두 0 / 0 |
| platform | 2591.2 / 10717.5 ms | 0 / 3 | 양쪽 모두 0 / 0 |

각 원고에서 3개 문단 모두 실제 모델이 규칙과 같은 ID를 선택했다. 두 원고 모두 규칙·모델 PDF를 독립적으로 300dpi로 래스터화한 지면이 같았다. 수정된 T14 측정물 검사기(evaluation/model.py SHA-256 `6f8b5915a8c1a7e396e521d255e60ece96739dac37272cfb5ff50fff22f9b3ed`)는 기존 네 응답과 영수증을 모두 수락했다. 이 재검사에는 원래 원고·모델 응답·PDF를 그대로 사용했다.

두 쌍 모두 고정 평가 집합에 남는다. T14의 운영자용 표기로는 두 쌍 모두 `same_choices=true`, `same_pages=true`다. 사람의 선호·동률과 수정 의견, 편집자가 확정한 기준 지면, 사용자 시간 수용 판정은 아직 없다. 제품 기본값은 규칙이다.
