# 네이티브 시험판 배포

설치할 때는 공개 릴리즈의 태그를 고르고 해당 페이지의 **Assets**에서 운영체제에 맞는 파일을 받습니다. 설치 파일 이름은 여러 시험판에서 같으므로 실행 후 편집기 상단의 버전을 태그와 대조하세요. 기존 원고는 **쪽결 문서 저장**으로 보관한 뒤 새 앱에서 **쪽결 문서 열기**로 다시 열 수 있습니다. [공개 시험판 목록](https://github.com/WizMasia/jjokgyeol/releases)

## 배포자용 빌드

원본 저장소 `WizMasia/jjokgyeol`에 푸시할 권한이 있는 배포자가 `main`과 `feature/native-desktop`에 통합된 커밋에서 배치를 시작합니다. `desktop/gui/tauri.conf.json`의 전체 버전에 v를 붙여 태그를 정합니다. CI는 공개된 main의 커밋과 태그를 확인합니다.

```sh
tag="v$(jq -r .version desktop/gui/tauri.conf.json)"
git push origin "HEAD:refs/heads/release-batch/$tag"
```

세 작업은 같은 커밋의 웹 화면, Rust 앱, Python 작업자, TeX Live 2022, 나눔 글꼴을 묶습니다. 각 운영체제에서 설치물을 풀거나 설치한 뒤 앱을 실행하고, 동봉된 조판기로 양면 줄 번호 원고를 출력해 기준 PDF의 쪽 수·글자·좌표와 비교합니다. 같은 설치 경로에서 문단 여백과 오프라인 규칙 대체도 검사합니다. 설치본에서 작성 날짜가 한 줄인지, 표의 각 셀이 올바른 열에 놓였는지도 검사합니다. 편집기 상단에서 릴리즈 태그를 확인할 수 있습니다. Mac 배포판은 고유한 앱 식별자를 사용하고 로컬 개발판은 ‘쪽결 개발판’으로 설치됩니다.

| 환경 | 파일 | 설치 |
| --- | --- | --- |
| Windows x64 | `jjokgyeol-windows-x64-setup.exe` | 설치 프로그램 실행. WebView2 오프라인 설치 프로그램 포함 |
| Apple Silicon Mac | `jjokgyeol-macos-arm64.dmg` | 디스크 이미지를 열고 쪽결을 응용 프로그램으로 복사 |
| Ubuntu 24.04 x86_64 | `jjokgyeol-linux-x86_64.deb` | `sudo apt install ./jjokgyeol-linux-x86_64.deb` |

세 플랫폼의 설치 검사가 모두 성공하면 배치가 같은 커밋의 GitHub 시험판 초안을 만들고, 설치물 3개와 `SHA256SUMS`의 업로드 해시를 대조합니다. 배포자는 초안의 대상 커밋과 파일을 확인하고 시험판으로 공개합니다.

```sh
gh release view "$tag" -R WizMasia/jjokgyeol --json isDraft,isPrerelease,targetCommitish,assets
gh release edit "$tag" -R WizMasia/jjokgyeol --draft=false --prerelease --latest=false
```

중단된 업로드는 같은 실행의 실패한 작업을 다시 실행해 이어갑니다. 설치물을 다시 빌드하거나 대상 커밋이 바뀌면 새 순번을 사용합니다. Mac 앱은 임시 서명(ad hoc)이며 Apple 공증 전입니다. Windows 설치 프로그램은 게시자 서명 전입니다.

TeX 실행 파일은 [TeX Live 2022 보관소](https://ftp.math.utah.edu/pub/tex/historic/systems/texlive/2022/)에서 가져오고 조판 자료는 저장소 Dockerfile의 Debian 패키지를 사용합니다. 구성 요소별 고지문은 설치된 `runtime/licenses`에 포함합니다.
