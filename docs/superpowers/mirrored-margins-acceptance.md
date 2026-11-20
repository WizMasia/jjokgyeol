# 양면·제본 여백 검증

폭 128mm인 보고서에 안쪽 12mm, 제본 6mm, 바깥쪽 10mm, 위·아래 15mm를 입력했다. 안쪽의 실효 여백은 18mm이고 본문 폭은 모든 면에서 100mm다. 원고 중간에 열 문단을 추가하자 추적 문장이 9쪽에서 10쪽으로 옮겨갔다. [최종 편집기](http://127.0.0.1:8768/) · [9쪽과 10쪽 비교 PDF](../../proofs/mirrored-margins/pages-9-10.pdf)

| 실제 PDF에서 측정한 항목 | 추가 전 | 추가 후 |
|---|---|---|
| 문서 쪽수와 추적 문장 위치 | 11쪽 중 9쪽 | 12쪽 중 10쪽 |
| 본문 왼쪽 / 오른쪽 여백 | 18mm / 10mm | 10mm / 18mm |
| 추적 문장 첫 글자의 왼쪽 끝 | 51.0240 PDF pt | 28.3460 PDF pt |
| 바깥쪽 번호의 바깥 모서리 | 오른쪽 ‘9’, 334.4878 PDF pt | 왼쪽 ‘10’, 28.3460 PDF pt |

표 좌표는 종이 왼쪽부터 잰 값이다. Gibbs가 브라우저에서 원고와 양식을 열고 내려받은 PDF를 미리보기와 SHA-256으로 대조했다. 23쪽의 본문·각주 글리프 4,595개를 기존 고정 14mm 교정본과 비교해 줄바꿈, 쪽 나눔, 세로 위치를 확인했다. 본문과 머리글·바닥글의 수평 이동은 홀수 면 +4mm, 짝수 면 −4mm였고 최대 오차는 0.010542 PDF pt였다. [추가 전 PDF](../../proofs/mirrored-margins/before.pdf) · [추가 후 PDF](../../proofs/mirrored-margins/after.pdf) · [쪽별 좌표와 해시](../../proofs/mirrored-margins/evidence.json)

**양식 열기**로 예제 양식을 열고 두 원고 중 하나를 불러온 뒤 **미리보기 갱신**을 누르면 재현할 수 있다. 제본 6mm는 양식에서 정수 1,118,808sp로 저장된다. 명시적 제본 0인 양면 파일과 기존 비대칭 고정 파일을 비교해 짝수 시작점이 다름을 확인했다. 바깥 번호는 표지의 숨김 이후 쪽마다 한 번씩 인쇄됐다. [추가 전 원고](../../proofs/running-heads/before.md) · [추가 후 원고](../../proofs/running-heads/after.md) · [예제 양식](../../proofs/mirrored-margins/style.json)

본문 폭을 전부 쓰는 제본 여백을 입력하면 갱신·양식 저장·PDF 다운로드에서 오류가 발생하고 직전 미리보기가 보존됐다. 양면에서 참고 DOCX의 물리적 여백은 고정 모드에서 적용 가능하다는 안내가 표시됐고, 같은 파일의 머리글은 현재 양면 지면에 적용됐다. [브라우저 인수 로그](../../proofs/mirrored-margins/acceptance.log)

## 최종 실행

Linux ARM64의 `jjokgyeol-mirrored-release` 컨테이너에서 소스 마운트 없이 실행했다. 배포본과 오프라인 테스트 이미지의 앱 파일 32개 해시가 작업공간과 일치했다.

| 검사 | 결과 |
|---|---|
| 네트워크를 끈 Docker 전체 회귀 4개 묶음 | 610건 통과 · 107.26초 |
| 신규 및 기존 양면 기능의 독립 인수 | 15건 통과 · 146.69초 |
| 별도 Docker 서비스에서 설치·설정·목록·조 제목 검증 | 14건 통과 · 117.22초 |
| 기존 머리글 및 참고 문서 호스트 회귀 | 18건 통과 · 158.13초 |

오프라인 실행에서 건너뛴 호스트 전용 47건은 호스트 실행에서 검증했다. 기존 머리글·참고 인수는 8769의 동일 이미지 임시 서비스에서 실행해 8768의 독립 브라우저 검사와 조판 슬롯을 분리했다.

```sh
export JJOKGYEOL_TEST_IMAGE=sha256:8ff58f162c1710573a108749e90ebb9a901fe83f561726ef43ce96b0dd651e17
export JJOKGYEOL_REFERENCE_URL=http://127.0.0.1:8768
python -m pytest -q tests/test_mirrored_margin_acceptance.py tests/test_duplex_acceptance.py
python -m pytest -q tests/test_slice.py::test_deployment_parity tests/test_refactor_acceptance.py tests/test_article_acceptance.py tests/test_list_acceptance.py tests/test_running_heads_acceptance.py tests/test_reference_acceptance.py
docker run --rm --pull=never --network none --shm-size=512m sha256:285585be4a46449a1220d676331c17807801e6a9f1736ead3a73de94e5887954 pytest -q
```
