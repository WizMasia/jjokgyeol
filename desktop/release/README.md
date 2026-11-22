# 네이티브 내부 시험판 배치

`feature/native-desktop`에서 검증할 커밋을 `release-batch/v<앱 버전>-preview.<순번>` 브랜치로 푸시합니다. 예를 들어 앱 버전 0.1.0의 첫 시험판은 아래 명령으로 시작합니다.

```sh
git push origin HEAD:refs/heads/release-batch/v0.1.0-preview.1
```

세 작업은 같은 커밋의 웹 화면, Rust 앱, Python 작업자, TeX Live 2022, 나눔 글꼴을 묶습니다. 각 운영체제에서 설치물을 풀거나 설치한 뒤 앱을 실행하고, 동봉된 조판기로 양면 줄 번호 원고를 출력해 기준 PDF의 쪽 수·글자·좌표와 비교합니다. 각 패키지는 검사를 통과해야 업로드됩니다.

| 환경 | 파일 | 설치 |
| --- | --- | --- |
| Windows x64 | `jjokgyeol-windows-x64-setup.exe` | 설치 프로그램 실행. WebView2 오프라인 설치 프로그램 포함 |
| Apple Silicon Mac | `jjokgyeol-macos-arm64.dmg` | 디스크 이미지를 열고 쪽결을 응용 프로그램으로 복사 |
| Ubuntu 24.04 x86_64 | `jjokgyeol-linux-x86_64.deb` | `sudo apt install ./jjokgyeol-linux-x86_64.deb` |

세 설치물이 모두 준비되면 비공개 저장소의 Releases에 시험판 초안과 `SHA256SUMS`가 생성됩니다. 초안은 저장소 쓰기 권한이 있는 사람이 확인할 수 있습니다. 이미 만든 초안을 수정해야 할 경우 새 순번으로 배치를 시작합니다. 네이티브 시험판은 규칙 조판을 사용합니다. Mac 앱은 임시 서명(ad hoc)이며 Apple 공증 전입니다. Windows 설치 프로그램은 게시자 서명 전입니다.

TeX 실행 파일은 [TeX Live 2022 보관소](https://ftp.math.utah.edu/pub/tex/historic/systems/texlive/2022/)에서 가져오고 조판 자료는 저장소 Dockerfile의 Debian 패키지를 사용합니다. 구성 요소별 고지문은 설치된 `runtime/licenses`에 포함합니다.
