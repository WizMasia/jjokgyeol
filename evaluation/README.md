# T14 사람 판독 / Blind reading

[사전 규약](../docs/superpowers/specs/2026-11-20-blind-reading-design.md) · [판독자에게 줄 안내](reader-guide.md) · [실행 기록](acceptance.md)

**원고 출처**　`corpus/quay.md`는 자간 비교용, `drawer.md`·`platform.md`는 모델 비교용이다. 세 편 모두 이 작업에서 에이전트가 새로 쓴 혼합 산문이다. 사용자가 프로젝트용 창작과 로컬 평가를 위임했다. 외부 저자·편집자에게서 수집한 원고나 선호 자료는 없다. 세 작품과 개작은 모델 학습·프롬프트 튜닝에서 제외한다. 새 양끝맞춤 규약에서 `quay`는 사용자가 지적한 지면을 재현하고 출판 양식의 자간 한계를 정할 때 사용했다. 이 원고의 12장은 해당 지면에 대한 선호를 묻는다. 모델 쌍의 두 원고는 별도로 고정하며 후보 규칙 조정에 사용하지 않는다. 분리 검사는 제출된 로컬 학습·개발 목록을 대상으로 한다. 사전학습 모델 자료의 전수 확인을 뜻하지 않는다.

### 실행과 응답 / Operator workflow

저장소 루트에서 test 이미지를 빌드한다. 이미지에는 평가 코드·안내·빈 CSV와 고정 원고 3편이 들어 있다. `/runs`는 호스트의 Git 제외 폴더다. 각 실행은 새 이름을 쓴다.

```sh
docker build --platform linux/arm64 --target test -t jjokgyeol:t14-test .
docker run --rm --network none --shm-size=512m jjokgyeol:t14-test pytest -q tests/test_evaluation.py
mkdir -p evaluation/runs
ev() {
  docker run --rm --network none --shm-size=512m \
    -v "$PWD/evaluation/runs:/runs" \
    jjokgyeol:t14-test python -m evaluation "$@"
}
ev justification /runs/quay-justified-01
ev timing /runs/quay-justified-01
```

`justification`은 고정 원고 `quay.md`를 편집기의 실제 `/api/render`로 조판한다. A5, 상하 20mm·좌우 18mm에서 나눔명조와 나눔고딕을 각각 11pt·행간 17pt와 16pt·행간 24pt로 비교한다. 네 묶음마다 선호 자간(initial tracking) 0%, −3%, −7%를 무작위 순서로 조판한다. 각 줄의 실제 자간은 세 조건 모두 같은 절대 범위 −15~+15% 안에서 달라질 수 있으며, 어절 공백은 자연 폭의 70~130%다. 두 글꼴의 실측 글리프 폭으로 계산했을 때 16pt의 일부 줄은 −7~+15%에서 어절을 보존하며 채울 수 없어 기본 하한을 −15%로 정했다.

범위를 바꿀 때는 별도 실행 이름과 `--tracking-bounds MIN MAX`, `--space-bounds MIN MAX`를 쓴다. 예를 들어 `ev justification /runs/quay-narrow --tracking-bounds -7 15 --space-bounds 70 130`은 좁은 하한을 그대로 적용한다. 어느 한 표본이라도 검사에 실패하면 명령이 실패하고 해당 실행 폴더가 생성되지 않는다.

원본 PDF에서 문단별 원고 글자와 줄 끝의 어절 경계, 글꼴·크기, 자간과 공백을 확인한다. 모든 줄의 첫 글자는 본문 왼쪽 경계에서, 문단 마지막 줄을 제외한 줄의 마지막 글자 진행 폭 끝은 본문 오른쪽 경계에서 0.05 PDF pt 이내여야 한다. 문단 마지막 줄은 해당 표본의 선호 자간과 자연 공백으로 조판한다. 공백 100%가 지정 범위를 벗어나면 가장 가까운 허용값을 쓴다. 한 어절만 남았고 그 어절의 자연 폭이 본문 폭보다 길 때는 들어갈 만큼의 압축만 허용한다. 쪽이 바뀌어도 같은 문단이면 검사를 이어간다. 비공개 키의 각 표본에는 실제 요청 양식과 `verification`이 저장된다. `line_ends`는 누적 비공백 문자 위치, `nonfinal_lines`는 양끝을 검사한 줄 수이며 오차는 PDF pt 단위다.

과거의 `spacing` 명령과 결과는 공백 100%, 한글 자간을 줄마다 같은 값으로 고정한 보정 자료로 유지한다. 새 양끝맞춤 판독에는 `justification`의 실행 ID와 빈 응답표를 쓴다.

진행자는 `reader/`와 판독 안내만 제공한다. `private/key.json`은 원고·조건·원본 PDF 해시·실측 시간을 연결하는 실행별 대응표다. PDF 원본과 이 폴더는 판독자와 분리한다. 자간 판독은 한 묶음당 세 표본, 모델 판독은 한 묶음당 두 표본과 확정 기준 지면 한 장이다.

```sh
ev capture /runs/quay-justified-01 /runs/returned.csv --reader R1
ev score /runs/quay-justified-01
```

CSV 열은 `run,group,sample,preferred,crowding,paragraph,reason`이다. 실행·묶음·표본 조합이 정확히 한 번씩 있어야 한다. 등록 시 익명 PDF 무결성을 확인해 응답 원문·별칭과 대응표 해시를 비공개로 고정한다. 응답 전 상태는 `awaiting_reader`다.

### 출판 양끝맞춤 모델 비교

새 판독에는 편집자가 `drawer`·`platform`을 양끝맞춤 프로필로 각각 다시 확정한 기준 지면을 권장한다. 두 기준은 A5·나눔명조 11pt·행간 17pt, 선호 자간 0%, 절대 자간 −15~+15%, 어절 공백 70~130%다. 별도 기준 폴더와 새 실행에서 규칙·모델 양쪽의 같은 프로필을 비교한다. `reference`의 각 수정판과 `pairs`에 모두 `--justified`를 지정한다.

```sh
ev reference /runs/refs-justified drawer /runs/drawer-edited.pdf --justified \
  --editor E1 --paragraph "$PARAGRAPHS" --reason "$EDITOR_REASON" --final
# platform도 편집자가 지면을 확인한 뒤 같은 방식으로 등록한다.
ev pairs /runs/model-justified /runs/refs-justified /runs/t13-justified --justified
```

새 모델 프로필은 선택한 후보의 줄마다 실제 한글 자간·어절 공백과 그 간격을 누적한 글자 위치를 PDF와 대조한다. 글자 위치는 배포 글꼴의 고유 진행 폭과 영문 커닝으로 계산한다. 테스트 이미지 밖에서 선택 로그를 검사할 때는 `JJOKGYEOL_FONT_DIR`로 `runtime.lock`과 해시가 같은 두 나눔 글꼴의 폴더를 지정한다. 후보 로그에 어절별 공백 폭(TeX sp)이 있으면 개수와 순서도 검사한다. PDF와 로그의 허용 차이는 0.05 PDF pt다.

아래의 옵션 없는 모델 명령은 종전 프로필로 수집한 기준·산출물의 재현용이다. 새 실행의 선호·동률·시간 수용은 해당 실행에서 받은 사람의 응답으로 기록한다.

### 편집자 기준 / Editor reference

편집자는 두 모델용 원고를 각각 같은 양식으로 직접 조판한다. 아래 `MODEL_STYLE`의 판형·글꼴·크기·여백을 유지하며 만족할 때까지 문단 배치를 고친다. 본문만 담은 검색 가능한 PDF를 준비한다. 프로그램은 본문·글꼴·크기·판형을 검사한다. 같은 조건의 기존 편집 지면도 가능하다. 각 수정판의 PDF·별칭·문단·사유를 등록한다. 처음부터 수정할 곳이 없으면 문단에 `none`을 쓰고 판단 사유를 남긴다.

```sh
ev reference /runs/refs drawer /runs/editor-drawer.pdf --editor E1 \
  --paragraph "$PARAGRAPHS" --reason "$EDITOR_REASON"
ev reference /runs/refs drawer /runs/editor-drawer-final.pdf --editor E1 \
  --paragraph "$PARAGRAPHS" --reason "$EDITOR_REASON" --final
```

`platform`도 같은 절차로 확정한다. `--final`은 편집자의 실제 확인을 뜻한다. 각 판의 PDF와 이력이 남고 최종 승인 후에는 해당 판본을 덮어쓸 수 없다. 반드시 최종 기준을 고정한 뒤 익명 비교를 시작한다.

### T13 인계 / Measured bundle

T13 담당자는 opt-in한 실제 제품 경로에서 각 원고를 두 번 조판한다. 같은 `POST /api/render`에 `source, style`을 보내며 `decision`을 `rule`·`model`로 지정한다. `evaluation.model.MODEL_STYLE`을 그대로 사용한다. A5, 여백 상우하좌 20/18/20/18mm, 나눔명조 11pt·행간 17pt, 기본 자간 0%, 최종 −7~0%, 공백 70~130%다. 평가 원고는 BOM 없는 UTF-8 파일 전체를 개행까지 보존한다.

| 상대 경로 | 필수 내용 |
|---|---|
| `manifest.json` | 객체에 `runtime`(실제 모델·런타임 이름/버전), `model_sha256`(로드한 파일), `development`(전체 프로젝트 학습·튜닝 원고 배열)가 필요하다. 각 원소는 `{group, split, source}`이며 split은 `train` 또는 `dev`다. 관련 판본은 같은 group이다. 해당 자료가 실제로 없을 때만 빈 배열을 쓴다. |
| `{group}/{arm}/render.pdf` | group은 `drawer`·`platform`, arm은 `rules`·`model`이다. 각 요청이 반환한 실제 최종 PDF다. |
| `{group}/{arm}/paragraph-candidates.jsonl` | 규칙은 해당 출력의 수집 로그, 모델은 재생한 최종 출력의 T11 로그다. 작업 폴더 정리 전에 두 로그를 보존한다. 각 본문 블록의 `block,candidates,rule_id,selected_id`와 후보별 `id,lines`를 보존한다. |
| `{group}/{arm}/measurement.json` | `applied`는 응답 헤더 `X-Jjokgyeol-Decision` 원값이다. 규칙은 `rule`, 실제 모델은 `model`이다. 모델 쪽의 `rule`·`mixed`는 거절한다. `source_sha256, pdf_sha256, log_sha256`은 원본 바이트의 SHA-256 소문자 16진수다. `style`은 요청한 MODEL_STYLE 객체, `model_sha256`은 모델 쪽에서 실제 로드한 파일 해시, 규칙 쪽에서 null이다. `elapsed_ms`는 요청 직전부터 PDF 본문 수신 완료까지 측정한 양의 밀리초다. |

지면 차이로 원고를 고르면 규칙과 일치하는 모델 판단이 평가에서 빠진다. 두 원고 모두 실제 모델 사용이 확인되면 같은 선택·같은 지면도 익명 표본 두 개로 판독한다. 운영자 키의 묶음별 `same_choices`는 전 문단 선택 ID 일치, `same_pages`는 300dpi 열람 지면 일치를 뜻한다. 판독표의 선호 칸은 비워 두며 동률은 판독자가 표시한다.

**Producer contract (English).** Export both real render responses, their final replay logs and wall-clock receipts from the same pinned runtime. Record the applied path, including any fallback. T14 checks source and artifact hashes, split membership, all paragraph selections, matching candidate sets and selected versus printed line ends. Verified model agreement stays in the fixed cohort. Agreement flags are private, and the reader decides preference or tie. The adapter consumes files locally.

```sh
ev pairs /runs/model-01 /runs/refs /runs/t13-real
ev capture /runs/model-01 /runs/returned-pairs.csv --reader R2
ev timing /runs/model-01
ev timing-decision /runs/model-01 accepted --by U1
ev score /runs/model-01
```

사용자가 실측 분포를 확인한 후에만 시간 결정(`accepted` 또는 `rejected`)을 등록한다. `score`는 모델 승·규칙 승·동률, 기준 지면 판과 잔여 수정 대목, 두 팔의 제약 위반·줄 끝 불일치, 실측 분포를 돌려준다. 줄 끝은 이 평문 코퍼스의 문단별 비공백 글리프 끝 번호를 문서 누적으로 바꿔 PDF와 대조한다. 불일치는 빠진 끝과 추가된 끝의 개수 중 큰 값이다. 제약 위반은 선택 로그의 폭·비율 위반과 PDF 문자 좌표의 사방 여백·한글 자간·어절 공백 위반을 각각 센다. PDF 허용 오차는 0.05 PDF pt다.

모든 쌍의 판독 완료, 모델 승수 우세, 위반·불일치 0, 사용자의 시간 수용이 함께 충족되면 `recommend_model=true`다. 제품 기본값 적용은 총괄의 별도 통합 단계다.
