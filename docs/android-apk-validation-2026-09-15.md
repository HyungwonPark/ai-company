# AI Company 서명 APK 전달·검증 기록

> 후속 정정: 아래 내용은 0.1.0 전달·도메인 적용 당시 기록이다. 이후 사용자가 아이콘을 누르면 즉시 종료됨을 보고했고, 동일 서명 APK로 필수 Activity 선언 누락을 재현했다. **0.1.0 대신 [수정 APK 0.1.1](https://github.com/HyungwonPark/ai-company/releases/download/android-v0.1.1/ai-company-0.1.1.apk)을 업데이트 설치한다.** [재현·수정·실제 Android 실행 결과](android-startup-fix-2026-09-15.md)에 현재 상태를 기록했다. 아래 당시의 실기기 미검증 표시는 이 후속 보고를 부정하지 않는다.

2026-09-15. 사용자 요청에 따라 **설치 가능한 실제 서명 APK를 GitHub 시험 릴리스로 게시했다.** APK 전달, 도메인 신뢰 연결, 실기기 사용 검증의 완료 여부를 구분한다.

후속 승인 **“인증서 연결 경로만 적용 승인”**에 따라 공개 assetlinks 경로도 적용했다. HTTPS GET·HEAD 200과 실제 APK 인증서 일치, Google Digital Asset Links의 `linked=true`까지 통과했다. 휴대폰에서 설치·실행·로그인·뒤로가기·테마·승인 화면을 조작한 결과는 아직 없다.

[APK 직접 다운로드](https://github.com/HyungwonPark/ai-company/releases/download/android-v0.1.0/ai-company-0.1.0.apk) · [릴리스](https://github.com/HyungwonPark/ai-company/releases/tag/android-v0.1.0) · [구조화된 검증 기록](evidence/android-apk-2026-09-15.json)

## 전달한 APK

| 항목 | 실제 검사 결과 |
|---|---|
| 이름 | AI Company |
| 파일 | `ai-company-0.1.0.apk`, 3,732,083 bytes |
| 버전 | `0.1.0`, versionCode `1` |
| 앱 ID | `cloud.hyungwon.aicompany` |
| 시작 주소 | `https://hyungwon.cloud/` |
| Android | minSdk 26(8.0 이상), targetSdk 36, native ABI 제약 없음 |
| APK 기준 커밋 | `dfe5e3b1d5d77db1d9122dbb9af1e61fc92b55c9` |
| APK SHA-256 | `8ef9685be3ad98408c0e113bf6ed069d550f058b959d61a5dd9de989e8fd0553` |
| 인증서 SHA-256 | `E0:22:C4:1F:03:95:9B:51:54:C0:85:8C:22:F7:25:EF:49:D0:B1:F6:2F:DB:39:92:64:0F:30:86:E6:2E:98:79` |
| 서명 | 실제 `apksigner verify`: v2·v3 PASS, RSA 3072, 서명자 1개 |
| 공개 다운로드 | 인증 없이 HTTP 200, 재다운로드 SHA-256 일치 |

기준은 실제 컴파일된 APK다. Manifest에서 패키지·버전·시작 URL·HTTPS intent·권한·debuggable=false·allowBackup=false·usesCleartextTraffic=false를 검사했다. 권한은 INTERNET과 앱 자체 AndroidX receiver 권한뿐이다. 모든 비-META-INF 항목의 해시가 서명 전후 일치하며, 압축하지 않은 199개 항목의 4-byte 정렬도 확인했다.

앱의 `asset_statements` 메타데이터는 실제 컴파일 리소스 `0x7f0d001e`를 참조한다. JSON의 `delegate_permission/common.handle_all_urls`, `namespace=web`, `site=https://hyungwon.cloud`가 일치한다. 이후 검사 도구에도 이 참조·JSON 대조를 추가했다. 해당 도구·서명 도구 수정은 APK 앱 코드를 바꾸지 않으며, 게시 APK의 기준 커밋은 위 `dfe5e3b`로 고정한다. 실제 사용한 서명 도구 파일 SHA-256은 릴리스 `release.json`에 남겼다.

## 빌드·서명 근거

[성공한 Android APK CI](https://github.com/HyungwonPark/ai-company/actions/runs/34956647584)는 workflow ID `358622511`, 경로 `.github/workflows/android.yml`, run attempt 1이다. run의 head와 산출물 이름의 기준 커밋을 대조했다. artifact ID `10391462472`의 ZIP digest `cb56f9ffb5cb0957bf57f86ada3cc6f748bb215cdcae4717e72dba95dcffe8dc`가 GitHub API 값과 실제 다운로드 내용에 일치했다. unsigned APK SHA-256은 `2f9de1267fd06ab8826a90e294e8311686e31bcda7f634ec19de8c6b5dd9da78`이다.

서버는 ARM이고 기존 공개 Android 도구 이미지는 amd64였다. 최소 실행이 `exec format error`로 실패했다. 전역 binfmt나 privileged 컨테이너를 활성화하지 않고 GitHub hosted runner에서 unsigned APK를 빌드했다. 서버에서는 공식 Temurin ARM JRE 17과 공개 SDK의 `apksigner.jar`로 서명했다. JRE 다운로드 SHA-256과 실제 서명 도구 해시를 보존했다.

초기 빌드는 API 26에 없는 `windowLightNavigationBar` 속성으로 Lint 실패했다. 이 속성을 `values-v27`로 분리한 뒤 assemble·lint가 통과했다. Lint의 잔여 경고 8개는 target API/Gradle/Browser 업데이트 권고, 중복 label, backup rules 권고, v26 폴더, 사용하지 않는 launch_url 2건이다. 오류를 숨기거나 lint baseline을 만들지 않았다. 최소 Android 8.0 및 최신 버전의 실제 실행 호환성은 실기기 검증으로 별도 확인해야 한다.

로컬 서명 첫 시도는 같은 한 줄 비밀번호 파일을 두 번 읽는 `--key-pass`에서 EOF로 실패했다. PKCS12의 기존 저장소 비밀번호를 재사용하도록 수정한 뒤 성공했다. 키·암호를 교체하거나 출력하지 않았다. 키와 비밀번호는 Git 밖, 디렉터리 0700·파일 0600이며 CI·릴리스에 업로드하지 않았다. 저장소 밖 같은 서버에 백업을 만들었으나 **서버 외부 백업·복구 검증은 미완료**다.

참고한 커플 앱 코드는 `HyungwonPark/edward-talenta`의 `agent/initial-mobile-design`, 커밋 `30dfeab4e38cab7d98c802329312dce0381110d6`이다. 해당 TWA의 표준 실행·도메인 선언 방식을 참고했고 커플 앱의 build/publish 스크립트는 실행하지 않았다. 기존 앱 ID `life.talentaedward.app` 및 공개 인증서 `F3:1D:EF:DA:93:2E:E5:BC:38:EE:59:48:CB:2F:D5:7F:0C:70:22:20:CD:BD:DC:7B:AA:D3:8F:DA:AA:CD:BC:F9`와 새 앱의 ID·인증서가 다르다.

## 도메인 연결: 적용 전 상태와 정확한 적용안

2026-09-15 10:24 UTC 초기 읽기 검사에서 공개 홈은 HTTP 200, `https://hyungwon.cloud/.well-known/assetlinks.json`은 **HTTP 400 / invalid_path**였다. 당시 웹사이트→앱 신뢰 연결이 차단된 사실을 보존한다. [Android 공식 조건](https://developer.android.com/training/app-links/configure-assetlinks)은 공개 HTTPS JSON, 리디렉션 없음, 실제 배포 APK 인증서 일치를 요구한다. [Chrome TWA 연결 안내](https://developer.chrome.com/docs/android/trusted-web-activity/integration-guide)에 따라 연결 전에는 주소 표시줄이 있는 Custom Tab으로 열릴 수 있다. 검증 우회 플래그를 사용하지 않는다.

적용 대상은 기존 `/home/edward/talenta-site/Caddyfile`의 `hyungwon.cloud` 블록 안 `reverse_proxy 172.30.88.3:8766` 한 줄이다. 이를 [검토 가능한 Caddy 설정 조각](../deploy/examples/ai-company-android-association.caddy)으로 교체한다. 실제 APK 지문에서 만든 [정확한 JSON](../deploy/examples/ai-company-assetlinks.json)을 GET·HEAD `/.well-known/assetlinks.json`에만 `200 application/json`, Cache-Control 300초로 제공한다. 나머지 경로는 기존 upstream으로 보낸다.

| 적용안 식별 | SHA-256 |
|---|---|
| 검사한 현재 전체 Caddyfile | `519ea3c21405e41c65ba01dd67d74d7c7b80a926762151d75cc456d9a1685261` |
| 준비한 전체 Caddyfile | `586a34ed731e56181dc2ec1dd938b37ef3f0498b6e4dcb63b6a3f8f348baab63` |
| 공개할 JSON 파일 | `901ee9cee7d35d8fe61d5669eac163bca768d3856f14ff5856982e05c4c73e67` |

기존 커플 앱 설정 구간은 바이트 단위로 보존됐다. 운영 Caddy와 같은 이미지 `sha256:4c6e91c6ed0e2fa03efd5b44747b625fec79bc9cd06ac5235a779726618e530d`로 전체 설정 validate가 통과했다. 별도 `network=none`, 공개 포트 0, capability 제거·권한 상승 금지 컨테이너의 loopback에서 GET·HEAD 200과 정확한 JSON·인증서를 대조했다. POST·다른 well-known 경로·홈은 기존 upstream으로 갔으며, 격리된 검사에서는 upstream 연결이 차단돼 예상대로 502였다. **이는 공개 HTTPS 적용 결과가 아니다.** 테스트 컨테이너는 종료·삭제했다.

승인 후 적용 순서는 다음으로 한정한다.

1. 현재 Caddyfile 해시·컨테이너 상태·두 도메인 HTTPS·커플 assetlinks·PR #10 pending을 재확인한다. 현재 해시가 달라졌으면 덮어쓰지 않고 적용안을 다시 만든다. 원본은 새 백업 파일로 보존한다.
2. 검증한 전체 후보를 기존 파일의 inode·소유권·권한을 유지하며 기록·fsync한다. 단일 파일 bind mount이므로 파일을 rename으로 교체해 컨테이너가 이전 inode를 읽게 하지 않는다.
3. 기존 Caddy 컨테이너에서 설정 validate 후 `caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile`로 graceful reload한다. 컨테이너 교체·재시작은 하지 않는다.
4. 실제 공개 GET·HEAD 200, JSON content type, 리디렉션 없음, 내려받은 APK의 앱 ID·실제 인증서와 일치를 검사한다. 기존 로그인·Host/Origin/CSRF·세션 쿠키, 커플 서비스와 assetlinks, 컨테이너 health도 대조한다.
5. 적용·검증 실패 시 이번 백업의 바이트를 같은 inode에 복원하고 validate·graceful reload한 뒤 기존 상태를 재확인한다. 새로운 무관한 변경이 발견되면 전체 파일을 임의로 덮어쓰지 않는다.

위 계획은 커밋 `3bdbf0ca6a66090038032e3068db6992c68d570c`에서 먼저 공개했고, 사용자가 **“인증서 연결 경로만 적용 승인”**으로 승인했다. 승인 원문·수신 기록·계획 digest를 보존한 뒤 **2026-09-15 10:37:40 UTC(19:37:40 KST)**에 적용했다. 실제 Caddyfile 해시·inode·소유권·권한과 기존 컨테이너 ID·image·PID·시작 시각을 대조했다. 원본은 Git 추적에서 제외한 로컬 작업 디렉터리의 `Caddyfile.approved-backup`으로 보존했다. 적용 후 검증이 통과해 rollback은 실행하지 않았다.

공개 GET·HEAD가 로그인·리디렉션 없이 `200 application/json`을 반환하고 실제 APK를 다시 `apksigner`로 검사한 인증서 및 앱 ID와 일치했다. [Google 공식 Digital Asset Links check API](https://developers.google.com/digital-asset-links/reference/rest/v1/assetlinks/check)에서도 실제 앱·인증서는 `linked=true`, 같은 앱 ID에 커플 앱 인증서를 넣거나 새 인증서에 다른 앱 ID를 넣으면 모두 `linked=false`였다. 이 결과는 서버의 공개 신뢰 연결 검사이며, 특정 휴대폰의 Android App Links 캐시나 Chrome TWA 실행 결과를 대신하지 않는다.

기존 커플 홈 307 응답과 assetlinks 200의 내용 해시는 적용 전후 일치했다. AI Company 홈은 200, 로그인 없는 관리 API는 401, 다른 Origin의 쓰기는 403을 유지했다. 이번에는 실제 비밀번호 로그인을 대행하거나 새 로그인 쿠키를 발급하지 않았다. Host·CSRF·HTTPS 쿠키 구현은 같은 운영 이미지와 기존 검증을 유지한다.

DNS·80/443 소유권·네트워크·API 포트·웹 이미지·worker·타이머·큐·승인 결정을 변경하지 않았다. B형 협업 UI 이미지 배포는 여전히 별도 경계다. 릴리스 `release.json`은 APK 게시 당시 적용 전 스냅샷이며, 현재 연결 결과는 `verification-summary.json`과 이 기록으로 확인한다.

## 항목별 검증과 설치 방법

| 항목 | 상태 | 근거 / 다음 확인 |
|---|---|---|
| 실제 APK 빌드·Lint | PASS | 위 Android run, 실물 산출물 |
| 앱 ID·버전·URL·서명·SHA-256 | PASS | APK 직접 검사·공개 재다운로드 |
| 앱→도메인 컴파일 선언 | PASS | 메타데이터 참조와 실제 JSON 대조 |
| 도메인→실제 APK 인증서 | PASS | 승인 후 공개 GET·HEAD 200·JSON, 실제 APK 인증서 일치 |
| Google Digital Asset Links | PASS | 실제 앱·인증서 true, 다른 앱 ID·인증서 false |
| 휴대폰 설치 | 미검증 | 실제 Android 기기에 설치 필요 |
| 완전 종료 후 실행 | 미검증 | Chrome 포함 실제 기기에서 확인 |
| 로그인·임시 비밀번호 변경 | 미검증 | 기존 `edward` 계정으로 직접 조작 |
| 뒤로가기 | 미검증 | 화면 이동·홈 복귀·앱 재진입 구분 |
| Light·Black 테마 | 미검증 | 기기에서 전환·종료 후 유지 확인; 현재 공개 웹 버전 기준 |
| 승인 화면 | 미검증 | 조회·스크롤·원문 확인만, PR #10은 pending 유지 |
| Android App Links 검증 | 미검증 | 적용 후 실제 `pm get-app-links` 결과 |
| Chrome TWA 신뢰·주소창 숨김 | 미검증 | App Links와 별개로 Chrome 실제 실행 확인 |

휴대폰에서 위 APK 링크를 열고, 필요하면 해당 다운로드 앱에 설치를 허용한 뒤 AI Company를 실행한다. `edward`와 기존 비밀번호로 로그인하며 임시 비밀번호 상태라면 화면에서 10자 이상으로 바꾼다. 비밀번호를 새로 발급하거나 기존 비밀번호를 읽지 않았다.

Android 12 이상 단말이 연결된 **사용자 컴퓨터**에서는 다음 읽기·재검증 명령을 사용할 수 있다. 서버에서 실행했다고 주장하지 않는다.

```bash
adb install ai-company-0.1.0.apk
adb shell pm verify-app-links --re-verify cloud.hyungwon.aicompany
adb shell pm get-app-links cloud.hyungwon.aicompany
```

APK 기준 커밋의 [Python 3.11·3.12 CI](https://github.com/HyungwonPark/ai-company/actions/runs/34956647437), [웹 UI CI](https://github.com/HyungwonPark/ai-company/actions/runs/34956647570), [공개 HTTPS 검사](https://github.com/HyungwonPark/ai-company/actions/runs/34956647528)는 통과했다. 각각 기존 회귀·웹 화면·연결 검증이며 Android 설치 결과를 대신하지 않는다. 컴파일 도메인 참조의 정상·잘못된 사이트·다른 리소스·다른 relation·미연결 메타데이터 회귀 5개도 추가했다.

후속 도구·회귀 커밋 `3bdbf0c`의 [Android 재빌드·Lint·신규 5개 검사](https://github.com/HyungwonPark/ai-company/actions/runs/34958535038), [Python 회귀 CI](https://github.com/HyungwonPark/ai-company/actions/runs/34958535113), [UI CI](https://github.com/HyungwonPark/ai-company/actions/runs/34958535000), [공개 연결 CI](https://github.com/HyungwonPark/ai-company/actions/runs/34958535155)도 모두 PASS다. 재빌드 산출물로 이미 게시한 APK를 덮어쓰지 않았다.

기존 AI Company·커플 앱·Caddy·DB 컨테이너는 healthy, backup은 running이었다. 운영 DB를 읽기 전용으로 대조한 PR #10 승인 `849f6de73d722f1ba2f4c5537111f6a0`은 `pending`이다. 이번 승인에 따른 공개 JSON 경로와 Caddy graceful reload만 적용했다. 서비스 교체·운영 큐 쓰기·타이머 전환·병합은 하지 않았다.
