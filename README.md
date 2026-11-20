# 쪽결

한글과 영문이 섞인 마크다운 원고를 쓰고 출판 양식에 맞춰 PDF로 조판하는 웹 편집기입니다. 브라우저에서 원고와 양식을 편집하고, 서버의 LuaLaTeX와 `luatexko`가 PDF를 만듭니다.

## 내 컴퓨터에서 열기

Docker를 실행하고 이 폴더에서 다음 두 명령을 실행합니다. 첫 빌드에서 TeX와 나눔 글꼴을 내려받습니다. 빌드 뒤에는 인터넷 연결 없이 사용할 수 있습니다. 현재 검증 대상은 Linux ARM64 이미지입니다.

```sh
docker build --platform linux/arm64 --target local -t jjokgyeol:local .
docker run -d --name jjokgyeol-editor -p 127.0.0.1:8765:8000 jjokgyeol:local
```

[브라우저에서 쪽결 열기](http://127.0.0.1:8765/). 사용을 마치면 `docker stop jjokgyeol-editor`, 다시 열 때는 `docker start jjokgyeol-editor`를 실행합니다.

## 편집과 출력

1. 기본 원고를 수정하거나 UTF-8 `.md` 파일을 엽니다.
2. 글자 크기와 판형을 정하고 **미리보기 갱신**을 누릅니다. 옆 창에서 실제 PDF의 줄바꿈과 쪽 나눔을 확인합니다.
3. **PDF 다운로드**로 출력합니다. 갱신 후 원고나 양식을 더 고쳤다면 최신 내용으로 다시 조판합니다. 원고는 마크다운 파일로 따로 저장할 수 있습니다.

참조할 위치에 `[^지도]`를 쓰고 정의를 별도 줄에 둡니다. 정의에 따라 쪽 하단 각주, 문서 끝의 미주, 원고에서만 읽는 메모가 됩니다. 같은 미주를 다시 인용하면 같은 번호가 붙습니다.

```markdown
지도에는 [판형]{풀이=책의 크기}과 출처가 적혀 있다.[^규격][^대장][^교정]

[^규격]: 실측에 사용한 자의 눈금은 1mm다.
[^대장]{미주}: 도서관 등록 대장에서 확인했다.
[^교정]{메모}: 인쇄 전에 원본과 대조할 것.
```

단어 풀이는 해당 단어 옆에 작은 괄호로 출력됩니다. 한 줄에 들어가면 단어와 풀이가 함께 이동합니다. [주석 원고](proofs/annotations/manuscript.md) · [11pt PDF](proofs/annotations/annotations-11.pdf) · [16pt PDF](proofs/annotations/annotations-16.pdf)

같은 A5 원고를 나눔명조 16pt로 조판한 파일에서 전각 들여쓰기와 자간에 따른 줄바꿈을 비교할 수 있습니다. [원고](proofs/web-editor/manuscript.md) · [자간 0% PDF](proofs/web-editor/tracking-0.pdf) · [자간 −7% PDF](proofs/web-editor/tracking-minus-7.pdf)

새 원고는 A5, 나눔명조 11pt, 행간 17pt의 **양끝맞춤**으로 시작합니다. 조판기는 한글 어절과 영문 단어 경계에서 줄을 나누고, 문단의 마지막 줄을 제외한 각 줄이 본문 오른쪽 경계에 닿도록 한글 자간과 어절 사이 공백을 조정합니다. 문단의 마지막 줄은 자연스러운 길이를 유지합니다. [같은 원고](proofs/justified-publication/manuscript.md) · [종전 PDF](proofs/justified-publication/before.pdf) · [양끝맞춤 PDF](proofs/justified-publication/after.pdf)

**판형·여백 및 세부 양식**에서 줄별 최종 자간과 어절 공백의 하한·상한을 정할 수 있습니다. 간격을 좁게 고정하거나 본문 폭에 비해 단어가 긴 경우에는 어절 보존과 허용 범위를 지키면서 가능한 만큼 맞춥니다. [출판용 양끝맞춤 설계](docs/superpowers/specs/2026-11-20-justified-publication.md)

첫 줄과 이후 줄의 들여쓰기는 mm 단위입니다. 첫 줄에 6, 이후 줄에 0을 입력하면 문단 첫머리가 들어갑니다. 첫 줄에 0, 이후 줄에 6을 입력하면 둘째 줄부터 안으로 들어가는 내어쓰기입니다. 조판기는 각 줄의 폭에 맞춰 어절 경계를 우선합니다.

자간과 공백은 소수 둘째 자리까지 적용되며 미리보기를 갱신하면 적용값을 확인할 수 있습니다. 예를 들어 자간에 `−7.245%`를 지정한 결과는 `−7.25%`입니다. **양식 저장**으로 정수 단위의 JSON 파일을 내려받고 **양식 열기**로 다른 원고에 다시 적용합니다. 원고는 **원고 저장**으로 별도의 마크다운 파일에 보관합니다.

같은 원고를 A5·14pt, 첫 줄 0mm·이후 줄 6mm로 출력한 예시입니다. 편집기에서 원고와 양식 파일을 각각 열면 두 쪽에 걸친 내어쓰기와 각주를 확인할 수 있습니다. [원고](proofs/web-editor/manuscript.md) · [양식 JSON](proofs/web-editor/fixed-point-style.json) · [출력 PDF](proofs/web-editor/fixed-point.pdf)

### 일반 보고서 양식

**판형·여백 및 세부 양식**에서 **일반 보고서 양식 다운로드**를 누른 뒤, 받은 JSON을 **양식 열기**로 엽니다. 원고의 첫 줄에 `# 보고서 제목`을 쓰면 A4 본문 폭의 사각 테두리 안에 나눔고딕 Bold 20pt로 가운데 정렬됩니다. 각 항목은 `- `로 시작하고 하위 단계마다 앞에 공백 두 칸을 더합니다. [양식 JSON](app/static/general-report.json) · [예제 원고](proofs/general-report/manuscript.md) · [예시 PDF](proofs/general-report/general-report.pdf)

| 단계 | 기호 | 글꼴과 크기 | 왼쪽 여백에서 들여쓰기 |
| --- | --- | --- | --- |
| 1 | □ | 나눔고딕 Bold 16pt | 0칸 |
| 2 | ○ | 나눔명조 15pt | 1칸 |
| 3 | **-** | 나눔명조 15pt, 기호만 굵게 | 3칸 |
| 4 | * | 나눔고딕 13pt | 5칸 |

한 칸은 7.5pt입니다. 두 글꼴 모두 배포 이미지에 포함되어 있어 별도 설치 없이 출력할 수 있습니다. [나눔고딕 라이선스](https://raw.githubusercontent.com/google/fonts/main/ofl/nanumgothic/OFL.txt) · [나눔명조 라이선스](https://raw.githubusercontent.com/google/fonts/main/ofl/nanummyeongjo/OFL.txt)

### 본문과 제목 크기를 따로 정하기

보고서 예제의 원고를 **원고 열기**로, 양식을 **양식 열기**로 엽니다. **판형·여백 및 세부 양식 → 역할별 글자 모양**에서 1단계 제목을 선택하면 18pt, 굵게, 남색(`#17365D`)으로 설정되어 있습니다. 본문 크기를 11pt에서 16pt로 바꾸고 **미리보기 갱신**을 누르면 본문과 목록의 줄바꿈을 비교할 수 있습니다. 두 PDF 모두 2단계 제목은 14pt, 각주는 9pt이고, 머리글에는 ‘능선 관측소’, 바닥글에는 쪽 번호가 나옵니다. [원고](proofs/publication-typography/manuscript.md) · [11pt 양식](proofs/publication-typography/style.json) · [본문 11pt PDF](proofs/publication-typography/report-11.pdf) · [본문 16pt PDF](proofs/publication-typography/report-16.pdf) · [PDF 글자 실측](docs/superpowers/plans/2026-11-20-publication-typography.md#보고서-pdf-실측)

새 편집기의 여섯 제목 단계는 각각 크기를 갖습니다. 예전 양식을 열면 크기를 지정하지 않은 제목은 종전처럼 본문 크기에 비례합니다. 한 단계만 고쳐 저장하면 다른 다섯 단계는 그 비례 동작을 유지합니다.

### 다른 형식의 원고 가져오기

**원고 가져오기**에서 TXT(ASCII·UTF-8), HTML, DOCX, HWPX 또는 HWP 5 파일을 고릅니다. 성공하면 현재 편집창에 새 마크다운 원고가 열립니다. 예를 들어 `관측 기록.hwpx`를 가져온 뒤 **원고 저장**을 누르면 `관측 기록.md`를 내려받습니다. 현재 출판 양식으로 **미리보기 갱신**과 **PDF 다운로드**를 사용할 수 있습니다.

그림이나 표 배치처럼 변환 중 달라진 요소는 원고 아래 **원고 가져오기 경고**에서 확인합니다. 현재 편집 중인 원고도 따로 보관하려면 가져오기 전에 **원고 저장**을 누르세요. 변환에 실패하거나 응답을 기다리며 새로 편집한 경우에는 작업 중인 원고가 유지됩니다.

### 자연어로 양식 바꾸기

**AI 양식 제안**을 열고 “본문은 16pt로 하고 바닥글 가운데에 쪽 번호를 넣어 줘”처럼 요청합니다. 바뀔 항목과 값을 확인한 뒤 **제안 적용**을 누릅니다. 이어서 본문 크기를 직접 고쳐도 바닥글은 유지됩니다.

Docker에서 호스트의 LM Studio로 연결할 때는 서비스 실행 명령의 이미지 이름 앞에 `-e JJOKGYEOL_STYLE_URL=http://host.docker.internal:1234/v1 -e JJOKGYEOL_STYLE_MODEL=google/gemma-4-e2b`를 추가합니다. 모델에는 변경 요청과 현재 양식 값이 전달됩니다. [구현 및 실모델 검증](docs/superpowers/plans/2026-11-20-style-suggestion.md)

### 단계별 목록 서식

마크다운에서 항목 앞에 `- `를 쓰고 하위 항목을 안쪽으로 들여씁니다. `# 제1조`처럼 조 제목을 별도 줄에 적으면 다음 목록을 새로 시작할 수 있습니다.

```md
# 제1조 자료의 정리

- 편집자는 현장 기록을 정리한다.
  - 제목에는 장소를 적는다.
  - 자료의 출처를 확인한다.
```

편집기의 **판형·여백 및 세부 양식 → 단계별 목록 서식**에서 법령형이나 보고서형을 고른 뒤 단계별 표지 표현식, 들여쓰기, 표지와 본문 사이 간격을 조정합니다. 위 원고의 세 표지는 법령형에서 `① / 1. / 2.`, 보고서형에서 `ㅁ / ㅇ / ㅇ`입니다. 각 단계의 형제 항목 중 가장 넓은 표지에 맞춰 본문 시작선을 정하므로 긴 항목의 다음 줄도 그 선에서 시작합니다.

`{숫자}`, `{한글}`, `{원숫자}` 자리에 해당 항목의 순번이 들어갑니다. `[붙임 {숫자}호]`는 두 번째 항목에서 `[붙임 2호]`가 됩니다. 고정 기호는 그대로 입력하며 중괄호 자체는 `{{`, `}}`로 적습니다. 원고가 지정한 단계보다 더 깊으면 마지막 단계의 서식을 이어서 씁니다. 현재 마크다운 파서는 목록을 9단계까지 읽고, 더 깊은 원고는 해당 입력 오류를 표시합니다.

번들 나눔 글꼴에서는 원숫자를 ①부터 ⑳까지 쓰고 21번째부터는 `(21)`, `(22)`로 표시합니다. 한글 순번은 `가`부터 `하`까지 쓴 뒤 `가가`, `가나`로 이어집니다.

[같은 원고](proofs/lists/manuscript.md) · [법령형 PDF](proofs/lists/legal.pdf) · [보고서형 PDF](proofs/lists/report.pdf)

### 조 제목 자동 번호

**판형·여백 및 세부 양식**에서 **조 제목 자동 번호**를 켜고 미리보기를 갱신합니다. 원고에는 평소처럼 `# 제8조(자료 수집)`, `## 제8조의2(구두 기록)`이라고 씁니다. 이 조가 출력 순서상 세 번째 일반 조이면 제목은 각각 **제3조(자료 수집)**, **제3조의2(구두 기록)**가 됩니다. `의5`를 추가해도 다음 일반 조는 제4조입니다. 법령형에서 일반 조와 가지조는 각각 ①부터 시작합니다.

번호를 유지하려면 조 제목에 커서를 놓고 **번호 고정/해제**를 누르거나 `# 제10조(보관) {번호고정}`처럼 작성합니다. 고정한 일반 조 다음에는 제11조가 오고, 고정한 가지조는 앞뒤 조를 옮겨도 원고에 적은 번호로 출력됩니다. 본문에 직접 인용한 조 번호는 원문을 따릅니다. 자동 번호 설정은 양식 파일, 고정 표시는 마크다운 원고에 저장됩니다.

[편집 전 원고](proofs/articles/before.md) · [편집 전 PDF](proofs/articles/before.pdf) · [편집 후 원고](proofs/articles/after.md) · [편집 후 PDF](proofs/articles/after.pdf) · [재현용 양식](proofs/articles/style.json)

### 기존 문서에서 서식 가져오기

1. 편집할 마크다운 원고를 열고 **참고 문서 서식 가져오기**에서 DOCX, HWPX, PDF, PNG 또는 JPEG를 선택합니다. DOCX와 HWPX는 구역과 문단을, PDF는 실제 쪽을 기준으로 살펴봅니다.
2. 참고 구간의 시작과 끝을 정합니다. 각 후보에서 판독 근거와 현재 값을 대조하고, 가져올 속성과 적용할 원고 제목을 고릅니다. 예를 들어 본문 크기에 14pt를 입력하면 그 구간의 본문에 적용됩니다. 판형과 사방 여백은 원고 전체에 적용됩니다.
3. 스캔에 실제 치수가 없으면 참고 지면의 폭을 mm로 입력해 상대값을 환산합니다. 글꼴이 확실하지 않으면 설치된 글꼴 중에서 직접 고릅니다. 머리글·바닥글 후보에서는 원본 구역과 문구를 확인하고, 가져올 칸의 값을 고칩니다. 스캔에서 읽은 숫자는 고정 문구로 쓸지 쪽 번호로 표시할지 선택합니다.
4. **시험 조판**으로 현재 원고의 PDF를 확인한 뒤 적용하거나 취소합니다. 확정한 구간과 속성은 양식 JSON에 저장됩니다. 원고의 제목이 바뀌거나 같은 경로에 제목이 둘 이상이면 적용 대상을 다시 선택합니다.

예제에서는 ‘들판 기록 › 모래톱’의 본문만 14pt로 바꿉니다. 편집기에서 원고와 양식을 각각 열어 결과를 재현할 수 있습니다. [예제 원고](proofs/reference-style/target.md) · [양식 JSON](proofs/reference-style/style.json) · [적용 전 PDF](proofs/reference-style/before.pdf) · [적용 후 PDF](proofs/reference-style/release-after.pdf)

**머리글·바닥글 편집**에서 문서 공통 바닥글을 ‘표시’로 바꾸고 가운데 칸에 `－ {현재쪽} －`를 입력하면 현재 쪽 번호가 인쇄됩니다. 표지에만 번호를 숨기려면 표지 제목을 적용 대상으로 선택하고 바닥글을 ‘숨김’으로 바꿉니다. 표지를 한 쪽 쓰면 다음 쪽의 번호는 2입니다. 부록에서 기관명만 바꾸려면 부록 제목을 선택하고 머리글의 해당 칸만 지정합니다. 각 쪽은 첫 원고 줄이 속한 구간의 양식을 쓰므로 긴 문단이 다음 쪽으로 이어져도 그 문단의 머리글이 따라갑니다.

예제의 ‘추적문장’은 중간에 열 문단을 넣으면 9쪽에서 10쪽으로 옮겨갑니다. 두 파일을 나란히 열면 쪽 번호와 부록 머리글이 어떻게 바뀌는지 확인할 수 있습니다. [11쪽 PDF](proofs/running-heads/before.pdf) · [12쪽 PDF](proofs/running-heads/after.pdf) · [예제 양식](proofs/running-heads/style.json)

### 양면 보고서의 바깥쪽 번호

1. **머리글·바닥글 편집**에서 문서 전체의 바닥글을 선택합니다. 공통 면의 표시 여부를 ‘표시’로 두고, 홀수 쪽의 오른쪽 칸과 짝수 쪽의 왼쪽 칸에 각각 `{현재쪽}`을 입력합니다. 공통 면에서 가운데 번호를 상속했다면 홀수 쪽과 짝수 쪽 각각에서 가운데 칸을 직접 지정해 비웁니다.
2. 표지에서만 번호를 숨기려면 문서 전체의 첫 쪽 바닥글을 ‘숨김’으로 지정합니다. 부록 첫 면에서 머리글만 숨기려면 부록 제목과 첫 쪽 머리글을 선택합니다. 부록이 짝수 쪽에서 시작해도 바닥글 번호는 왼쪽에 놓입니다.
3. 참고 문서를 가져올 때는 원본에서 읽은 면과 내 원고에 적용할 면을 확인합니다. 서로 다른 홀·짝 후보를 함께 선택해 시험 조판할 수 있습니다. 한 장짜리 스캔은 적용할 면을 직접 지정하고, 문구 속 숫자를 고정하거나 자동 번호로 바꿉니다.

열 문단을 추가한 예제에서 같은 문장의 번호가 9쪽 오른쪽에서 10쪽 왼쪽으로 옮겨갑니다. [9쪽·10쪽 나란히 보기](proofs/duplex-running-heads/pages-9-10.pdf) · [양면 양식](proofs/duplex-running-heads/style.json) · [원고와 검증 기록](docs/superpowers/duplex-running-heads-acceptance.md)

### 양면 보고서의 본문·제본 여백

**판형·여백 및 세부 양식**에서 **양면 여백**을 선택하고 안쪽·바깥쪽·제본 여백을 mm로 입력합니다. 왼쪽으로 제본할 보고서에서 안쪽 12mm, 제본 6mm, 바깥쪽 10mm를 지정하면 종이의 제본 쪽 가장자리부터 본문까지는 18mm입니다. **미리보기 갱신**을 누르면 홀수 쪽에는 왼쪽 18mm·오른쪽 10mm, 짝수 쪽에는 왼쪽 10mm·오른쪽 18mm가 적용됩니다. 위에서 설정한 바깥쪽 번호도 같은 본문 경계에 맞춰집니다.

제본에 필요한 공간을 바꾸려면 제본 여백만 조정합니다. 제본을 0으로 두어도 안쪽과 바깥쪽은 쪽마다 교대합니다. 양식 파일에는 제본 치수를 따로 저장하므로 다시 열어 수정할 수 있습니다.

같은 문장이 9쪽에서 10쪽으로 밀릴 때 본문 시작점과 번호가 어디로 옮겨가는지 교정본에서 확인할 수 있습니다. [9쪽·10쪽 비교 PDF](proofs/mirrored-margins/pages-9-10.pdf) · [예제 양식](proofs/mirrored-margins/style.json) · [검증 기록](docs/superpowers/mirrored-margins-acceptance.md)

스캔의 글자 크기는 이미지에서 보이는 글자 높이를 바탕으로 추정하므로 직접 조정할 수 있습니다. 부모 단계 번호까지 조합한 복합 목록 표지에는 지원 상태를 표시합니다. DOCX의 조건부 표 스타일은 현재 가져오기 범위에서 제외됩니다.

스캔과 이미지형 PDF 쪽을 분석하려면 서버에 이미지 입력을 지원하는 모델을 연결합니다. 처음 로컬 컨테이너를 시작할 때 LM Studio에서 설치된 비전 모델과 로컬 서버를 켜고 아래 명령을 사용합니다. 모델 이름은 LM Studio가 표시하는 식별자를 씁니다.

```sh
docker run -d --name jjokgyeol-editor -p 127.0.0.1:8765:8000 \
  -e JJOKGYEOL_VISION_URL=http://host.docker.internal:1234/v1 \
  -e JJOKGYEOL_VISION_MODEL=google/gemma-4-e2b jjokgyeol:local
```

호스팅판에서는 운영자가 같은 두 변수로 모델을 지정합니다. 인증을 쓰는 서버는 `JJOKGYEOL_VISION_TOKEN`도 설정합니다. 분석을 시작하면 해당 모델에는 참고 지면 이미지가 전달됩니다. 구조 문서와 텍스트 PDF는 설치한 판독기로 처리합니다. 업로드 크기·쪽수·처리 시간은 [참고 파일 운영 설정](app/config/reference.json)에서 조정합니다.

## 설치 기본값 바꾸기

새 원고의 본문 크기를 바꾸려면 [출판 설정](app/config/publication.json)의 `defaults.body_pt` 한 곳을 수정합니다. 이미 실행 중인 컨테이너에는 새 설정을 포함한 이미지를 빌드해 적용합니다. 편집기의 첫 화면, 기본 양식을 쓰는 API 요청, PDF가 같은 값을 사용합니다. 사용자가 열어 둔 양식 파일에 본문 크기가 지정되어 있으면 그 값이 우선합니다.

요청 크기, 동시에 실행할 조판 작업 수, 작업 시간과 파일 크기 한도는 [운영 설정](app/config/operations.json)에서 관리합니다. 설치 기본값과 운영 한도는 서버 시작 시 읽습니다.

## 검증

```sh
docker build --platform linux/arm64 --target test -t jjokgyeol:test .
docker run --rm --network none jjokgyeol:test pytest -q
```

독립 컨테이너 비교와 설정 변경 검사는 Docker에 접근할 수 있는 호스트에서 실행합니다. 호스트 테스트 환경에는 pytest, httpx, pdfminer.six와 Playwright Chromium이 필요합니다. 출판 설정 한 값만 변경한 컨테이너에서 화면·API·PDF를 확인하고, 기본 양식과 저장 양식의 줄·글자 좌표를 이전 버전과 대조합니다.

```sh
JJOKGYEOL_TEST_IMAGE=jjokgyeol:local python -m pytest -q tests/test_slice.py::test_deployment_parity tests/test_refactor_acceptance.py
```

참고 서식의 실모델 인수 검사는 사용 중인 로컬 컨테이너·모델과 같은 이미지를 지정해 실행합니다. [검증 표본과 측정 결과](docs/superpowers/reference-style-acceptance.md)

```sh
JJOKGYEOL_REFERENCE_URL=http://127.0.0.1:8765 JJOKGYEOL_TEST_IMAGE=jjokgyeol:local \
  python -m pytest -q tests/test_reference_acceptance.py
```

[제품 스펙](SPEC.md) · [개발 계획](docs/superpowers/plans/2026-11-19-jjokgyeol-development.md) · [엔진 비교 PDF](proofs/engine-comparison/side-by-side.pdf)

### 실험용 문단 선택과 판독 쌍

미리보기와 다운로드는 T11의 규칙을 사용합니다. T14 판독용 클라이언트는 같은 `POST /api/render`에 같은 `source`, `style`을 넣고 `decision`만 `"rule"`과 `"model"`로 바꿉니다. 운영자의 로컬 모델 연결도 필요합니다. 응답의 `X-Jjokgyeol-Decision`이 각각 `rule`, `model`인 쌍을 채점에 사용합니다. `mixed`는 선택 가능한 문단 일부에 규칙 복귀가 있었음을 뜻합니다. 모델이 규칙과 같은 ID를 골라도 `model`이므로 두 지면의 실제 줄 끝을 비교해 기록합니다.

두 배포 모드에서 같은 운영자의 Docker Desktop과 LM Studio를 쓸 때는 아래처럼 실행합니다. LM Studio 로컬 서버에 이미 로드한 모델을 사용합니다. `lms ls google/gemma-4-e2b --json`의 4bit 행에서 `indexedModelIdentifier`의 @ 뒤 폴더를 확인합니다. `lms runtime ls`에서 선택된 MLX 엔진과 응답이 보고하는 실제 엔진은 배포 핀과 일치해야 합니다.

```sh
model_root=$(python3 -c 'import json,pathlib; print(json.loads((pathlib.Path.home()/".lmstudio/settings.json").read_text())["downloadsFolder"])')
model_dir="$model_root/lmstudio-community/gemma-4-E2B-it-MLX-4bit"
docker build --platform linux/arm64 --target local -t jjokgyeol:t13-local .
docker build --platform linux/arm64 --target service -t jjokgyeol:t13-service .
for image in jjokgyeol:t13-local jjokgyeol:t13-service; do
  docker run -d --pull=never -p 127.0.0.1::8000 \
    -e JJOKGYEOL_DECISION_HOST=host.docker.internal \
    --mount "type=bind,source=$model_dir,target=/opt/decision-model,readonly" "$image"
done
```

`docker port <컨테이너 ID> 8000/tcp`로 각 주소를 확인합니다. 포트 기본값은 LM Studio의 1234이며 `JJOKGYEOL_DECISION_PORT`로 변경할 수 있습니다. 본문 선택에는 후보 ID, 줄 끝 위치·폭·간격과 문자 분류만 전달합니다. 작업자는 첫 추론 전 가중치·토크나이저 등 여덟 파일의 SHA-256을 확인하고 실제 선택에 쓰인 응답의 모델·양자화·런타임을 검사합니다. 모델 검사·추론·재생에는 작업당 최대 16초를 두고, 요청 마감 1초 전에는 이미 만든 규칙 지면을 반환할 수 있도록 합니다. [모델 핀과 한도](app/config/decision.json)

명시적인 평가 요청은 규칙 쪽도 실측 후보를 수집합니다. T14 원고로 양쪽 응답 PDF와 최종 후보 로그·시간·해시를 내보내는 명령과 이전 실행 결과는 [문단 선택 검증 기록](docs/superpowers/decision-acceptance.md)에 있습니다.
