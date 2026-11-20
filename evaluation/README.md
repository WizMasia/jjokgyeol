# T14 사람 판독 / Blind reading

[사전 규약](../docs/superpowers/specs/2026-11-20-blind-reading-design.md) · [판독자에게 줄 안내](reader-guide.md) · [실행 기록](acceptance.md)

**원고 출처**　`corpus/quay.md`는 자간 비교용, `drawer.md`·`platform.md`는 모델 비교용이다. 세 편 모두 이 작업에서 에이전트가 새로 쓴 혼합 산문이다. 사용자가 프로젝트용 창작과 로컬 평가를 위임했다. 외부 저자·편집자에게서 수집한 원고나 선호 자료는 없다. 세 작품과 개작을 모델 학습, 프롬프트 튜닝, 후보 규칙 조정에서 제외한다. 분리 검사는 제출된 로컬 학습·개발 목록을 대상으로 한다. 사전학습 모델 자료의 전수 확인을 뜻하지 않는다.

### 실행과 응답 / Operator workflow

저장소 루트에서 잠긴 test 이미지로 실행한다. `/runs`는 호스트의 Git 제외 폴더다. 각 실행은 새 이름을 쓴다.

```sh
mkdir -p evaluation/runs
ev() {
  docker run --rm --network none --shm-size=512m \
    -v "$PWD:/opt/jjokgyeol:ro" -v "$PWD/evaluation/runs:/runs" \
    jjokgyeol:mirrored-margins-test python -m evaluation "$@"
}
ev spacing /runs/spacing-02
ev timing /runs/spacing-02
```

진행자는 `reader/`와 판독 안내만 제공한다. `private/key.json`은 원고·조건·원본 PDF 해시·실측 시간을 연결하는 실행별 대응표다. PDF 원본과 이 폴더는 판독자와 분리한다. 자간 판독은 한 묶음당 세 표본, 모델 판독은 한 묶음당 두 표본과 확정 기준 지면 한 장이다.

```sh
ev capture /runs/spacing-02 /runs/returned.csv --reader R1
ev score /runs/spacing-02
```

CSV 열은 `run,group,sample,preferred,crowding,paragraph,reason`이다. 실행·묶음·표본 조합이 정확히 한 번씩 있어야 한다. 등록 시 익명 PDF 무결성을 확인해 응답 원문·별칭과 대응표 해시를 비공개로 고정한다. 응답 전 상태는 `awaiting_reader`다.

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

**Producer contract (English).** Export both real render responses, their final replay logs and wall-clock receipts from the same pinned runtime. Record the applied path, including any fallback. T14 checks source and artifact hashes, split membership, all paragraph selections, identical candidate sets, physical page differences and selected versus printed line ends. The adapter consumes files locally.

```sh
ev pairs /runs/model-01 /runs/refs /runs/t13-real
ev capture /runs/model-01 /runs/returned-pairs.csv --reader R2
ev timing /runs/model-01
ev timing-decision /runs/model-01 accepted --by U1
ev score /runs/model-01
```

사용자가 실측 분포를 확인한 후에만 시간 결정(`accepted` 또는 `rejected`)을 등록한다. `score`는 모델 승·규칙 승·동률, 기준 지면 판과 잔여 수정 대목, 두 팔의 제약 위반·줄 끝 불일치, 실측 분포를 돌려준다. 줄 끝은 이 평문 코퍼스의 문단별 비공백 글리프 끝 번호를 문서 누적으로 바꿔 PDF와 대조한다. 불일치는 빠진 끝과 추가된 끝의 개수 중 큰 값이다. 제약 위반은 선택 로그의 폭·비율 위반과 PDF 문자 좌표의 사방 여백·한글 자간·어절 공백 위반을 각각 센다. PDF 허용 오차는 0.05 PDF pt다.

모든 쌍의 판독 완료, 모델 승수 우세, 위반·불일치 0, 사용자의 시간 수용이 함께 충족되면 `recommend_model=true`다. 제품 기본값 적용은 총괄의 별도 통합 단계다.
