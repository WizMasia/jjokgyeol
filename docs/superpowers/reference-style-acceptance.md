# 참고 문서 서식 가져오기 독립 인수

2026-11-19 최종 배포 서비스는 Linux ARM64 `sha256:6e170961bf6ecfd52c2062d1e29d34173f3acebf485112cde15659a4fee99525`이다. 실행 중인 8765 컨테이너의 이미지 ID와 빈 마운트 목록을 확인했고, 작업공간과 컨테이너의 앱 파일 30개를 SHA-256으로 대조해 모두 일치했다. [현재 편집기 열기 · http://127.0.0.1:8765/](http://127.0.0.1:8765/)

배포 담당자가 실행한 최종 로그와 다운로드 파일을 독립 대조했다. `v1-host.log`에서 **호스트 22건이 176.60초에 통과**했다. 여기에는 실제 LM Studio와 Chromium을 사용하는 참고 서식 인수 9건이 포함된다. `v1-suite.log`에서 네트워크를 끈 테스트 이미지 `sha256:ad0ea9758d56a367c4cb9a9959f0f640148fd005493c2fc8a90524250cdffa2c`는 **429건 통과, 호스트 전용 22건 건너뜀**으로 209.48초에 종료했다. 별도 마감시간 검사는 담당자가 즉시 업로드와 1.25초 지연 업로드 모두에서 HTTP 200과 첫 쪽 판독값 보존을 확인했다.

배포 서비스에서 내려받은 DOCX 적용 PDF는 SHA-256 `2d9e025bb4bce68d4af7162940f4dd14755e96d2adf8368f4e067d9a7625b2e7`로 시험 조판·미리보기·다운로드 기록과 일치했다. PDF를 다시 읽은 본문 글리프는 **13.94770 PDF pt**였다. DOCX·HWPX 결과와 저장 양식 재개방 결과의 전체 지면 지문은 모두 `76c40bd7050e7366d230e53105d7188c37eddc3bb60e16b11c1403e0f9396b93`이었다. [8765 최종 배포 PDF](../../proofs/reference-style/release-after.pdf) · [배포본과 바이트가 같은 저장 양식](../../proofs/reference-style/style.json)

## 8768 후보에서 보관한 증빙

아래 표의 PDF와 모델 로그는 앞선 8768 후보 `sha256:718ee99aa8b0270c49e8fe9d8fa828045f4784787aafc516aaa621707cd0e5bf`에서 직접 수집했다. 이 후보는 Chromium 독립 인수 9건을 88.58초에 통과했다.

| 사용자 경로 | 실측 결과 |
|---|---|
| Office 본문만 적용 | DOCX와 HWPX에서 직접 지정한 11pt를 읽고 14pt로 수정했다. 대상 ‘들판 기록 › 모래톱’의 본문 첫 글리프는 10.95890 → **13.94770 PDF pt**였다. 표제는 17.53425 PDF pt, 다른 구간 본문과 목록은 10.95890 PDF pt를 유지했다. 양쪽 형식의 결과에서 쪽·줄·전체 글리프 지문이 같았다. [적용 PDF](../../proofs/reference-style/after.pdf) · [저장 양식](../../proofs/reference-style/style.json) |
| 취소·검증 실패·재개방 | 시험 조판 뒤 취소했을 때 원고·양식·기존 PDF 바이트가 보존됐다. 음수 들여쓰기는 적용할 수 없었고 기존 PDF를 유지했다. 저장 양식에는 선택한 경로의 `font_sp: 917504`만 추가됐다. 예전 양식을 거쳐 다시 열어도 적용 직후의 글자 크기와 줄바꿈이 재현됐다. 참고 기관명·문구·이미지는 결과 PDF에서 발견되지 않았다. |
| 범위 변경·목록 재연결 | 참고 범위를 6번째 문단으로 좁히고 1단계 들여쓰기만 골랐다. 대상 목록 본문 시작점은 67.10290 → **85.10290 PDF pt**로 이동했다. 다른 목록은 67.10290에 머물렀다. 앞에 30문단을 넣어 목록이 1쪽에서 2쪽으로 밀려도 같은 규칙이 적용됐다. 제목 경로 중복·누락 시에는 적용이 풀리고, ‘물안개’로 다시 연결하자 18.00000 PDF pt 이동이 복원됐다. [목록 적용 PDF](../../proofs/reference-style/list-applied.pdf) |
| 텍스트 PDF | 1쪽 표지, 2쪽 ‘차례’를 목차, 3쪽 본문으로 판독했다. 3쪽 판형 후보는 148.167 × 209.889 mm, 본문은 11.041 TeX pt였다. 첫 목록 표지의 오른쪽 끝과 본문 시작점 사이 1.164 mm를 독립 좌표와 대조했다. |
| PNG와 혼합 PDF | LM Studio의 `google/gemma-4-e2b`에 합성 지면을 보냈다. PNG는 기준 폭 입력 전 환산 칸이 비었고, 혼합 PDF의 스캔 쪽은 파일 판형으로 자동 보정됐다. 수동 폭 148 mm에서 본문 추정은 각각 249/10000 → **10.48541 TeX pt**, 235/10000 → **9.89587 TeX pt**였다. 해당 값을 적용한 글리프는 각각 10.44624, 9.85890 PDF pt였다. [PNG 적용 PDF](../../proofs/reference-style/scan-applied.pdf) · [혼합 PDF 적용 결과](../../proofs/reference-style/mixed-applied.pdf) |
| 부분 성공·재시도 | 혼합 PDF의 첫 쪽은 모델 미연결 상태에서도 후보 16개를 보존했고 둘째 쪽에 경고가 표시됐다. 재시도 요청은 둘째 쪽만 지정했다. 연결된 서비스에서도 같은 쪽만 재분석해 성공했고 첫 쪽의 판독값은 그대로였다. 실제 모델 서버 로그에는 15:33:19, 15:33:30, 15:33:32 KST에 각각 요청이 들어와 예측을 반환한 기록이 있다. |

적용 순간의 시험 조판 HTTP 응답·시험 미리보기·확정 미리보기·내려받은 파일을 대조했다. 저장한 DOCX 적용 PDF의 SHA-256은 `9ac38f792784c3d2d13c580f9a240bbe9517525338cc43727e9f5e9ffe71eb3a`다. DOCX·HWPX 적용 및 양식 재개방 후 지면 지문은 모두 `76c40bd7050e7366d230e53105d7188c37eddc3bb60e16b11c1403e0f9396b93`였다.

## 독립 표본과 기준값

참고 문서는 별자리 관측을, [대상 원고](../../proofs/reference-style/target.md)는 강가의 새와 식물을 다룬다. 모두 이 검증을 위해 작성했다.

| 표본 | 독립 대조값 |
|---|---|
| [DOCX](../../proofs/reference-style/reference.docx) · [HWPX](../../proofs/reference-style/reference.hwpx) | 표제 24pt, 차례 본문 13pt, ‘잔별수첩’ 본문 직접 지정 11pt. 7문단 중 6·7번째는 번호 목록이며 1단계 표지 들여쓰기는 6.35mm다. HWPX는 한글의 빈 문서 서식에 합성 문단을 넣었다. |
| [텍스트 PDF](../../proofs/reference-style/reference.pdf) | 나눔 글꼴을 내장한 표지·차례·본문 3쪽. PDFMiner로 읽은 판형은 420.000 × 594.960 PDF pt, 표제 24.000, 본문 10.995 PDF pt다. |
| [스캔 PNG](../../proofs/reference-style/scan.png) · [혼합 PDF](../../proofs/reference-style/mixed.pdf) | 같은 본문 쪽을 래스터화한 840 × 1190 RGB 이미지다. PNG에는 실제 크기를 나타내는 메타데이터가 없다. 혼합 PDF 1쪽은 원래 글자, 2쪽은 같은 지면 이미지 한 장이며 텍스트층이 없다. |
| [변경 전 PDF](../../proofs/reference-style/before.pdf) | 기능 추가 전 8765에서 Chromium으로 내려받은 원고다. 페이지·줄·전체 글리프의 글꼴·크기·좌표를 포함한 SHA-256은 `4ea8b4df9b6b57034134416c37bead5099976278f5580386cf5980fcb0418f8c`다. |

기존 8765 이미지 `2c4280a611ba`에서 설정 API가 404를 반환하고 가져오기 버튼이 없어 최초 브라우저/API 검사 2건이 모두 실패했다. 중간 후보는 같은 PNG의 본문을 0.4211 TeX pt로 추정했고 ‘차례’를 본문으로 분류했다. 두 사례를 같은 입력으로 재검증했다.

PDF 글리프 기대값은 편집기의 TeX pt에 `72 / 72.27`을 곱해 구했다. 스캔에는 깨끗한 합성 지면에 한해 원본 본문 크기의 ±20%를 허용했다. 실물 촬영, 비뚤어지거나 흐린 스캔, 다른 비전 모델의 정확도는 이번 인수 범위 밖이다.

## 재현

운영자 모델이 연결된 루프백 서비스 주소와 동일한 Docker 이미지 ID를 지정한다. 테스트는 참조 파일을 프로젝트에서 읽으며, 모델 미연결 검증용 컨테이너를 별도 임시 포트로 띄운다. [독립 인수 테스트](../../tests/test_reference_acceptance.py)

```sh
export JJOKGYEOL_REFERENCE_URL=http://127.0.0.1:8765
export JJOKGYEOL_TEST_IMAGE=sha256:6e170961bf6ecfd52c2062d1e29d34173f3acebf485112cde15659a4fee99525
export PLAYWRIGHT_BROWSERS_PATH=/private/tmp/jjokgyeol-dev/browsers
/private/tmp/jjokgyeol-dev/venv/bin/python -m pytest -q -s tests/test_reference_acceptance.py
```
