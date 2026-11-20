# 네이티브 첫 교정본 구현 계획

**목표**는 Rust 명령줄에서 마크다운과 저장 양식 JSON을 열어 로컬 작업자로 생성한 PDF를 저장하고, 웹판의 같은 지면과 비교하는 것이다. 이는 Windows 설치판에 앞서 설계에서 정한 첫 단계다. [전체 설계](../specs/2026-11-20-native-desktop-design.md)

Superpowers의 병렬 작업 및 TDD 절차를 사용한다. 제품 코딩은 Luna 또는 Terra가 맡는다. 웹 비교 기준은 `6b8da3abc048caa4a0f9cada97e8b20691795836`의 Docker 앱과 저장소의 기존 PDF다. 개발 및 통합은 `feature/native-desktop`에서 수행한다. 기존 컨테이너·기준 PDF를 덮어쓰지 않는다.

## 작업자 사이의 계약

- 개발용 Rust 명령은 `jjokgyeol-proof --runtime DIR --source FILE --style FILE --output FILE`이다. DIR은 신뢰하는 설치 리소스 폴더다. 작업자는 `DIR/worker/jjokgyeol-worker[.exe]`이고, 인수는 `--runtime DIR`, 현재 디렉터리는 요청마다 새로 만든 전용 폴더다.
- 입력은 UTF-8 `request.json`의 `{"source":"…","style":{…}}`이다. 여기서 style은 **저장용 정수 sp** 형식이다. 결과는 성공 시 `response.pdf`와 종료값 0, 실패 시 0이 아닌 종료값이다. 입력 오류는 표준 오류에 필드와 메시지를 JSON으로 쓴다. 웹 API의 편집용 단위와 데스크톱 파일의 저장용 단위는 각각 기존 검증기를 쓴다.
- `DIR/runtime.json`은 `engine`과 `font_dir` 두 상대 경로를 담는다. 예를 들어 `{"engine":"tex/bin/lualatex.exe","font_dir":"fonts"}`다. 설치 리소스는 읽기 전용이다. 운영 한도는 기존 `app/config/operations.json`을 따른다.

## 동시에 진행할 작업

### A. Rust 실행기 · Terra

- [ ] `desktop/Cargo.toml`, `desktop/src/`, `desktop/tests/`에서 Rust 실행기를 작성한다. 재사용할 수 있는 실행 함수와 개발용 CLI를 제공한다. 실제 사용자 출력 경로를 작업자에게 넘기지 않는다.
- [ ] 누락된 작업자, 중간에 멈춘 작업자, 잘린 PDF, 한글·공백 경로, 이미 있는 출력 파일을 시험한다. 작은 모의 작업자로 실패를 먼저 재현한다. 실패와 시간 초과 시 원래 출력 바이트를 유지한다.
- [ ] `cargo test --manifest-path desktop/Cargo.toml`과 포맷 검사를 통과하고 작업 브랜치에 커밋한다.

### B. 이식 가능한 규칙 조판 작업자 · Terra 또는 Luna

- [ ] `desktop/worker/`에 위 계약의 실행 진입점과 PyInstaller 디렉터리 배포 방법을 구현한다. 패키지에 양식 설정·Lua 템플릿을 포함한다. 웹판의 조판 함수와 파서를 공유한다.
- [ ] `app/typeset.py`에 동봉 엔진과 글꼴을 명시적으로 지정하는 경로를 추가한다. 기존 호출은 같은 설정·행동을 보존한다. 패키지 경로는 현재 작업 폴더와 독립적이어야 한다.
- [ ] 일반 사용자 권한, 심볼릭 링크 없는 작업, 외부 PATH에 TeX가 없는 경우, 잘못된 저장 양식, 훼손된 런타임을 시험한다. 규칙 PDF를 저장하는 성공 경로까지 실행하고 커밋한다.

### C. Windows 검증 및 웹판과의 대조 · Terra 또는 Luna

- [ ] `desktop/qa/`에 두 PDF를 받는 대조 명령을 두고 `tests/pdf_metrics.py`를 재사용한다. 쪽수·줄 문자·글꼴·좌표를 대조한다. 좌표 허용 오차는 0.05 PDF pt다. 첫 차이로 쪽과 줄을 식별하고 실패로 종료한다.
- [ ] 동일 PDF, 다른 지면, 깨진 PDF를 시험한다. 양면 줄 번호 원고의 기준 PDF는 2쪽이고 SHA-256은 `dfe31ad480c731816706431f7be2242f6d7965c86f4e239754e827d92ef30439`다.
- [ ] Windows x64용 개발 검증 절차를 만들고 두 쪽 교정 PDF와 측정값을 보관한다. 실제 측정 여부와 남은 의존성을 명시한다. 서명·릴리즈는 이 단계에 포함하지 않는다.

## 통합 및 다음 단계

- [ ] 작업별 리뷰 후 통합 브랜치에서 Rust 시험, 작업자 시험, PDF 대조를 다시 실행한다. 웹판 원본의 커밋·교정 PDF 해시·서비스 응답을 확인한다.
- [ ] Windows x64에서 실제 결과가 나온 뒤 Tauri 창의 원고 열기·미리보기·저장으로 같은 경로를 연결한다. 동봉 엔진과 설치물의 오프라인 실행, 모델·규칙 선택, macOS·Linux 검증은 전체 설계의 후속 출시 기준을 따른다.
