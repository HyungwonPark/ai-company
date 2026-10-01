# AI Company Android

`hyungwon.cloud`를 여는 별도 TWA 앱이다. 기존 커플 앱의 앱 ID·키·위치·푸시·위젯·업데이트 스크립트를 가져오지 않는다.

- [설치 APK 0.1.1](https://github.com/HyungwonPark/ai-company/releases/download/android-v0.1.1/ai-company-0.1.1.apk)
- [릴리스와 SHA-256·서명 기록](https://github.com/HyungwonPark/ai-company/releases/tag/android-v0.1.1)
- [시작 종료 수정과 실제 Android 실행 검증](../docs/android-startup-fix-2026-09-15.md)
- [최초 APK·도메인 연결 기록](../docs/android-apk-validation-2026-09-15.md)

0.1.0은 필수 Activity 선언 누락으로 시작 시 종료된다. **기존 앱을 삭제하지 않고 0.1.1로 업데이트한다.** 같은 앱 ID·서명키의 업데이트 설치와 세 차례 시작 경로를 Android 16(API 36) 에뮬레이터에서 확인했다. Chrome 최초 실행 화면까지 확인했으며 실제 휴대폰의 로그인·뒤로가기·웹 테마·승인 화면은 별도 미검증이다.

앱 ID는 `cloud.hyungwon.aicompany`, 시작 주소는 `https://hyungwon.cloud/`, 최소 Android 버전은 8.0(API 26)이다. Chrome의 표준 `LauncherActivity`를 사용한다. 로그인과 테마·승인 화면은 공개 웹의 현재 버전을 표시하므로 앱 설치와 새 웹 버전 배포를 구분한다.

사용자 승인 후 실제 APK 인증서의 공개 assetlinks 경로를 연결했다. HTTPS GET·HEAD 200과 Google Digital Asset Links의 `linked=true`를 확인했다. 실제 기기의 Chrome TWA 실행·로그인은 별도 미검증이다.

## 빌드

JDK 17, Android SDK platform 36 / build-tools 36.0.0이 필요하다. Gradle 8.13 배포본과 wrapper SHA-256을 고정했다. AGP 8.13.2, Android Browser Helper 2.7.3, AndroidX Browser 1.9.0을 사용한다. 최신 버전 사용을 주장하지 않으며 Lint의 업데이트 권고는 검증 기록에 남긴다.

```bash
cd android
./gradlew --no-daemon --console=plain :app:assembleRelease :app:lintRelease
```

출력 `app/build/outputs/apk/release/app-release-unsigned.apk`는 설치 배포용이 아니다. `.github/workflows/android.yml`은 실제 PR head를 checkout하고 실제 컴파일 Manifest·리소스·APK를 검사한 뒤, unsigned APK와 실행 ID·기준 커밋·내용 해시를 artifact로 보존한다. CI에 서명키를 전달하지 않는다.

`verify_unsigned.py`는 패키지·버전·URL·권한·디버그 설정을 검사한다. `asset_statements` 문자열에 도메인이 등장하는지만 보지 않고, application 메타데이터가 실제 컴파일 리소스를 참조하며 그 JSON의 relation·namespace·site가 일치하는지 검사한다. Android Browser Helper가 시작할 때 사용하는 `ManageDataLauncherActivity`의 실제 선언·비공개 설정·관리 URL도 검사한다.

```bash
python3 -m unittest discover -s android/tests -v
python3 android/verify_unsigned.py --help
```

`.github/workflows/android-runtime.yml`은 게시한 **서명 APK**를 지정한 `workflow_dispatch`에서 실행한다. 태그와 로컬에서 검사한 기대 SHA-256을 모두 입력해야 한다. 배포 파일과 checksum만 함께 신뢰하지 않고 기대 해시를 별도로 대조한다. Android 에뮬레이터에서 기존 0.1.0 설치 → 삭제 없는 업데이트 → 최초 실행·일반/야간 모드 재실행을 검사한다. crash log와 Chrome foreground Activity를 함께 확인한다. PR의 이 작업이 `skipped`인 결과는 실행 통과로 세지 않는다. 에뮬레이터의 Google/서비스 로그인과 승인 제출은 수행하지 않는다.

## 로컬 서명과 업데이트

`create_signing_key.py`는 **최초 새 소유권 생성용**이다. 기존 키가 있는 디렉터리와 Git 저장소 안의 생성을 거부한다. 기존 0.1.0용 키를 재생성하지 않는다. 전용 PKCS12/RSA 3072 키와 비밀번호는 저장소 밖에 두고 디렉터리 0700, 비밀 파일 0600으로 보관한다. 비밀번호는 명령 인자·환경 변수·출력에 넣지 않고 파일 입력으로 전달한다.

`sign_release.py`에는 검증한 unsigned APK, `build-evidence.json`, 기대하는 기준 커밋·인증서, 전용 키·비밀번호 파일, Java와 `apksigner.jar`, 새 출력 디렉터리를 명시한다. 먼저 원격 workflow/run/artifact의 연결과 artifact ZIP digest를 대조한다. 도구는 unsigned APK 및 내부 파일 해시를 다시 대조하고 v2·v3로 서명한다. 실제 APK 인증서·서명과 내용 불변을 확인한 뒤 `release.json`, `SHA256SUMS`, 검사 로그를 만든다. 같은 출력 APK를 덮어쓰지 않는다.

```bash
python3 android/create_signing_key.py --help
python3 android/sign_release.py --help
```

PKCS12의 키·저장소 비밀번호가 같은 현재 구성에서는 `apksigner`의 `--key-pass`를 생략해 저장소 비밀번호를 재사용한다. 동일한 한 줄 비밀번호 파일을 두 옵션에 지정하면 두 번째 읽기에서 EOF가 발생한다. 비밀번호를 평문으로 출력하는 방식으로 해결하지 않는다.

후속 앱 변경에는 versionCode를 올리고 앱 ID와 기존 전용 서명키를 유지한다. 현 검증 스크립트의 기대 버전도 함께 수정·검토한다. 이미 게시한 APK와 태그를 덮어쓰지 않는다. 이번 APK는 GitHub 직접 설치용이며 Play App Signing은 설정하지 않았다. 향후 Play 배포 시에는 upload key가 아닌 실제 배포 APK의 인증서를 다시 대조한다.

## 휴대폰 확인

APK 다운로드 → 필요하면 해당 다운로드 앱의 설치 허용 → 설치 → AI Company 실행 순서다. `edward`와 기존 비밀번호로 로그인한다. 임시 비밀번호 상태라면 화면에서 10자 이상으로 변경한다. 새 비밀번호 발급·로그인 대행은 이번 작업에 포함하지 않았다.

실기기에서 설치, 완전 종료 후 실행, 로그인, 뒤로가기, Light·Black 전환, 승인 화면 조회를 각각 기록한다. 승인 조회 검증 중 PR #10 후보를 수용하지 않는다. 공개 `assetlinks.json` 적용 전에는 도메인 신뢰가 성립하지 않아 주소 표시줄이 있는 Custom Tab으로 열릴 수 있다. 도메인 연결을 우회하는 Chrome 플래그는 사용하지 않는다.
