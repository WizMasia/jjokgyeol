# 앱 리팩터링 인수 검증

2026-11-19, Linux ARM64. 이전 서비스와 후보 서비스를 각각 임시 루프백 포트에서 실행하고 실제 HTTP 응답과 Chromium 다운로드를 측정했다.

| 이미지 | 불변 ID |
|---|---|
| 이전 서비스 | `sha256:60b2ba7d074f1379d715618dc7d9dd8a0a428074b0092242e41ae07172d9be36` |
| 최종 서비스 | `sha256:77f395924bdf189a33ac75da753935920b14bca819286906eeda14e8bd8acefe` |
| 최종 오프라인 테스트 | `sha256:09e0b7b5912b5522f0d179559767a9a7a2d8284963042706b8bd1aac384f99bb` |

## 기존 PDF와의 대조

교정 원고를 빈 양식과 저장 양식으로 각각 출력했다. 저장 양식은 실제 열기 API로 읽고 내보내기 API로 원래 정수 필드 전체가 같은지 확인했다. [교정 원고](../../proofs/web-editor/manuscript.md) · [저장 양식](../../proofs/web-editor/fixed-point-style.json)

| 양식 | 쪽별 줄 수 | 쪽별 문자 수 | 이전·후보 공통 지면 SHA-256 |
|---|---|---|---|
| 기본 11 pt | 19 | 452 | `28208635caf3ebe7f6ff314c0e8019bd897a60b88fa1428870cb2ec748db9327` |
| 저장 14 pt | 21, 2 | 422, 30 | `de354c6cbea36e9045f358525a607a25054ac212a99bf60fb446aeb4f6a4be79` |

후보에서 추출한 지면 JSON은 두 경우 모두 설계 당시 보관한 JSON과 바이트 단위로 같았다. 보관한 PDF와 후보 PDF에서도 쪽 크기, 줄별 문자열, 모든 글자의 글꼴·크기·경계 상자·기준선·진행 폭이 정확히 일치했다. 두 양식 모두 각주 본문의 첫 글자 ‘길’은 1쪽의 x=65.370, 기준선 y=56.693 PDF pt에 있다. [설계의 기준 서명](specs/2026-11-19-app-refactor-design.md)

## 기본 글자 크기 한 항목 변경

후보 컨테이너에 설치된 `app/config/publication.json`을 복사해 `defaults.body_pt`의 `11`을 `15`로 바꿨다. 달라진 바이트는 하나다. 같은 이미지로 새 서버를 시작하고 수정한 파일 하나만 읽기 전용으로 마운트했다. 이전 이미지에 같은 15 pt 설정을 마운트한 대조군에서는 브라우저가 11, 빈 양식 내보내기가 720896 sp, PDF 본문이 10.95890 PDF pt였다.

| 측정 경로 | 배포 기본값 | 변경 후 |
|---|---:|---:|
| 최초 GET의 설정 / 실제 본문 입력창 (TeX pt) | 11 / 11 | 15 / 15 |
| 빈 양식 내보내기 (sp) / 다시 열기 (TeX pt) | 720896 / 11 | 983040 / 15 |
| 빈 양식으로 조판한 ‘본문’의 글자 크기 (PDF pt) | 10.95890 | 14.94396 |
| 브라우저 첫 미리보기와 다운로드에서 일치한 SHA-256 | `54915873107be3636e39d46ad262b54269bdd17388cb962cfa0f6856a79f821d` | `1ad791017db9ba1970c6f96949eeb3a4724b50220be83abe62d12e080acb8bbf` |

Chromium에서 실제 조판 응답과 표시 중인 iframe의 PDF, 다운로드 파일 세 곳의 바이트를 대조했다. 표의 PDF 파일 해시는 이 실행에서 받은 파일의 값이다. 15 pt 설치에서도 저장 양식의 명시값 14 pt와 저장 필드 전체를 보존했다.

Nash는 최종 서비스의 오프라인 임시 컨테이너에서 설치 설정의 공백 범위를 [70, 150]%로 바꾸고 ‘가 나’ PDF의 100%·150% 공백을 각각 3.276711·4.920546 PDF pt로 실측했으며, 최종 소스에서 표현 한계를 넘는 공백은 조판·양식 저장·열기 모두 HTTP 422를 반환하고 부적합한 기본값·제목 배율·행간 배율은 시작 시 거부됨을 확인했다.

## 재현 명령

Docker 호스트에서 프로젝트 루트를 기준으로 실행한다. 호스트 검사는 서비스 이미지에 코드나 원고 디렉터리를 마운트하지 않고 HTTP로 원고를 보낸다.

```sh
export JJOKGYEOL_TEST_IMAGE=sha256:77f395924bdf189a33ac75da753935920b14bca819286906eeda14e8bd8acefe
export PLAYWRIGHT_BROWSERS_PATH=/private/tmp/jjokgyeol-dev/browsers
/private/tmp/jjokgyeol-dev/venv/bin/python -m pytest -q -s tests/test_refactor_acceptance.py tests/test_slice.py::test_deployment_parity
docker run --rm --pull=never --network none sha256:09e0b7b5912b5522f0d179559767a9a7a2d8284963042706b8bd1aac384f99bb pytest -q --tb=short
```

최종 오프라인 테스트 컨테이너는 네트워크 `none`, 마운트 0개에서 104.20초 동안 186개 통과, 호스트 전용 4개 건너뜀을 기록했다. 해당 4개는 최종 서비스 이미지로 호스트에서 별도 실행해 19.83초에 모두 통과했다. 두 독립 컨테이너의 지면을 대조하는 검사도 이 수에 포함되며, 배포 담당자가 별도로 반복한 같은 검사도 5.11초에 통과했다.
