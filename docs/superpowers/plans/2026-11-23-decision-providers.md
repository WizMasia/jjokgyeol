# 디시전 모델 연결 구현 계획

기존 규칙 PDF와 문단 후보 검증을 재사용해 다섯 가지 모델 API를 웹과 네이티브 편집기에 연결한다. 승인된 설계는 docs/superpowers/specs/2026-11-23-decision-providers-design.md에 있다.

공통 연결 객체는 provider, base_url, model, api_key를 가진다. provider 값은 responses, chat, gemini, claude, ollama다. 명시적 규칙은 {"provider":"rule"}이며, 연결 객체 생략은 기존 운영자 기본값을 따른다. 서버 응답에는 고정 사유 코드만 포함한다.

- [x] Python 표준 라이브러리 어댑터. app/ai/providers.py와 app/config/decision-providers.json에 형식별 주소, 요청, 인증과 완료된 답의 해석을 구현한다. tests/test_decision_providers.py에서 각 형식의 요청을 로컬 HTTP 서버로 받고 정상·거절·시간 초과·잘못된 후보·주소 제한·리디렉션을 검사한다. 실패를 확인하고 구현한 뒤 pytest로 다시 검사한다.
- [x] 공통 조판 및 웹. app/ai/decision.py에서 측정·규칙 PDF를 보존하고 모델 선택·재조판을 분리한다. app/jobs.py 및 app/server.py에서 요청별 연결을 메모리로만 전달하고 POST /api/decision/check와 비교 응답 사유를 제공한다. 기존 시간 제한 테스트와 새 연결별 격리·오류·비밀정보 검사로 검증한다.
- [x] 네이티브. desktop/worker 및 desktop의 Rust 감독 코드·Tauri 명령에서 번들 엔진을 쓰는 모델 조판과 연결 확인을 추가한다. 연결을 표준입력으로 전달한다. GUI용 바이너리 응답은 4바이트 big-endian JSON 길이, UTF-8 메타데이터(decision, reason, rule_bytes), 규칙 PDF, 선택 PDF 순서다. 기존 CLI·작업자 테스트, 새 명령 테스트와 cargo test로 검증한다.
- [x] 공통 편집기. 기존 설정과 웹·네이티브 어댑터에 연결 선택, 적용, 취소, 연결 확인을 추가한다. 라우트, 세션 격리, 키 재입력, 늦은 응답, 파일 직렬화 제외를 실제 브라우저 테스트로 검증한다.
- [x] 통합. 고정 원고에서 실제 PDF의 줄 끝을 규칙·모델과 비교하고 양쪽 진입점을 검사한다. 앱에 포함되는 리소스를 실제 빌드로 확인한다. 운영자 주소 목록과 사용자 설정을 README에 기록하고 전체 회귀 검사 및 변경 검토를 거친다.

## 통합 검사 기록

- 다섯 API 형식 각각을 로컬 HTTP 모의 서버에 연결했다. 웹 조판 API와 macOS의 Rust 실행기·동봉된 작업자·LuaLaTeX로 같은 원고를 조판해 첫 줄이 규칙판 9글자, 지정한 모델판 7글자인지 PDF에서 읽었다. 같은 후보를 선택한 웹·Mac PDF의 글꼴, 줄 끝과 글자 좌표는 0.05pt 이내에서 일치했다.
- 실제 로컬 Chat Completions 서버에서 `google/gemma-4-e2b`의 예제 연결 확인과 웹·네이티브 조판을 실행했다. 같은 모델도 호출에 따라 다른 유효 후보를 골랐다. 외부 유료 서비스의 계정 인증과 결제는 이 검사 범위 밖이다.
- macOS DMG를 읽기 전용으로 마운트해 앱 서명을 검사하고 설치물 안의 작업자로 규칙·모델 PDF를 생성했다. Linux에서도 Rust 작업자 감독 검사를 실행했다. Windows x64 감독 코드와 테스트는 교차 컴파일로 확인했다.
- Docker 전체 회귀 검사에서 1,347건 통과, 62건 건너뜀을 확인했다. 이후 기존 운영자 모델의 HTTP 429 회귀 2건을 추가하고 관련 검사 86건을 다시 통과했다. 최종 DMG의 작업자 검사 37건, 네이티브 편집기 검사 9건과 하위 검사 2건, Tauri 검사 17건, macOS와 Linux의 Rust 실행기 검사 각 16건이 통과했다.
