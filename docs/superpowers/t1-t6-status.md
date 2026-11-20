# 쪽결 첫 편집기 인수 기록

Nash 총괄로 T1–T6를 구현하고 2026-11-19에 Linux ARM64 서비스의 최종 인수를 마쳤다. 아래 중간 시험은 진행 순서대로 남겼다.

## 최종 인수

- 실행 중인 서비스 이미지 `sha256:172a3ff7aefb18ffac4d30dfbee84e917fdc2b83de43009804132c7ead53eed0`. [로컬 편집기](http://127.0.0.1:8765/) · [런타임·글꼴 해시](../../runtime.lock)
- 같은 서비스 코드를 담은 테스트 이미지에서 `docker run --rm --network none jjokgyeol:test pytest -q` 결과 69개 통과. 컨테이너 두 개를 시작해야 하는 배포 동등성 검사는 호스트에서 최종 서비스 이미지로 별도 실행해 1개 통과했다. 두 배포의 쪽별 줄, 좌표, 글꼴, 크기가 일치했다.
- 브라우저에서 20,000pt를 입력했을 때 이전 지면에 실패 상태가 표시됐고 API는 `body_pt` 오류와 HTTP 422를 반환했다. 같은 탭에서 16pt로 고치고 다운로드하자 최신 A5 2쪽 PDF가 표시됐다. 전각 공백 하나가 있는 첫 문장은 본문 여백보다 15.146 PDF pt 안쪽에서 시작했다.
- 같은 원고·판형·글꼴·크기에서 미리보기와 다운로드 파일의 SHA-256을 각각 비교했다. [자간 0% PDF](../../proofs/web-editor/tracking-0.pdf)는 `0b4cee637ac21158348b5fa400e339ec6edc92f3e460ea63fb5512c21202ba85`, [자간 −7% PDF](../../proofs/web-editor/tracking-minus-7.pdf)는 `834250ae8719dfe1549ae168ce00e6932865c96267fbeaf0af636109c18c0656`로 각각 일치했다. [공통 마크다운 원고](../../proofs/web-editor/manuscript.md)
- 위 PDF에서 다음 글자의 시작점과 이전 글자의 기본 진행 폭을 비교했다. 본문 ‘오래된’의 두 글자 간격은 0에서 −1.116 PDF pt로, 제목 ‘지도에’는 0에서 −1.785 PDF pt로 바뀌었다. 각 크기의 −7.0%다. 영문 ‘River’ 안쪽의 네 간격은 0.001pt 정밀도에서 같았다.

| Linux 고정 배치 | 대상 문단 줄 수 | 한글 자간 보정 (PDF pt) | 어절 공백 (PDF pt) | 참조와 각주가 실린 쪽 |
|---|---:|---:|---:|---:|
| [A · 자간 0%, 공백 100%](../../proofs/web-editor/layout-A.pdf) | 2 | 0.000 | 3.277–3.288 | 1 |
| [B · 자간 −7%, 공백 90%](../../proofs/web-editor/layout-B.pdf) | 3 | −0.767~−0.756 | 2.948–2.959 | 2 |

## 중계용 작업 배정

이 세션에는 하위 에이전트 생성·메시지 도구가 없어 부모가 다섯 담당의 활성화를 확인했다. A Bernoulli, D Darwin, C Hegel, E Nietzsche, B Galileo가 각각 구현 중이다. 각 담당은 계획의 소유 파일에만 쓰고 테스트 실패 명령·이유 → 성공 명령·결과 → 독립 검토 결과를 인계한다.

|담당|입출력 계약과 인계 대상|
|---|---|
|A · T1|B·C에 `app.document.parse_md(source)`, `app.style.Style()`, `app.style.apply_style(current, patch)`와 필드 오류 예시를 준다.|
|D · T5|C의 `GET /sample.md`를 UTF-8로 기본 표시한다. 편집창·PDF가 화면의 주 요소다. .md 열기/저장과 최신 버전 Blob 공유를 검증한다.|
|C · T4|`app.server:app`, `POST /api/render`의 source/style → PDF, `GET /`과 `GET /sample.md`. 샘플은 기존 비교 원고 한 파일이다.|
|E · T2|동일 이미지에 앱과 기존 비교 원고를 포함하고 고정 글꼴 파일 대응표, PDF 판독기, 이미지 식별자를 제공한다.|
|B · T3|`app.typeset.typeset(doc, style, workdir)`. 운영 API 밖에서만 A/B 고정 후보를 넣는다. 총괄의 두 fixture를 사용하고 T6에 테스트 진입점과 실제 두 PDF의 판독 결과를 인계한다.|

공통 기본값은 `page_mm=[148,210]`, `margins_mm=[20,18,20,18]`, `body_pt=11`, `leading_pt=17`, `body_font="nanum-myeongjo"`, `heading_font="nanum-gothic"`, `ko_tracking_pct=0`, `space_pct=[100,100]`이다. ID와 실제 글꼴 경로의 대응은 런타임이 소유한다. 임시 macOS TeX는 대체 실행 증거이며, 배포 인수는 E의 Linux 이미지로 판정한다.

## 기준 자료와 인수 상태

- 착수 시 SPEC SHA-256 `f21eebc0c979bfff33285c4319241bed69e974b433a2ee5a78914ff54daeefdd`, 계획 `fcd4066023db51997163880a3dba28ab2e3e0b21131c13a5d3560ce0c1a4ce87`, 샘플 원고 `086d9cddee29d4832d44fdd484d1630b883d9dec970b90feffec112d4cf22d74`.
- T6의 `tests/fixtures/page-edge.md`와 `layout-cases.json`은 제품 실행 전 작성했다. 두 후보는 원문이 동일하며 A는 2줄, B는 3줄이다. 마지막 단어의 참조와 각주 본문은 A에서는 1쪽, B에서는 2쪽이 기대값이다. 실제 엔진 출력은 인수 전이다.

### 담당자 인계 요청

- B Galileo에게. T6는 `tests/test_typeset.py`의 테스트 전용 `render_candidate_for_test(source: str, cases: dict, label: str, workdir: Path) -> bytes`를 호출한다. 실제 조판 경로를 사용하고 반환한 PDF로 각 담당의 단언을 따로 돌린다. fixture의 문단 순번은 0부터다. 기대값을 고정해 두었으며 페이지 높이·앞 문단 개수 교정이 필요하면 총괄에 전달한다.
- D Darwin에게. 실브라우저 테스트는 textarea, ‘본문 크기’가 포함된 접근 가능 레이블, ‘미리보기 갱신’·‘PDF 다운로드’, PDF iframe을 사용한다. 동일한 의미의 다른 마크업을 쓰면 DOM 계약을 인계하여 테스트를 맞춘다.
- E Nietzsche에게. T6 배포 인수는 호스트에서 `JJOKGYEOL_TEST_IMAGE`에 로컬 빌드 이미지를 지정해 실행한다. 이미지 ID 하나로 컨테이너 두 개를 루프백의 임의 포트에 띄운다. 컨테이너 HTTP 포트는 8000을 가정하며 달라지면 인계받아 조정한다.

### 첫 RED

공유 Python 3.12에서 `python -m pytest -q -p no:cacheprovider tests/test_slice.py` 결과 1 failed, 2 errors, 1 skipped. T3 후보 렌더 함수와 T4 서버가 아직 없어서 실패했다. 이미지 ID가 필요한 배포 검사는 미실행이다.

실엔진 동시 요청 시험 추가 후 같은 명령은 1 failed, 3 errors, 1 skipped였다. T3가 테스트 함수 계약을 구현했고 현재 실패 지점은 제품 모듈이다. T6는 E의 PDF 판독기를 공유한다. 각 작업의 첫 GREEN 뒤 부모가 다른 담당에게 스펙·품질 순으로 독립 검토를 중계한다.

### macOS 대체 엔진과 브라우저 통합

공유 Python·macOS 임시 TeX·나눔 글꼴·Playwright Chromium으로 T6를 직접 실행했다. 로컬 소켓 권한을 받아 루프백 서버에서 4 passed, 1 skipped (20.78초). 기본 원고가 열린 후 편집한 제목과 16pt가 실제 PDF에 반영되고 뷰어 바이트와 받은 파일 해시가 일치했다. 동시 실제 요청 두 건의 표식이 분리되었다. 긴 원고의 참조와 각주가 11pt의 2쪽에서 16pt의 3쪽으로 함께 이동했다. 배포 이미지 비교는 이 실행에서 제외되었다.

|최종 PDF에서 읽은 값|후보 A|후보 B|
|---|---:|---:|
|대상 문단 줄 수|2|3|
|참조와 각주가 실린 쪽|1|2|
|줄 안 어절 공백 범위 (PDF pt)|3.277–3.288|2.948–2.959|
|한글 음절 사이 기본 advance 대비 보정 (PDF pt)|0.000|0.000|

첫 판독에서 영문 River·Walk 내부 인접 7쌍의 원점 차이도 두 후보에서 0.05 PDF pt 이내로 일치했다. 현재 숫자는 개발용 macOS 실측이다.

### Linux 서비스 이미지 비교

총괄이 로컬 서비스 이미지 `sha256:12bdc07a6419af633294c76f29cd59f86674cdac7def4337c107a7ce9f9e62dc` (linux/arm64)를 두 개의 독립 컨테이너로 시작했다. `JJOKGYEOL_TEST_IMAGE=jjokgyeol:local python -m pytest -q tests/test_slice.py::test_deployment_parity` 결과 1 passed (5.22초). 각 서버가 만든 여러 쪽 PDF에서 쪽별 텍스트 줄·위치·글꼴·크기가 일치했다. 인수용 컨테이너 두 개는 종료했다. 담당 수정이 들어가면 최종 이미지로 재검한다.

### 통합 중 발견 사항

- 중간 Linux 테스트 이미지 `b4b65f913256` 전체 실행은 54 passed, 6 failed, 1 skipped. 뷰어 초기 보기 옵션을 검사하는 T5 다섯 테스트와 가변 공백을 좁혀 한 줄에 넣는 T3 한 테스트가 실패했다. 호스트용 두 컨테이너 검사는 컨테이너 내부에서 건너뛴다.
- B의 최신 파일을 같은 Linux 이미지에 읽기 전용으로 연결해 좁은 판형 공백 회귀를 재실행하니 1 passed였다. D의 최신 화면 확인 및 E의 최종 이미지 재빌드 뒤 전체 시험을 반복한다.
