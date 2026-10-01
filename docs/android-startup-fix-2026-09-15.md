# Android 0.1.1 시작 종료 수정·실행 검증

2026-09-15. 사용자가 보고한 **아이콘을 누르면 즉시 종료되는 문제**를 공개한 0.1.0 서명 APK로 재현했다. 필수 Android 구성요소 선언을 추가한 0.1.1을 같은 앱 ID·서명키로 게시했다. 기존 앱을 삭제하지 않고 업데이트 설치할 수 있다.

[0.1.1 APK 다운로드](https://github.com/HyungwonPark/ai-company/releases/download/android-v0.1.1/ai-company-0.1.1.apk) · [릴리스·SHA-256·공개 증거](https://github.com/HyungwonPark/ai-company/releases/tag/android-v0.1.1) · [구조화된 현재 검증 결과](evidence/android-startup-fix-2026-09-15.json)

## 재현된 원인과 수정

[0.1.0 실제 재현 실행](https://github.com/HyungwonPark/ai-company/actions/runs/34966397966)은 Android 16(API 36), Chrome `133.0.6943.137`에서 실제 배포 APK를 설치했다. 설치는 성공했지만 시작 시 다음 예외로 앱 프로세스가 종료됐다.

```text
java.lang.IllegalArgumentException: Component class
com.google.androidbrowserhelper.trusted.ManageDataLauncherActivity
does not exist in cloud.hyungwon.aicompany
```

호출 경로는 `LauncherActivity.onCreate` → `LauncherActivity.launchTwa` → `ManageDataLauncherActivity.addSiteSettingsShortcut` → `PackageManager.setComponentEnabledSetting`이다. `am start` 자체는 `Status: ok`를 반환했으므로 그 결과만으로 앱 실행을 통과시키면 이 문제를 놓친다. crash log와 실제 foreground Activity를 함께 검사했다.

사용한 [Android Browser Helper 2.7.3의 실제 소스](https://github.com/GoogleChrome/android-browser-helper/blob/android-browser-helper-2.7.3/androidbrowserhelper/src/main/java/com/google/androidbrowserhelper/trusted/ManageDataLauncherActivity.java)는 시작 과정에서 관리 Activity의 활성화 상태를 설정한다. 우리 Manifest에 해당 Activity가 없었다. Chrome 설치 여부로 해결되는 문제가 아니다.

수정 커밋 `ad146ceca5ab9503cb7698acb79a278a94da4092`에서 `ManageDataLauncherActivity`를 `exported=false`로 선언하고, application의 `manageSpaceActivity`와 승인된 도메인의 `MANAGE_SPACE_URL`을 연결했다. versionName을 `0.1.1`, versionCode를 `2`로 올렸다. 실제 컴파일된 Manifest에 누락·외부 공개·잘못된 관리 URL이 있으면 검사 도구와 서명 절차가 거부한다. 기존 도메인 검사 5개와 신규 구성요소 검사 4개, 총 **9개 회귀 검사**가 통과했다. 새 검사에 실제 0.1.0의 컴파일 Manifest를 입력하면 누락된 구성요소로 거부된다.

## 실제 전달 파일과 출처

| 항목 | 값 |
|---|---|
| APK | `ai-company-0.1.1.apk`, 3,732,083 bytes |
| 버전 | `0.1.1`, versionCode `2` |
| 앱 ID | `cloud.hyungwon.aicompany` |
| 시작 주소 | `https://hyungwon.cloud/` |
| Android | minSdk 26, targetSdk 36, native ABI 제약 없음 |
| APK 소스 커밋 | `ad146ceca5ab9503cb7698acb79a278a94da4092` |
| APK SHA-256 | `a48bb5642ab676203f56051fe8647c802a7dcbec4f6d78eaa8c351902088992c` |
| 인증서 SHA-256 | `E0:22:C4:1F:03:95:9B:51:54:C0:85:8C:22:F7:25:EF:49:D0:B1:F6:2F:DB:39:92:64:0F:30:86:E6:2E:98:79` |

[APK 빌드 CI](https://github.com/HyungwonPark/ai-company/actions/runs/34967204782)의 workflow ID `358622511`, 경로 `.github/workflows/android.yml`, run head, artifact ID `10395557160`와 ZIP digest를 실제 다운로드 바이트에 대조했다. assembleRelease·lintRelease·회귀·컴파일 APK 검사가 통과했다. 서명 전 APK SHA-256은 `fe963990330ed084f6a8fdc5a9fc7a2f172d643a84cb6fb703d7ad4db23de4ae`다.

기존 전용 키로 로컬 서명한 실제 APK에서 v2·v3 서명과 인증서, 검사한 APK 내용의 불변을 확인했다. 인증 없이 GitHub에서 다시 내려받은 APK도 위 해시와 일치한다. 공개 HTTPS assetlinks의 앱 ID·인증서가 실제 새 APK와 일치한다. 키·암호는 저장소·CI·릴리스에 업로드하지 않았다. 기존 릴리스 파일과 태그는 덮어쓰지 않는다. 릴리스 `release.json`은 서명 당시 스냅샷이며 후속 도메인·실행 결과는 `verification-summary.json`에 기록한다.

## 실행 결과와 확인하지 못한 범위

[0.1.1 실제 서명 APK 실행 CI](https://github.com/HyungwonPark/ai-company/actions/runs/34967897825)는 workflow ID `358678729`, 경로 `.github/workflows/android-runtime.yml`, 검사 코드 커밋 `fd84643ade0790833a1d62d2b4ddaf566fca0885`에서 실행됐다. APK 소스 커밋과 검사 코드 커밋을 구분한다. 입력한 기대 APK SHA-256, 실제 설치한 APK 해시, artifact ID `10395659081`·run head·ZIP digest를 대조했다.

| 시나리오 | 결과와 한계 |
|---|---|
| 0.1.0 설치 후 0.1.1 업데이트 | PASS. 동일 앱 ID·서명키로 `adb install -r`; 삭제 없음 |
| 아이콘에 해당하는 MAIN/LAUNCHER 시작 | PASS. 앱 crash 없음, Chrome foreground 전환 |
| 홈 이동·앱 강제 종료 후 재실행 | 일반/야간 모드 각각 PASS. 총 세 차례 시작 |
| Chrome 상태 | 에뮬레이터의 `133.0.6943.137`, 최초 실행 `FirstRunActivity` 관측 |
| 캡처 | Chrome 최초 실행 로딩 화면. AI Company 웹 로그인 화면 캡처가 아님 |
| 도메인→실제 새 APK 인증서 | PASS. 공개 HTTPS JSON과 실제 서명 인증서 일치 |
| 사용자 휴대폰 0.1.0 | 사용자 보고: 설치 후 아이콘을 누르면 즉시 종료 |
| 사용자 휴대폰 0.1.1 | 재검증 대기. 정확한 기종·Android 숫자 버전은 미확인 |
| 최신 Chrome·Android 8 최소 버전 | 별도 실행 미검증. 이번 에뮬레이터 결과로 일반화하지 않음 |
| 로그인·뒤로가기·Light/Black 웹 테마·승인 화면 | 새 APK에서 미검증. Android 야간 모드 시작과 웹 테마 검증을 구분 |
| Google/서비스 로그인·승인 제출 | 수행하지 않음. PR #10 후보 승인 유지 |

실행 workflow의 PR 자동 작업은 서명 파일이 없는 단계에서 `skipped`된다. 이 결과를 PASS로 세지 않는다. 위 성공은 실제 게시 APK와 기대 해시를 지정한 별도 `workflow_dispatch`다. 초기 CI 환경 구성 중 KVM 실행 그룹·AVD 위치·ADB 테스트 키 접근 오류가 있었고, 앱 설치 이전 실패로 구분했다. 일회성 hosted runner에서 기존 KVM 그룹으로 실행 권한을 내리고, 작업 전용 AVD·ADB 키 경로를 맞춘 뒤 실제 앱을 재현했다. 운영 서버의 전역 권한·샌드박스·userns 설정은 변경하지 않았다.

검사 코드 커밋 `fd84643`의 [Python 3.11·3.12 회귀](https://github.com/HyungwonPark/ai-company/actions/runs/34967568508), [UI 회귀](https://github.com/HyungwonPark/ai-company/actions/runs/34967568433), [APK·Lint](https://github.com/HyungwonPark/ai-company/actions/runs/34967568465), [공개 HTTPS](https://github.com/HyungwonPark/ai-company/actions/runs/34967568496)도 통과했다.

## 설치·운영 상태

휴대폰에서 위 APK를 다운로드하고 기존 앱 위에 업데이트한 뒤 AI Company 아이콘으로 실행한다. 로그인 화면이 열리면 `edward`와 기존 비밀번호를 사용한다. 임시 비밀번호 상태이면 10자 이상으로 변경한다. 로그인·뒤로가기·테마·승인 화면 조회는 각각 실기기 결과로 기록하며 후보 승인은 제출하지 않는다.

기존 공개 도메인과 서명 인증서를 유지해 이번 APK 수정에는 Caddy 재적용이나 웹 배포가 필요하지 않았다. Caddy 설정 해시는 승인 후 상태와 동일하고, 두 서비스 HTTPS 응답과 기존 사용량 타이머를 읽기 전용으로 확인했다. PR #10 승인 원장의 상태도 `pending`이다. 큐·계획·승인 결정·운영 이미지·타이머를 변경하지 않았고, 배포·병합은 수행하지 않았다.
