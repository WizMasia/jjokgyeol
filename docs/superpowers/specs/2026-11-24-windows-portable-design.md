# 쪽결 Windows 포터블 배포

## 사용 흐름

Windows x64 컴퓨터에 시스템 WebView2가 없어도, 사용자가 선택한 쓰기 가능한 폴더에서 쪽결의 원고 편집, `.jjok` 열기·저장, 규칙 조판과 PDF 내보내기를 오프라인으로 사용할 수 있게 한다. 첫 배포의 지원 대상으로 Windows 10 22H2 x64와 Windows 11 x64를 제안한다. AI 조판에는 기존과 같이 설정한 모델에 연결할 수 있어야 한다.

포터블 배포 파일은 `JjokgyeolPortable_<version>.paf.exe` 하나로 제안한다. PortableApps.com Platform에서는 **Apps → Install a New App**으로 이 파일을 선택한다. Platform 없이 사용할 때는 같은 파일을 직접 실행해 폴더를 선택하고, 생성된 `JjokgyeolPortable.exe`를 실행한다. 배포자는 쪽결 GitHub 릴리즈에서 파일을 제공한다. [PortableApps 포터블 앱 설치 방식](https://portableapps.com/support/portable_app)

## 검토한 배포 방식

| 방식 | 적용 범위와 비용 |
| --- | --- |
| 시스템 WebView2를 사용하는 ZIP과 PAF | 포터블 파일 크기를 줄일 수 있다. 사용자가 WebView2가 없는 컴퓨터에서 실행해야 한다는 요구와 맞지 않는다. |
| 고정 WebView2를 담은 ZIP과 PAF | 압축을 직접 풀거나 PortableApps에서 설치할 수 있다. 각각 배포 파일, 설치 검증 및 해시 관리가 필요하다. |
| 고정 WebView2를 담은 PAF | 같은 파일로 단독 설치와 PortableApps 설치를 지원한다. 포터블 검증 경로를 하나로 유지할 수 있다. |

WebView2가 없는 컴퓨터에서 실행한다는 요구에 맞춰 런타임을 동봉한다. PAF 한 개가 두 설치 방식에 충분하다는 사용자 의견을 반영해 포터블 산출물을 하나로 구성한다. [고정 WebView2 배포](https://learn.microsoft.com/microsoft-edge/webview2/concepts/distribution) · [PortableApps 파일 형식](https://portableapps.com/development/portableapps.com_format)

## 앱과 사용자 데이터

- 기존 Windows 배포 작업의 쪽결 실행 파일, 편집기 화면, 조판 작업자, LuaLaTeX, 글꼴과 고지문을 재사용한다. 실행 파일 옆에 기존 조판용 `runtime`을 배치하고, 같은 앱 디렉터리에 Microsoft x64 Fixed Version WebView2와 재배포 고지문을 둔다.
- 공식 PortableApps.com Installer와 Launcher를 사용한다. 쪽결 배포자 정보, 버전, 아키텍처, 메뉴 아이콘과 실행 경로를 `AppInfo`에 둔다. Microsoft CAB와 빌드 도구의 출처 및 무결성을 검증하고 재배포 조건을 각 패키지의 고지문에 반영한다. 런타임은 검증된 패치 버전으로 고정하고 이후 릴리즈에서 보안 업데이트를 따라간다.
- 포터블 실행에서만 번들 안의 WebView2 경로와 `Data` 아래의 사용자 데이터 경로를 지정한다. 출판 기본값과 모델 설정이 폴더를 옮긴 뒤에도 남는지 확인한다. 쪽결 문서는 사용자가 지정한 위치에 저장한다. 앱에서 세션 메모리에 두는 모델 API 키는 포터블 설정으로 저장하지 않는다. 동일한 폴더에 업데이트할 때는 `Data`를 보존한다.
- 배포 기준 경로는 사용자 소유의 로컬 NTFS 폴더로 잡는다. Windows와 동봉 런타임이 요구하는 읽기·실행 권한을 해당 런타임 경로에만 적용하고 확인한다. 일반 사용자 권한으로 실행을 검증하며 샌드박스를 유지한다. 네트워크 공유에는 고정 런타임 제약이 있으며, 다른 파일 시스템이나 이동식 드라이브에서의 지원은 검증 결과에 따라 문서에 명시한다. [WebView2 폴더 권한](https://learn.microsoft.com/microsoft-edge/webview2/concepts/distribution) · [예정된 WebView2 권한 변경](https://learn.microsoft.com/en-us/microsoft-edge/webview2/release-notes/runtime/156)

## 배포와 수락 기준

- 기존 Windows 설치 프로그램과 함께 PAF를 생성하고 Windows 산출물 목록과 `SHA256SUMS` 검증에 포함한다. GitHub 시험판에는 세 플랫폼 설치물과 Windows 포터블 파일을 같은 커밋으로 제공한다.
- 시스템 WebView2가 없는 Windows x64 시험 환경에서 네트워크를 끊고 일반 사용자로 PAF를 단독 설치해 앱을 실행한다. 실행 중인 WebView2 경로가 동봉한 바이너리인지 확인하고, PDF 미리보기와 내보내기, 한글 원고와 공백이 포함된 경로에서 `.jjok` 저장·열기를 검증한다. 기존 설치판의 PDF 대조 절차를 재사용한다.
- PortableApps Platform 메뉴에서 같은 PAF를 설치해 앱이 등록되고 실행되는지 확인한다. 폴더 이동과 업데이트 후 기본값과 문서를 다시 연다. 시스템 WebView2가 설치된 CI에서의 검사와 WebView2가 없는 환경에서의 검증 결과를 각각 기록한다.
- CI가 기존 흐름대로 초안을 만들 수 있게 한다. 배포자가 공개 전에 최종 PAF를 깨끗한 Windows 환경에서 검증하고, 시험 환경과 결과 및 초안 파일의 SHA-256을 대조한다.
