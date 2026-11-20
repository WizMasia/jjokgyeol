# 양면 머리글·바닥글 인수 결과

같은 문장 ‘추적문장 파도 끝에서 푸른 유리 조각을 찾았다.’를 브라우저 미리보기와 다운로드 PDF에서 찾아 실제 쪽과 번호 위치를 확인했다. 열 문단을 추가하자 문장에 붙은 각주도 함께 밀렸고, 바닥글 번호는 새 쪽의 바깥쪽에 한 번만 인쇄됐다. [9쪽과 10쪽 한 장 비교](../../proofs/duplex-running-heads/pages-9-10.pdf) · [11쪽 PDF](../../proofs/duplex-running-heads/before.pdf) · [12쪽 PDF](../../proofs/duplex-running-heads/after.pdf)

| 실제 PDF에서 측정한 값 | 추가 전 | 열 문단 추가 후 |
|---|---|---|
| 전체 쪽수 | 11 | 12 |
| 추적 문장이 실린 쪽과 머리글 | 9쪽 · 홀수연안 | 10쪽 · 짝수연안 |
| 바닥글의 유일한 번호 | 오른쪽 `9` | 왼쪽 `10` |
| 번호의 바깥 모서리, 종이 왼쪽부터 | 323.1498 PDF pt | 39.6850 PDF pt |
| 단면 기준과 위치·폭·크기·글꼴이 같은 본문 및 각주 글자 | 2,192 | 2,403 |

판형은 128 × 100mm, 좌우 여백은 14mm다. 번호 오른쪽 끝을 114mm, 왼쪽 끝을 14mm에 대조했고 허용 오차는 0.04 PDF pt였다. 여백을 왼쪽 11mm, 오른쪽 17mm로 따로 지정한 배포 API 검사에서도 9쪽의 오른쪽 끝은 314.646 PDF pt, 10쪽의 왼쪽 끝은 31.181 PDF pt였다. [쪽별 좌표 및 미리보기·다운로드 SHA-256](../../proofs/duplex-running-heads/evidence.json)

편집기에서 아래 원고와 양식을 각각 열고 **미리보기 갱신**을 누르면 재현할 수 있다. 양식을 저장했다가 다시 열어 같은 글자 배치를 확인했다. 예전 단면 양식을 열면 번호가 다시 가운데에 인쇄된다. [로컬 편집기](http://127.0.0.1:8767/) · [추가 전 원고](../../proofs/running-heads/before.md) · [추가 후 원고](../../proofs/running-heads/after.md) · [양면 양식](../../proofs/duplex-running-heads/style.json)

## 표지·제목 구간·각주

- 문서 첫 쪽은 두 원고 모두 전용 머리글 ‘표지연안’을 쓰고 번호를 숨겼다. 2쪽에는 짝수 머리글과 왼쪽 번호 2가 인쇄됐다.
- ‘부록’ 제목은 추적 문장과 같은 쪽 중간에 있다. 부록 본문이 처음으로 쪽 맨 위에 온 면은 추가 전 10쪽, 추가 후 11쪽이다. 그 면만 머리글을 숨기고 바닥글은 각각 왼쪽 10과 오른쪽 11을 유지했다.
- 상위 제목과 하위 제목의 첫 면이 겹친 원고에서는 두 규칙을 함께 썼고, 다음 면에는 일반 홀짝 양식으로 돌아왔다. 각주만 이어진 2~5쪽에는 직전 구간의 홀짝 양식을 쓰고, 6쪽에 본문이 재개될 때 해당 구간의 첫 면을 사용했다. [쪽 나눔 회귀 검사](../../tests/test_duplex_typeset.py)
- 짝수 바닥글의 문구가 두 자리 쪽 번호와 겹치는 표본은 9쪽까지 정상 출력됐다. 10쪽에서 시험 조판이 HTTP 422와 ‘10쪽 바닥글’을 반환했고, 편집기에는 직전 정상 PDF가 남았다. [독립 브라우저 검사](../../tests/test_duplex_acceptance.py)

## 참고 문서로부터 가져오기

| 참고 입력 | 편집하고 실제 출력에서 확인한 결과 |
|---|---|
| [DOCX](../../proofs/duplex-running-heads/banks.docx) | 서로 다른 머리글을 ‘동녘연안’과 ‘서녘연안’으로 고쳐 각각 홀수·짝수 쪽에 적용했다. 원본의 빈 첫 면 바닥글을 선택하자 표지의 기존 가운데 번호가 사라지고 2쪽 왼쪽 번호는 유지됐다. [출력 PDF](../../proofs/duplex-running-heads/from-docx.pdf) |
| [HWPX](../../proofs/duplex-running-heads/outer.hwpx) | 바깥쪽 쪽 번호 제어에서 읽은 두 후보를 함께 선택해 `항{현재쪽}`으로 고쳤다. 매 쪽의 왼쪽·오른쪽 경계에 번갈아 인쇄됐다. [출력 PDF](../../proofs/duplex-running-heads/from-hwpx.pdf) |
| [텍스트 PDF](../../proofs/duplex-running-heads/banks.pdf) | 반복 위치로 판독한 홀수 머리글의 적용 면을 짝수로 바꿨다. 1쪽을 다시 분석한 뒤에도 편집한 문구와 면이 유지됐다. [출력 PDF](../../proofs/duplex-running-heads/from-pdf.pdf) |
| [단일 스캔](../../proofs/running-heads/scan.png) | 로컬 LM Studio의 `google/gemma-4-e2b`가 읽은 ‘17’에 대해 사용자가 홀수 면과 자동 번호를 각각 선택했다. 출력은 홀수 면에만 ‘홀1면’, ‘홀3면’으로 이어졌다. [출력 PDF](../../proofs/duplex-running-heads/from-png.pdf) |

네 입력 모두 분석 전후의 파일 해시가 같았다. 저자가 고친 문구와 적용 면은 양식에 저장됐고, 원고는 브라우저에서 저장한 마크다운 바이트로 대조했다.

## Docker 배포와 검사 재현

Linux ARM64 서비스 컨테이너 이름은 `jjokgyeol-duplex-release`다. 8767 포트는 루프백에 연결했고, 소스 마운트 없이 컨테이너의 앱 파일 32개가 동결 소스와 SHA-256으로 일치했다. 호스트의 Chromium과 LM Studio를 연결해 아래 인수를 실행한다.

```sh
export JJOKGYEOL_TEST_IMAGE=sha256:99b9d1ad44d874fd6b1d820d18dd6ebaee268a6b345de0d510bb6fab17d47746
export JJOKGYEOL_REFERENCE_URL=http://127.0.0.1:8767
python -m pytest -q tests/test_duplex_acceptance.py tests/test_running_heads_acceptance.py tests/test_reference_acceptance.py
python -m pytest -q tests/test_slice.py::test_deployment_parity tests/test_refactor_acceptance.py tests/test_article_acceptance.py tests/test_list_acceptance.py
docker run --rm --pull=never --network none --shm-size=512m sha256:95bd4f669c75b4efe6a659cd8ee3b3379dc222252e4c99fc8569315c2306e8fd pytest -q
```

| 실행 환경 | 검사 | 최종 결과 |
|---|---|---|
| 네트워크를 끈 테스트 이미지 | 양면·단면 조판, 판독기, 편집기와 기존 회귀 | 571건 통과 · 277.00초 |
| 배포 컨테이너, Chromium, 실제 LM Studio | 양면 인수 9건 및 기존 머리글·참고 판독 인수 18건 | 27건 통과 · 239.49초 |
| 같은 배포 이미지로 실행한 로컬 임시 컨테이너 | 설치 방식, 출판 설정 전파, 조 제목, 목록 | 14건 통과 · 114.32초 |

오프라인 실행에서 건너뛴 호스트 전용 41건을 뒤의 두 실행에서 모두 확인했다. 총 612건이 통과했다. [브라우저 인수 로그](../../proofs/duplex-running-heads/acceptance.log)
