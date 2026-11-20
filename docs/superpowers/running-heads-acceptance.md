# 머리글·바닥글 출력 검증

로컬 편집기의 최종 배포 이미지는 Linux ARM64 `sha256:755dbe0f266a5030cad3efd7b2fa464a260d6013cbe9f9e8d69d42f11bec046e`이다. 컨테이너 `jjokgyeol-running-heads-release`의 마운트 목록은 비어 있고, 설치된 앱 파일 32개의 SHA-256이 작업공간과 일치했다. 이번 기능을 같은 호스트의 8766 포트에 배포했다. [편집기 열기](http://127.0.0.1:8766/) · [추가 전 PDF](../../proofs/running-heads/before.pdf) · [추가 후 PDF](../../proofs/running-heads/after.pdf) · [브라우저에서 저장한 양식](../../proofs/running-heads/style.json)

## 원고를 늘렸을 때

두 원고는 ‘보충1’부터 ‘보충10’까지 열 문단만 다르다. 기존 배포본에서 만든 기준 PDF를 고정한 다음, 같은 원고에 머리글과 바닥글을 적용했다. PDF에서 위·아래 여백과 본문을 좌표로 나누고, 쪽마다 본문과 각주 글자의 위치·폭·크기·글꼴을 기준 PDF와 비교했다. [추가 전 원고](../../proofs/running-heads/before.md) · [추가 후 원고](../../proofs/running-heads/after.md) · [쪽별 기대값](../../proofs/running-heads/expected.json)

| 실제 출력에서 찾은 내용 | 추가 전 | 추가 후 |
|---|---|---|
| 표지 | 1쪽, 위·아래 문구 숨김 | 1쪽, 위·아래 문구 숨김 |
| ‘추적문장 파도 끝에서 푸른 유리 조각을 찾았다.’ | 9쪽, 바닥글 `－ 9 －` | 10쪽, 바닥글 `－ 10 －` |
| ‘부록기록’ 머리글 | 10쪽 | 11쪽 |
| ‘판독표기록’ 머리글 | 11쪽 | 12쪽 |
| 기존 PDF와 본문·각주 글리프 좌표가 같은 쪽 | 11/11 | 12/12 |

Gibbs가 최종 컨테이너에서 Chromium으로 두 PDF를 내려받았다. 추가 전 본문·각주 2,192글자와 추가 후 2,403글자의 좌표가 각각 기준 PDF와 일치했다. 제목이 쪽 중간에 오는 경우, 제목이 쪽 첫 줄인 경우, 각주만 이어지는 마지막 쪽도 따로 검사했다. 시험 결과와 확정 미리보기·다운로드가 같은 PDF 바이트인지 확인했고, 양식을 저장했다가 다시 열어 재조판한 결과는 글자의 배치로 대조했다. [기준 PDF 11쪽](../../proofs/running-heads/baseline-before.pdf) · [기준 PDF 12쪽](../../proofs/running-heads/baseline-after.pdf) · [검사 결과와 다운로드 해시](../../proofs/running-heads/evidence.json)

## 참고 문구와 출판 설정

- 실제 분석 API에서 DOCX·HWPX·텍스트 PDF의 `COAST 2026`은 고정 문구, `- {현재쪽} -`은 번호 필드로 읽혔다. 머리글을 ‘올해 관측소’로 고친 뒤 PDF에서 ‘올해 관측소’, 원고 ‘올해의 원고’, 바닥글 ‘- 1 -’을 각각 확인했다. 가져오기 전후 참고 파일 바이트가 일치했다. [DOCX](../../proofs/running-heads/reference.docx) · [HWPX](../../proofs/running-heads/reference.hwpx) · [텍스트 PDF](../../proofs/running-heads/reference.pdf)
- 별도 설치 사본에서 `publication.json`의 `running_defaults.font_pt`만 9에서 12.5로 바꿨다. 첫 HTML에 포함된 편집기 초기값은 12.5였다. 명시적 크기를 생략한 양식을 API에서 저장하고 다시 열어 조판한 결과, 머리글은 12.45330 PDF pt, 본문은 10.95890 PDF pt였다. 각각 TeX 12.5pt와 11pt에 해당한다. [설정 전파 검사](../../tests/test_refactor_acceptance.py)
- 최종 브라우저에서는 같은 네 형식의 기관명을 ‘파도기록실’로 고쳤다. 스캔은 실제 로컬 LM Studio의 `google/gemma-4-e2b`가 판독했고, 저자가 머리글 연도는 고정 숫자로, 바닥글의 `17`은 자동 쪽 번호로 선택했다. 네 PDF 모두 위에는 ‘파도기록실’, 아래에는 ‘제1면’이 인쇄됐다. [DOCX 적용 PDF](../../proofs/running-heads/from-docx.pdf) · [HWPX 적용 PDF](../../proofs/running-heads/from-hwpx.pdf) · [텍스트 PDF 적용 결과](../../proofs/running-heads/from-pdf.pdf) · [스캔 적용 PDF](../../proofs/running-heads/from-png.pdf)
- 9쪽까지 맞던 바닥글이 10쪽에서 겹치는 원고는 시험 조판과 다운로드 모두 HTTP 422 및 ‘10쪽 바닥글’ 오류를 반환했다. 기존 미리보기와 작업 중인 원고·양식은 보존됐다. 본문이 비어 있는 DOCX·HWPX 표지의 전용 머리글과 DOCX 줄바꿈 방지 하이픈(U+2011)도 회귀 표본에 포함했다.

## 최종 이미지 검증

| 실행 위치 | 검증 범위 | 결과 |
|---|---|---|
| 네트워크를 끈 Linux ARM64 테스트 이미지 | 전체 pytest 회귀 | 506건 통과, 호스트에서 실행할 32건 건너뜀 · 251.63초 |
| 배포 이미지와 Chromium, 실제 LM Studio | 새 기능·참고 문서 인수 | 18건 통과 · 156.87초 |
| 배포 이미지로 띄운 임시 로컬 서비스 | 설치 방식 일치, 설정 교체, 기존 조·목록 | 14건 통과 · 116.83초 |

재현 명령은 쪽결의 호스트 테스트 환경에서 실행한다. 브라우저와 로컬 모델을 준비하고 `JJOKGYEOL_REFERENCE_URL`에는 실행 중인 배포 서비스의 주소를 지정한다.

```sh
docker run --rm --pull=never --network none --shm-size=512m sha256:70da980d2cdba5ae55dbd83977f89c23b73d0031beb48fc83831cd8f15020fc0 pytest -q
export JJOKGYEOL_TEST_IMAGE=sha256:755dbe0f266a5030cad3efd7b2fa464a260d6013cbe9f9e8d69d42f11bec046e
export JJOKGYEOL_REFERENCE_URL=http://127.0.0.1:8766
python -m pytest -q tests/test_running_heads_acceptance.py tests/test_reference_acceptance.py
python -m pytest -q tests/test_slice.py::test_deployment_parity tests/test_refactor_acceptance.py tests/test_article_acceptance.py tests/test_list_acceptance.py
```
