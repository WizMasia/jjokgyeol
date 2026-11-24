# 네이티브 시험판 배포

설치할 때는 공개 릴리즈의 태그를 고르고 해당 페이지의 **Assets**에서 운영체제에 맞는 파일을 받습니다. 설치 파일 이름은 여러 시험판에서 같으므로 실행 후 편집기 상단의 버전을 태그와 대조하세요. 기존 원고는 **쪽결 문서 저장**으로 보관한 뒤 새 앱에서 **쪽결 문서 열기**로 다시 열 수 있습니다. [공개 시험판 목록](https://github.com/WizMasia/jjokgyeol/releases)

## Windows 포터블 설치

GitHub 시험판의 **Assets**에서 태그와 같은 버전의 `JjokgyeolPortable_<version>.paf.exe`를 받습니다. 기존 설치판을 쓰려면 `jjokgyeol-windows-x64-setup.exe`를 실행합니다. 설치판에는 Evergreen WebView2 오프라인 설치 프로그램이 포함됩니다. [쪽결 시험판](https://github.com/WizMasia/jjokgyeol/releases)

- PortableApps.com Platform을 쓰는 경우 **Apps → Install a New App**에서 받은 PAF를 선택합니다. 설치 후 메뉴에서 쪽결을 실행합니다.
- 단독으로 쓰는 경우 PAF를 직접 실행해 설치 폴더를 고르고, 설치된 `JjokgyeolPortable.exe`를 실행합니다.

지원 범위는 Windows 10 22H2 x64와 Windows 11 x64의 사용자 쓰기 가능한 로컬 고정 NTFS 드라이브입니다. 설치와 실행에는 일반 사용자 권한을 사용합니다. 동봉된 고정 WebView2로 편집과 규칙 조판을 오프라인에서 사용할 수 있습니다. AI 조판은 설정한 모델에 연결할 수 있어야 합니다. ARM64, 네트워크 공유와 이동식 드라이브는 지원 범위 밖입니다.

새 문서 기본값과 모델 연결 설정은 포터블 폴더의 `Data`에 남습니다. 모델 API 키는 세션 메모리에만 보관됩니다. 문서는 저장할 때 선택한 위치에 있습니다. 앱을 닫고 포터블 폴더 전체를 옮기면 설정도 함께 이동합니다. 업데이트 전에 `Data`와 문서를 백업하고, 새 PAF를 같은 포터블 폴더에 설치합니다.

## 배포자용 빌드

원본 저장소 `WizMasia/jjokgyeol`에 푸시할 권한이 있는 배포자가 `main`과 `feature/native-desktop`에 통합된 커밋에서 배치를 시작합니다. `desktop/gui/tauri.conf.json`의 전체 버전에 v를 붙여 태그를 정합니다. CI는 공개된 main의 커밋과 태그를 확인합니다.

```sh
tag="v$(jq -r .version desktop/gui/tauri.conf.json)"
git push origin "HEAD:refs/heads/release-batch/$tag"
```

세 플랫폼 작업은 같은 커밋의 웹 화면, Rust 앱, Python 작업자, TeX Live 2022, 나눔 글꼴을 묶습니다. 각 운영체제에서 설치물을 풀거나 설치한 뒤 앱을 실행하고, 동봉된 조판기로 양면 줄 번호 원고를 출력해 기준 PDF의 쪽 수·글자·좌표와 비교합니다. 같은 설치 경로에서 문단 여백과 오프라인 규칙 대체도 검사합니다. 설치본에서 작성 날짜가 한 줄인지, 표의 각 셀이 올바른 열에 놓였는지도 검사합니다. 편집기 상단에서 릴리즈 태그를 확인할 수 있습니다. Mac 배포판은 고유한 앱 식별자를 사용하고 로컬 개발판은 ‘쪽결 개발판’으로 설치됩니다.

| 환경 | 파일 | 설치 |
| --- | --- | --- |
| Windows x64 | `jjokgyeol-windows-x64-setup.exe` | 설치 프로그램 실행. WebView2 오프라인 설치 프로그램 포함 |
| Windows x64 포터블 | `JjokgyeolPortable_<version>.paf.exe` | PAF 단독 실행 또는 PortableApps.com Platform에서 설치 |
| Apple Silicon Mac | `jjokgyeol-macos-arm64.dmg` | 디스크 이미지를 열고 쪽결을 응용 프로그램으로 복사 |
| Ubuntu 24.04 x86_64 | `jjokgyeol-linux-x86_64.deb` | `sudo apt install ./jjokgyeol-linux-x86_64.deb` |

세 플랫폼의 설치 검사가 모두 성공하면 배치가 같은 커밋의 GitHub 시험판 초안을 만들고, 설치물 4개와 `SHA256SUMS`의 업로드 해시를 대조합니다. 배포자는 초안의 대상 커밋과 파일을 확인하고 아래의 오프라인 수락 검사를 통과한 초안을 시험판으로 공개합니다.

```sh
gh release view "$tag" -R WizMasia/jjokgyeol --json isDraft,isPrerelease,targetCommitish,assets
gh release edit "$tag" -R WizMasia/jjokgyeol --draft=false --prerelease --latest=false
```

중단된 업로드는 같은 실행의 실패한 작업을 다시 실행해 이어갑니다. 설치물을 다시 빌드하거나 대상 커밋이 바뀌면 새 순번을 사용합니다. Mac 앱은 임시 서명(ad hoc)이며 Apple 공증 전입니다. Windows 설치 프로그램은 게시자 서명 전입니다.

Windows 작업은 한 번 빌드한 실행 파일과 조판 런타임으로 일반 설치판과 PAF를 만듭니다. 빌드에는 x64 7-Zip이 필요하며, 해시를 검증한 공식 PortableApps.com Launcher Generator와 Installer Generator를 사용합니다. Installer Generator가 사용하는 NSIS 템플릿은 쓰기 전에 경로를 검사하고 명시적 `/S` 검증 실행을 허용하도록 빌드 중 조정합니다. 공개 버전과 PAF 파일명에는 Tauri 설정의 전체 버전을 사용합니다. PAF의 숫자 버전은 네 번째 자리에 시험판 순번을 쓰며, 같은 기본 버전의 정식판에는 65535를 사용합니다. 포터블 런타임과 도구를 업데이트할 때 `desktop/release/portable.lock.json`의 버전·공식 URL·SHA-256을 함께 갱신하고 같은 검사를 다시 실행합니다. 고정 WebView2는 새 PAF를 설치할 때 갱신됩니다. [Microsoft 고정 버전 배포 문서](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)

배포 관련 경로를 바꾸는 `main` 대상 PR에서는 같은 배포 워크플로의 Windows 작업만 실행합니다. Windows Server 러너에 이미 설치된 Evergreen WebView2를 사용해 기존 설치판을 검증한 뒤, PAF는 자체 WebView2 프로세스 경로와 조판 결과를 별도로 확인합니다. 초안 생성은 공개 `main` 커밋과 일치하는 `release-batch/v*-preview.*` 브랜치 푸시에서 실행합니다.

TeX 실행 파일은 [TeX Live 2022 보관소](https://ftp.math.utah.edu/pub/tex/historic/systems/texlive/2022/)에서 가져오고 조판 자료는 저장소 Dockerfile의 Debian 패키지를 사용합니다. 구성 요소별 고지문은 설치된 `runtime/licenses`에 포함합니다.


## 깨끗한 Windows에서 포터블 수락 검사

시스템 WebView2가 없는 Windows 10 22H2 x64 및 Windows 11 x64의 깨끗한 시험 환경을 준비합니다. 초안의 PAF와 `SHA256SUMS`, 해당 커밋의 저장소, x64 PowerShell 7, Python과 비교 도구를 미리 준비합니다. 설치·실행·조판 검사는 네트워크를 끊은 뒤 로그인한 일반 사용자 세션에서 수행합니다.

```powershell
# Run from the release commit's checkout before disconnecting.
python -m pip install --require-hashes --only-binary=:all: -r desktop/qa/requirements-compare.lock
# Read and accept the terms in the interactive PAF before using unattended mode.
# Disconnect the test machine, then substitute the exact filename from the draft.
pwsh -NoProfile -File desktop/qa/runtime/verify-portable.ps1 -Package 'C:\검증 파일\JjokgyeolPortable_<version>.paf.exe'
```

명령은 PAF의 SHA-256을 출력하고, 한글과 공백이 있는 경로에 설치한 뒤 실행 중인 브라우저의 바이너리와 프로필 위치를 확인합니다. 같은 PAF를 다시 설치하고 폴더를 옮겨도 `Data`의 시험 파일이 남아 있는지 확인하며, 실제 동봉 작업자가 출력한 양면 줄 번호 PDF를 기준 파일의 글자와 좌표에 대조합니다. 이전 PAF가 있으면 `-PreviousPackage <path>`를 추가해 이전 버전에서의 업데이트를 검사할 수 있습니다. 기본값은 같은 버전을 덮어쓰는 검사입니다.

같은 PAF를 단독 설치해 본문 글자 크기를 바꾸고 **새 문서 기본값으로 저장**을 누릅니다. 한글 원고를 한글과 공백이 포함된 경로에 `.jjok`로 저장하고 PDF 미리보기와 내보내기를 실행합니다. 앱을 닫고 폴더를 옮긴 뒤 저장한 기본값으로 새 문서가 시작되고, `.jjok`를 다시 열었을 때 원고와 양식이 유지되는지 확인합니다. 같은 위치에 PAF를 다시 설치해 이 항목을 반복합니다. PortableApps.com Platform에서도 동일한 PAF를 설치해 메뉴 등록 및 실행을 확인합니다.

OS 버전과 빌드, 일반 사용자와 오프라인 상태, 명령, 단독 설치 및 Platform 설치 결과를 기록합니다. 명령에 표시된 SHA-256을 초안의 `SHA256SUMS`와 대조합니다. 호스팅 CI의 Windows Server 결과와 이 검사 결과를 각각 기록합니다.
