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

기존 기본 양식의 전체 PDF 글자 좌표 해시는 그대로다. 가변 공백을 저장한 `proofs/web-editor/fixed-point-style.json`은 2쪽·23줄을 유지하고 두 문단의 줄 끝이 달라졌다. 기존 `test_prechange_geometry[saved]`의 전환 전 해시와 새 해시는 각각 `1666f2dc…`, `6b095dac…`이다. 상위 통합에서 이 지면 변경을 검토한다.

## 실행 기록

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

전체 시험에서 실패한 `test_configured_limits_reach_requests_and_worker`는 조판기를 대역으로 교체한다. 편집기 초기 설정 키 집합을 검사하는 101행에서 T7의 신규 키와 기대값이 다르다. 아래 명령으로 T11 직전 코드에서도 동일 실패 1건을 확인했다.

```sh
git archive bd86ec4 | docker run --rm -i --network none --shm-size=512m \
  -e PYTHONPATH=/tmp/t11-base -w /tmp jjokgyeol:mirrored-margins-test \
  sh -c 'mkdir t11-base && cd t11-base && tar -xf - && pytest -q tests/test_operations.py::test_configured_limits_reach_requests_and_worker'
```
