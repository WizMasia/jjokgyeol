# 혼합 문단 교정 계획

기준 커밋 `425a940`. 승인된 세 재현을 실제 PDF로 검증한다.

1. `tests/test_decision.py`에서 U+3000으로 시작하는 혼합 문단의 후보를 재생한다. PDF에서 읽은 비공백 줄 끝과 첫 한글 좌표를 검사한다.
2. `tests/test_gloss_layout.py`에서 55×48 mm 판형, 첫 줄 들여쓰기 23 mm를 사용해 제목과 풀이의 쪽을 검사한다. 본문 폭 17 mm에서 연속 공백을 둔 인용구와 풀이 묶음의 줄 경계도 검사한다.
3. 세 실패를 확인한 후 `paragraph.lua`의 글리프 집계, `layout.lua`의 빈 줄 뒤 쪽 나눔, `wordbreak.lua` 폴백의 공백 판정을 고친다.
4. 오프라인 컨테이너에서 재현 테스트와 기존 문단·풀이·어절·결정 테스트를 실행하고 변경 파일과 커밋 SHA를 확인한다.

## 재현 명령

작업 트리 루트에서 실행한다. 첫 명령은 수정 전 네 사례 모두 실패했고 같은 명령으로 수정 후 네 사례 모두 통과했다. 제목 사례는 기존·실측 양식에서 각각 검사한다.

```sh
docker run --rm --network none --shm-size=512m -v "$PWD:/opt/jjokgyeol:ro" jjokgyeol:roadmap-test pytest -q \
  'tests/test_decision.py::test_choice_reaches_pdf[ideographic]' \
  tests/test_gloss_layout.py::test_heading_follows_gloss_to_next_page \
  tests/test_gloss_layout.py::test_gloss_fallback_keeps_quotes_across_literal_spaces
```

기존 메모·조 번호 검사는 `425a940` 원본에서도 실패했다. T7 각주와 T9 메모 수정이 합쳐진 `e7cce37`을 병합해 최종 검증한다.

```sh
docker run --rm --network none --shm-size=512m -v "$PWD:/opt/jjokgyeol:ro" jjokgyeol:roadmap-test pytest -q \
  tests/test_decision.py tests/test_gloss_layout.py tests/test_mixed_layout.py tests/test_breaks.py \
  tests/test_typeset.py::test_ideographic_indent_moves_first_hangul \
  tests/test_list_typeset.py::test_mark_stays_with_text_across_pages_and_continued_paragraph
```

T7 각주 표지 회귀 두 건이 통과했다.

```sh
docker run --rm --network none --shm-size=512m -v "$PWD:/opt/jjokgyeol:ro" jjokgyeol:roadmap-test pytest -q \
  tests/test_publication.py::test_unused_heading_style_preserves_footnote_geometry \
  tests/test_publication.py::test_footnote_marks_follow_installed_weight_and_slant
```

기본 양식과 저장 양식으로 기존 교정지를 출력해 각 지면의 두 해시를 대조했다. 네 값이 모두 `BASELINES`와 일치했다.

```sh
docker run --rm -i --network none --shm-size=512m -e PYTHONPATH=/opt/jjokgyeol:/opt/jjokgyeol/tests -v "$PWD:/opt/jjokgyeol:ro" jjokgyeol:roadmap-test python - <<'PY'
from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import closing
from test_slice import server_url
from test_refactor_acceptance import BASELINES, test_prechange_geometry
with closing(server_url.__wrapped__()) as service, TemporaryDirectory() as tmp:
    url = next(service)
    for name in BASELINES:
        test_prechange_geometry((None, url), name, Path(tmp))
PY
```
