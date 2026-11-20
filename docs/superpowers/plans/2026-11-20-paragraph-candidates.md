# T11 구현 계획

지정 워크트리 `t11`에서 아래 순서로 실행한다. 상위 작업자가 반환 후 독립 검토한다.

1. `tests/test_mixed_layout.py`에서 좁은·넓은 PDF의 줄 끝, 자간 0/−3/−7%, 한글과 영문, 글꼴 공백 4pt의 고정·가변 지면을 독립 판독한다. 후보 수집·재생 계약의 RED를 기록한다.
2. `app/tex/wordbreak.lua`의 어절·문자 묶음·선택한 개행 잠금과 `fixed.lua`의 정수 간격 계산을 공유한다. 새 `app/tex/paragraph.lua`에서 노드 폭으로 최대 네 완성 후보를 비교하고 선택안만 같은 노드에 적용한다.
3. `app/tex/layout.lua`의 본문 종료 시점과 `app/typeset.py`에 수집·ID 재생을 연결한다. 두 서로 다른 줄 끝의 ID를 실제 PDF에서 재생하고 원고·폭 변경 및 잘못된 ID의 복귀를 검사한다.
4. 외톨이 마지막 글자, 쪽 아래 제목, 각주, 금칙, 긴 URL, 들여쓰기, 하한과 줄별 보정을 검사한다. 기존 T3·T6 및 전체 오프라인 시험으로 회귀를 확인한다. 실패 원인과 명령·결과를 기록하고 기능 브랜치에 커밋한다.

## 확인한 PDF

| 조건 | PDF 판독 결과 |
| --- | --- |
| 같은 혼합 원고, 계속 줄 2mm 들여쓰기 | 본문 폭 [26mm](../../../proofs/paragraph-candidates/26mm.pdf)에서 10줄, [39mm](../../../proofs/paragraph-candidates/39mm.pdf)에서 6줄. 두 쪽 모두 Markdown과 PDF는 온전하고 URL·긴 어절만 내부에서 개행했다. 참조와 각주는 각각 같은 쪽에 있다. |
| 25mm, 고정 간격 | [규칙](../../../proofs/paragraph-candidates/rule.pdf)의 첫 줄은 ‘강가 가나’, [다른 ID 재생](../../../proofs/paragraph-candidates/replay.pdf)의 첫 줄은 ‘강가 가나 다른’이다. 두 후보의 ID와 줄별 끝 위치·공백 폭은 [재생 기록](../../../proofs/paragraph-candidates/replay.jsonl)에 있다. |
| 나눔명조 기본 공백 4 TeX pt | PDF의 두 공백은 고정 시 3.9893·4.0026pt였다. 본문 폭을 1pt 줄이면 고정 양식은 River를 다음 줄에 둔다. 가변 양식은 두 공백을 각각 3.4956·3.4823pt로 줄여 River를 첫 줄에 놓았다. |
| 본문 폭 29.8mm, 기본 자간 0%, 하한 −7% | 첫 줄은 −3.23%, 마지막 줄은 0%였다. PDF의 첫 한글 간격은 각각 −0.352pt, 0pt였다. 기본과 하한이 모두 −7%인 경우도 검사했다. |

최초 구현 `61ceefb`에서는 기존 기본 양식의 전체 PDF 글자 좌표 해시가 일치했다. 가변 공백을 저장한 `proofs/web-editor/fixed-point-style.json`은 2쪽·23줄을 유지하고 두 문단의 줄 끝이 달라졌다. 기존 `test_prechange_geometry[saved]`의 전환 전 해시와 새 해시는 각각 `1666f2dc…`, `6b095dac…`이다. 아래 후속 작업에서 이 배치를 명시적으로 선택하도록 변경했다.

## 최초 구현 실행 기록 (`61ceefb`)

작업 위치는 `.superpowers/worktrees/t11`이다. Linux ARM64 시험 이미지 ID는 `sha256:285585be4a46449a1220d676331c17807801e6a9f1736ead3a73de94e5887954`다. 아래 명령의 러너는 다음과 같다.

```sh
t11() {
  docker run --rm --network none --shm-size=512m \
    -v "$PWD:/opt/jjokgyeol:ro" jjokgyeol:mirrored-margins-test "$@"
}
```

| 명령 | 결과 |
| --- | --- |
| `t11 pytest -q tests/test_breaks.py tests/test_typeset.py` | 변경 전 29 통과 |
| `t11 pytest -q tests/test_mixed_layout.py` | 최초 RED, 수집 API 부재 6 실패 |
| `t11 pytest -q tests/test_mixed_layout.py -k 'expanded or consecutive'` | 확장 후보 부재·연속 공백 소실로 2 실패. 공백 보존 후 여닫는 따옴표 분리도 재현하고 수정함 |
| `t11 pytest -q --basetemp=/tmp/t11 tests/test_mixed_layout.py tests/test_breaks.py tests/test_typeset.py` | 최종 38 통과. 위 교정 PDF 네 개와 JSONL을 이 실행에서 복사함 |
| `t11 pytest -q --basetemp=/tmp/ruler tests/test_mixed_layout.py -k "spacing_limits or line_correction"` | 2 통과, 7 선택 제외. 위 숫자는 렌더 결과에서 글자 원점 차이와 advance로 측정함 |

전체 오프라인 실행 `t11 pytest -q` 결과는 650 통과, 47 건너뜀, 기존 실패 1건이었다(412.12초).

T9 `fae10e0` 임시 합본에서 `pytest -q tests/test_annotations.py -k gloss`는 7 통과, 7 선택 제외였다. 병합 시 `wordbreak`가 T9의 `gloss_attribute` 및 T11의 `wordspace/chunks/lock`을 함께 내보내고, `typeset.py`가 T9의 메모 제거와 T11의 재생 인자를 함께 유지해야 한다. 후보 경로도 T9의 `chunks(first, stop, 계속줄폭)`를 호출한다.

최초 구현의 전체 시험에서 실패한 `test_configured_limits_reach_requests_and_worker`는 조판기를 대역으로 교체한다. 편집기 초기 설정 키 집합을 검사하는 101행에서 T7의 신규 키와 기대값이 다르다. 아래 명령으로 T11 직전 코드에서도 동일 실패 1건을 확인했다.

```sh
git archive bd86ec4 | docker run --rm -i --network none --shm-size=512m \
  -e PYTHONPATH=/tmp/t11-base -w /tmp jjokgyeol:mirrored-margins-test \
  sh -c 'mkdir t11-base && cd t11-base && tar -xf - && pytest -q tests/test_operations.py::test_configured_limits_reach_requests_and_worker'
```

## 후속 호환 작업

1. 저장 교정 원고를 저장 양식 그대로 열어 출력한 첫 줄과, 같은 양식에 balanced를 지정한 첫 줄을 공개 API로 비교한다. 먼저 기존 지면이 달라지는 실패를 확인한다.
2. 편집·저장 양식의 선택 필드, 잘못된 값, 새 문서 전용 설치 설정을 시험하고 기존 Style 경로에 연결한다. HTML·정적 자산과 기존 교정본의 트리 ID를 대조한다.
3. 일반 출력에서 명시한 모드·자간 범위와 명시적 후보 수집·재생만 실측 경로로 연결한다. T11 시험에서 선택을 명시하고 기본·저장 양식의 기존 PDF 좌표 해시와 전체 회귀를 다시 확인한다.

공개 API의 새 인수 시험은 `61ceefb`에서 실패했다. 원본 첫 본문 줄의 ‘들어오는’이 다음 줄로 이동했다. 변경 후에는 같은 저장 양식에서 ‘눈에 들어오는’까지 인쇄하고, `paragraph_layout="balanced"`를 넣으면 ‘눈에’에서 줄을 바꾼다. 편집기에 노출하는 새 문서 기본값은 `new_paragraph_layout`이다. [양식·재생 계약](../specs/2026-11-20-paragraph-candidates-design.md)

실제 서비스 인수에는 로컬 Linux ARM64 시험 이미지에 현재 `app/`을 복사했다. 아래는 변경 후 실행 명령이다.

```sh
image_id=$(docker build --network none --pull=false -q -f - . <<'DOCKERFILE'
FROM jjokgyeol:mirrored-margins-test
COPY app/ /opt/jjokgyeol/app/
CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8000"]
DOCKERFILE
)
JJOKGYEOL_TEST_IMAGE="$image_id" /private/tmp/jjokgyeol-dev/venv/bin/python -m pytest -q -s \
  tests/test_refactor_acceptance.py::test_prechange_geometry \
  tests/test_refactor_acceptance.py::test_saved_proof_layout_is_opt_in
```

3건 통과. 두 양식 모두 독립 판독기의 글자 배치 해시와 전체 좌표·텍스트 해시가 기존 기대값과 일치했다.

| 양식 | 줄·글자 배치 SHA-256 | 전체 좌표·텍스트 SHA-256 |
| --- | --- | --- |
| 기본 | `28208635caf3ebe7f6ff314c0e8019bd897a60b88fa1428870cb2ec748db9327` | `8cdd1eb9c972f883f717498005e5b6753500cba9f1ba9e4807dcc930b353ebff` |
| 저장 | `de354c6cbea36e9045f358525a607a25054ac212a99bf60fb446aeb4f6a4be79` | `1666f2dce73aafcff1dc098a2ed421053ed35ba6bb0b567c36ed16d0f13de38d` |

실제 HTML에서 부트스트랩 값 부분을 제거한 SHA-256은 변경 전후 모두 `86ff53c0e280c76be66f7ddf9d1545b51c162927808b769f85138f30e19207da`였다. 기존 부트스트랩 필드의 값도 모두 일치했다. `app/static`과 `proofs`는 이전 커밋의 트리 그대로다. 기존 T13 교정본의 ID `8608ffbd499bb6298c9a28fc9feb0c85`를 재생해 후보 JSONL의 모든 값과 PDF의 모든 글자 좌표가 일치함을 확인했다.

양식·조판·초기 설정 회귀 명령 `t11 pytest -q --tb=line tests/test_publication.py tests/test_mixed_layout.py tests/test_operations.py`는 82건 모두 통과했다. 초기 설정 키 집합 검사에는 이 브랜치의 T7 필드와 새 문서 전용 필드를 반영했다.

최종 `t11 pytest -q`는 664 통과, 48 건너뜀, 408.15초였다. 별도 서비스 이미지가 필요한 인수 중 위 좌표 및 모드 검사는 호스트에서 실행했다. 모드 필드와 수집 요청을 생략하고 자간 범위만 −7~0%로 준 29.8mm 문단에서도 PDF에서 첫 줄 −3.2%, 마지막 줄 0%를 실측했다.
