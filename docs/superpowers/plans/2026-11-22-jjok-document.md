# 쪽결 작업 문서 구현 계획

Superpowers의 subagent-driven-development 절차로 구현한다. 기준은 [승인한 문서 설계](../specs/2026-11-22-jjok-document-design.md)다.

**목표**는 웹과 네이티브 앱에서 같은 ZIP 문서를 오갈 수 있게 하는 것이다. 양식 변환과 파일 저장은 각 편집기가 이미 쓰는 경로를 이용하고, ZIP 처리는 두 환경에 함께 배포되는 정적 자바스크립트로 구현한다. 패키징 중 외부 CDN에 의존하지 않는다.

## 공통 제약과 검토 사항

형식은 `.jjok` 버전 1이다. BOM·혼합 줄바꿈과 정수 양식의 정밀도, 모델 지면의 실제 PDF 바이트, 다른 원고로 바뀐 뒤 도착한 응답, 포함 해제 뒤 재저장, 복원 실패 시 미저장 편집을 각각 검증한다.

## 1. 문서 코덱

- [ ] `tests/test_jjok_archive.py`에서 표준 ZIP과의 양방향 호환, 메타데이터와 PDF 포함 여부, 중복·손상·용량 한도에 대한 테스트를 먼저 실패시킨다.
- [ ] `app/static/jjok-archive.js`에 `JjokArchive.pack({source,style,includeProof,proof}, limits)`와 `unpack(file, limits)`를 만든다. source는 BOM을 포함할 수 있는 문자열, style은 저장용 정수 객체, proof는 null 또는 `{blob,state,mode}`다. state는 current/stale, mode는 rule/model/mixed다. 반환값은 각각 Blob과 동일 구조의 문서다. limits는 `{request,file}` 바이트다. 기존 운영 설정에서 가져온다.
- [ ] 고정 버전 ZIP 라이브러리와 라이선스를 로컬 번들에 포함한다. 표준 ZIP 독자로 저장 결과를 독립 대조하고 코덱 테스트를 통과시킨다.

## 2. 편집기에서 저장과 열기

- [ ] `tests/test_jjok_editor.py`에서 파일 선택, PDF 제외와 재열기, 수정 전 지면, 실패·취소·늦은 응답을 정상 화면 조작으로 검증하는 실패 테스트를 추가한다.
- [ ] `app/static/index.html`, `editor.js`와 문서 전용 스크립트를 연결한다. 기존 원고·양식 변경 확인과 PDF 표시·다운로드, 네이티브 저장 대화상자를 재사용한다. 저장은 선택한 PDF와 편집 버전을 한 시점에 잡고, 복원은 전체 검증 뒤에 한 번에 적용한다.
- [ ] 웹과 데스크톱이 함께 읽는 설정에 크기 한도를 노출한다. 기본 포함, 같은 문서의 선택 유지, 새 원고의 기본값 복원을 확인한다.

## 3. 통합과 배포

- [ ] `desktop/gui/frontend/`의 실제 프런트엔드 빌드에서 웹과 네이티브 간 왕복을 검사한다. 기존 기준 PDF와 별도 저장 기능도 회귀 검사한다.
- [ ] 전체 Docker 검사, 네이티브 작업자·Rust·프런트엔드 검사, 설치 패키지의 실행과 기준 PDF 대조를 진행한다. README에 문서 열기·저장 방법을 기록한다.
- [ ] 검증한 커밋을 main과 네이티브 브랜치에 통합한다. 다음 미사용 preview 번호로 배포 배치를 시작하고 세 설치 파일과 SHA256SUMS를 검증한 뒤 GitHub 시험판을 발행한다. 릴리즈 태그가 검증한 커밋을 가리키는지 대조한다.
