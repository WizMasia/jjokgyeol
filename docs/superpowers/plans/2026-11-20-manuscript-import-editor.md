# T10 원고 가져오기 HTTP·편집기 구현 계획

**목표** 작업자로 격리한 변환 결과를 편집 중인 원고에 안전하게 연결한다. [보충 설계](../specs/2026-11-20-manuscript-import-editor-design.md)

### Task 1: 가져오기 요청과 격리

- [x] `tests/test_import_api.py`에서 일반 HTTP POST의 원본 바이트·한글 파일명, 반환 키, 422 오류, Content-Length 및 스트리밍 크기 초과, 느린 업로드와 포화 후 재사용을 검사한다. Docker에서 404로 실패하는지 확인한다.
- [x] `app/server.py`의 DocumentJob에서 설정된 원본 크기와 슬롯·기한을 적용하고 `app/import_worker.py`를 호출한다. 작업자가 메모리·파일 한도를 적용한 뒤 `app.importers.read.import_file(name, data)`만 호출하고 두 필드를 JSON으로 쓴다.
- [x] 같은 테스트와 기존 작업자 쓰기·연결 해제 회귀를 Docker에서 실행한다.

### Task 2: 일반 편집기에서 새 .md 열기

- [x] `tests/test_import_editor.py`에서 기존 호스트 Chromium으로 선택기, 이전 원고·저장 형식·미리보기·경고 보존, 지연 후 편집/새 열기/새 가져오기, 다시 저장을 검사한다. 선택기가 없는 실패를 먼저 확인한다.
- [x] `app/static/index.html`에 선택기와 경고 목록을 추가한다. `app/static/editor.js`에서 기존 source 열기와 원고 적용 경로를 재사용한다. 응답 검증 뒤 텍스트로만 경고를 표시한다.
- [x] `README.md`에 가져오기 절을 추가하고 새 브라우저 테스트와 기존 마크다운 열기·저장 회귀를 실행한다.

### Task 3: 다섯 형식 실물과 실제 조판 대조

- [x] 라이브러리 담당자의 고정 표본을 사용해 호스트 브라우저와 Docker 서비스에서 TXT, HTML, DOCX, HWPX, HWP를 차례로 선택한다. 각 표본의 기대 텍스트를 저장한 .md와 실제 PDF에서 검사하고, 모든 경고와 원본 바이트를 대조한다.
- [x] 라이브러리 커밋과 결합한 상태에서 새 HTTP·브라우저 테스트, 라이브러리 실물 대조와 기존 편집기·서버 회귀를 실행한다. 소유 파일만 커밋하고 브랜치와 검증 범위를 총괄에게 전달한다.

**검토 초점** 오류나 뒤늦은 응답의 부분 반영, 원본 이름으로 덮어쓰기, 사용자가 입력하는 동안의 토큰 취소, 경고를 통한 HTML 삽입, HTTP 시간 제한과 슬롯 해제, 기존 양식·미리보기 동작.

## HTTP·브라우저 검증 기록

판독기 커밋 `678e620`을 `feature/import-editor`에 병합하고 같은 작업 트리에서 Linux ARM64의 `local`과 `test` 이미지를 빌드했다. 서비스는 이미지의 비특권 `editor` 사용자로 실행했다.

| 입력 원본 | 저장한 원고 | 다운로드한 PDF에서 확인한 문구 |
|---|---|---|
| field.txt | field.md | 측정을 마쳤다 |
| field.html | field.md | 자료 보기 |
| lists.docx | lists.md | Some nested lists |
| ParaHead.hwpx | ParaHead.md | 개요2-1-1 |
| lists.hwp | lists.md | 3-2-1 |

각 행은 호스트 Chromium의 **원고 가져오기**에서 실제 파일을 선택했다. 파일명·바이트 수·요청 형식을 확인하고 같은 원본을 API에 직접 보낸 결과와 편집 본문·표시 경고·저장 바이트를 대조했다. 본문 크기 12pt가 편집기에 유지되는지 검사한 뒤 **PDF 다운로드**로 받은 파일에서 표의 문구를 추출하고 입력 파일을 다시 읽어 원본 보존을 확인했다.

- 적용 전 HTTP 테스트 8건이 실패했다. 없는 경로는 404였고 느린 본문은 읽히지 않았다. 호스트 브라우저에서는 가져오기 버튼 탐색이 실패했다.
- HTTP 검증에서 입력 오류는 422, 선언 또는 스트림 크기 초과는 413, 점유 중인 슬롯은 429, 멈춘 업로드는 504를 반환했다. Linux 작업자의 메모리 상한을 줄이자 해당 요청만 실패하고 다음 요청이 성공했다. 상한 설정 한 줄을 제거한 임시 사본에서는 이 검사가 실패했다.
- 직전 PDF와 BOM·줄바꿈이 있는 원고를 열어 둔 상태에서 실패를 확인했다. 진행 중인 변환 뒤에 편집, 새 열기, 재가져오기가 일어나면 늦게 도착한 결과·경고·오류가 무시됐다. 실제 손상 DOCX를 고른 경우에도 앞서 가져온 HTML 원고의 수정 내용·이름·그림 경고가 보존됐다.
- 네트워크를 끈 테스트 이미지에서 HTTP·다섯 판독기·작업자·브라우저 67건이 통과했다. 별도의 Docker 서비스에 연결한 호스트 Chromium에서 가져오기 11건과 기존 편집기 34건이 통과했다.

재현할 때는 로컬 서비스 포트를 환경 변수에 지정한다.

```sh
docker build --platform linux/arm64 --target test -t jjokgyeol:t10-editor-test .
docker run --rm --network none --shm-size=512m jjokgyeol:t10-editor-test \
  pytest -q tests/test_import_api.py tests/test_imports.py tests/test_jobs.py tests/test_import_editor.py

docker build --platform linux/arm64 --target local -t jjokgyeol:t10-editor-local .
docker run -d --rm --name t10-import-check -p 127.0.0.1::8000 jjokgyeol:t10-editor-local
export JJOKGYEOL_IMPORT_URL="http://$(docker port t10-import-check 8000)"
PLAYWRIGHT_BROWSERS_PATH=/tmp/jjokgyeol-dev/browsers /tmp/jjokgyeol-dev/venv/bin/python \
  -m pytest -q tests/test_import_editor.py tests/test_editor.py
docker stop t10-import-check
```
