# hyungwon.cloud 도메인·TWA 운영 준비

2026-09-14. 후속 D 작업의 읽기 점검과 미배포 설계다. DNS, TLS, 로그인, Android 설치,
서명, domain verification을 완료했다고 주장하지 않는다. 신규 app/key/release 저장소를
기존 커플 서비스와 분리한다.

## 실제 운영 서비스 보존 기준

읽기 전용 점검 결과 운영 checkout은 `/home/edward/talenta-site`, 깨끗한
`agent/initial-mobile-design` 브랜치다. 설정은 `docker-compose.yml`과 `Caddyfile`이다.

| 항목 | 현재 확인 |
|---|---|
| Compose 이름 / network | `talenta-edward` / `talenta-edward_private` bridge |
| app | `talenta-edward-app-1`, host `127.0.0.1:3000` → container 3000 |
| edge | `talenta-edward-caddy-1`, `caddy:2.10.2-alpine`, public TCP80/443 및 UDP443 점유 |
| DB / backup | `talenta-edward-db-1`, `talenta-edward-backup-1`, MariaDB11.4.12, host 포트 공개 없음 |
| edge 설정·상태 | Caddyfile→`/etc/caddy/Caddyfile`, `/data/caddy/{data,config,logs}` 마운트 |
| 기존 데이터 | `/data/{photos,backups,app-releases,secrets,mariadb}`; 신규 앱에 마운트하지 않음 |
| domain | `www.talenta-edward.life`→apex redirect, apex→`app:3000` |
| 공개 예외 | Caddy가 `/.well-known/*`를 bot/session gate보다 먼저 app으로 전달. 신규 서비스에서는 필요한 assetlinks **정확한 경로**만 공개 |
| 후보 API 포트 | host8088 현재 listen 없음. 예약/배포한 것은 아님 |
| 자원 | 2 logical CPU, MemTotal 11.65GiB, MemAvailable 약8.4GiB, `/data` 가용 약115GiB |

컨테이너의 현재 explicit CPU/memory 제한은 모두 0(미설정)이다. 한 시점의 사용량은
app129.2MiB, Caddy36.52MiB, DB53.05MiB, backup11.08MiB였으며 용량 보장은 아니다.
초기 모델 실행 동시성은 2 이하로 두고 CPU가 큰 검사/Android build는 1개만 허용하는
새 실행 정책을 제안한다. 역할 수를 2개로 제한한다는 뜻은 아니다. 운영 적용 전 장시간
측정으로 조정하고 기존 컨테이너 제한을 이 작업에서 바꾸지 않는다.

서버 resolver 조회 당시 `talenta-edward.life=161.118.250.112`,
`hyungwon.cloud=121.254.178.253`으로 달랐다. 이 결과만으로 DNS 소유권, authoritative
record, 대상 서버 주소를 확정하지 않는다. 신규 apex는 현재 다른 대상을 가리키므로
기존 사용 여부·TTL·A/AAAA/CAA와 소유자 승인 확인 전 덮어쓰지 않는다.

보존할 파일의 이번 읽기 점검 SHA-256:

| 경로 | digest |
|---|---|
| `/home/edward/talenta-site/Caddyfile` | `2393a6d6215ce43342041192f7d4598624f26164b8866e01bdd3485b4ffce474` |
| `/home/edward/talenta-site/docker-compose.yml` | `98f48067af354f3974e01aa554188d4ec361ca14c9e82f148cdcfa7c44e1c9fd` |
| `~/.config/systemd/user/ai-company-quota.service` | `8d456356a4cb0fbbb952caf691b50f16a1c969ec4ce0423a18773b8137678487` |
| `~/.config/systemd/user/ai-company-quota.timer` | `32dc1d11e96dfd8800f83f342dc6750182b42e22d5d4af027481374aad57ca34` |

최종 점검에서 네 파일 digest가 모두 일치했고 app/caddy/db는 running/healthy, backup은
running이었다. 기존 quota timer는 active이며 다음 실행을 예약하고 있었다. 이 점검에서 queue를 열거나
timer를 재시작하지 않았다. 큐 보존 사실을 내용 hash로 다시 검증할 경우 현재 writer와
SQLite WAL을 고려한 읽기 snapshot으로 비교하며 파일 hash만으로 정상성을 추정하지 않는다.

## 지정된 실제 TWA 저장소에서 확인한 방식

`HyungwonPark/edward-talenta`의 기본 `main`에는 README만 있다. 실제 코드는
`agent/initial-mobile-design`의 **`30dfeab4e38cab7d98c802329312dce0381110d6`**다.
이를 `/tmp/ai-company-twa-reference`에 따로 복제하여 읽었다. 운영 checkout은 변경하지 않았다.

| 실제 파일 | 확인한 구성 / 재사용 범위 |
|---|---|
| [android/app/build.gradle.kts](https://github.com/HyungwonPark/edward-talenta/blob/30dfeab4e38cab7d98c802329312dce0381110d6/android/app/build.gradle.kts) | `life.talentaedward.app`, compile/target35/min26, Java17, androidbrowserhelper2.5.0 + browser1.8.0. siteOrigin 하나로 BuildConfig·manifest host·launch_url·asset_statements 생성 방식 참고 |
| [android/app/src/main/AndroidManifest.xml](https://github.com/HyungwonPark/edward-talenta/blob/30dfeab4e38cab7d98c802329312dce0381110d6/android/app/src/main/AndroidManifest.xml) | TWA entry 및 domain 선언. 커플 전용 위치·위젯·푸시·업데이트 권한은 신규 앱에 복사하지 않음 |
| [android/build.sh](https://github.com/HyungwonPark/edward-talenta/blob/30dfeab4e38cab7d98c802329312dce0381110d6/android/build.sh) | ARM 서버에서 amd64 toolchain/Gradle 실행, signing env+read-only key mount. **서명 build가 `/data/app-releases/latest.json`까지 갱신하므로 실행/복사 금지** |
| [ANDROID.md](https://github.com/HyungwonPark/edward-talenta/blob/30dfeab4e38cab7d98c802329312dce0381110d6/ANDROID.md) | sideload/서명 보존·빌드 운영 기록. 기존 secret 위치만 확인하고 key/env 파일을 읽지 않음 |
| [app/manifest.ts](https://github.com/HyungwonPark/edward-talenta/blob/30dfeab4e38cab7d98c802329312dce0381110d6/app/manifest.ts), [proxy.ts](https://github.com/HyungwonPark/edward-talenta/blob/30dfeab4e38cab7d98c802329312dce0381110d6/proxy.ts) | manifest, sw, assetlinks를 login 전 제공하는 방식 참고. 기존 app의 다른 device/token 예외는 복사하지 않음 |
| [assetlinks route](https://github.com/HyungwonPark/edward-talenta/blob/30dfeab4e38cab7d98c802329312dce0381110d6/app/.well-known/assetlinks.json/route.ts) | env의 package+fingerprint, 없으면 빈 배열. 신규 앱은 포맷도 검사하며 미검증 표시 유지 |
| [public/sw.js](https://github.com/HyungwonPark/edward-talenta/blob/30dfeab4e38cab7d98c802329312dce0381110d6/public/sw.js) | 개인 데이터 캐시 없이 network-only. 새 화면에서 정적 shell만 캐시하더라도 API·보고·승인 결과 캐시는 금지 |

참고 저장소의 Android dependency/version과 빌드 특이사항은 **그 커밋의 관찰 사실**이다.
신규 프로젝트에 그대로 적합하거나 최신이라고 단정하지 않는다. 참고 스크립트의
`--privileged` binfmt 등록과 즉시 release publish는 본 작업 권한에 포함되지 않는다.

## 신규 연결 설계와 배포 경계

1. **주소/네트워크**: `hyungwon.cloud`를 신규 origin으로 사용한다. 기존 Caddy가 80/443을
   점유하므로 별도 proxy가 같은 host 포트를 bind하지 않는다. 승인된 유지보수에서만
   기존 Caddy에 별도 vhost를 추가하는 안을 우선 검토한다. 새 API/UI 서비스는 독립
   release/state 경로와 전용 `ai-company-edge` bridge로 둔다. Caddy만 이 새 network에도
   연결하고 신규 앱은 `talenta-edward_private`에 연결하지 않는다. app의 Docker socket,
   커플 DB/secret volume 공유는 금지한다. container 내부 upstream 예시는
   `ai-company-web:8088`이며 현재 배포된 endpoint가 아니다.
2. **실행 분리**: 공개 웹 프로세스는 모델 CLI나 shell을 요청 스레드에서 실행하지 않고
   기존 영속 dispatcher에 승인된 작업만 제출한다. worker는 별도 보호된 실행 경로에서
   계속하며 API 컨테이너에 개인 CLI 인증 디렉터리를 통째로 mount하지 않는다.
   SQLite를 공유한다면 새 DB 디렉터리의 UID/WAL/locking과 narrow permission을 검증한다.
   현재 개발용 HTTP 서버를 그대로 공개하는 것은 출시 조건을 충족하지 않는다.
3. **로그인**: 신규 서비스 전용 master identity와 session secret을 쓴다. 초기 개발 token은
   인터넷 공개 인증 완료 근거가 아니다. 실제 로그인에는 server-side session, Secure/
   HttpOnly cookie, logout/revocation, rate limit 및 동일 origin 쓰기 검사를 검증한다.
   TWA 외부 intent 재실행을 고려해 SameSite=Lax를 검토하고 CSRF는 별도 token/Origin으로
   방어한다. 기존 계정·비밀번호·인증 키를 가져오지 않는다.
4. **승인**: POST만 상태를 바꾸고 재전송은 같은 idempotency key로 한 번 처리한다.
   세션 만료/권한 없음/대상 commit 변경/기한 경과/비용 증가가 승인 실행을 막아야 한다.
   공개 프록시의 인증을 통과했어도 API가 project/master 권한을 다시 검사한다.
5. **TLS/DNS**: 운영자가 현재 A/AAAA/CAA/TTL과 신규 대상 IP를 확정하고 기존 레코드를
   백업한다. 앱과 auth를 비공개에서 검증한 후 승인된 DNS 변경과 Caddy vhost를 적용한다.
   HTTPS redirect, certificate host/chain, renewability를 실제 검사한다. 신규 origin의
   certificate/key/state를 커플 application signing key와 혼동하지 않는다.
6. **public routes**: login shell, `/manifest.webmanifest`, `/icon.svg` 및 실제 제공 icon PNG,
   `/sw.js`, 정확히 `/.well-known/assetlinks.json`만 공개 대상으로 검토한다.
   보고/이벤트/작업/승인 API와 session 정보는 공개하지 않는다. assetlinks는 login redirect나
   bot block 없이 HTTPS JSON으로 제공하며, 설정 전에는 `[]`로 어떤 앱도 허가하지 않는다.
7. **자원/비용**: 기존 host를 쓰는 안은 추가 VM 구매 없음이나 CPU/메모리·대역폭·TLS/DNS
   provider 비용 정책은 실제 계정에서 확인한다. 새 서비스 memory/CPU/pids 상한과 log
   rotation을 배포 manifest에 명시한 후 승인을 올린다. 현재 자료만으로 월 비용을 확정하지 않는다.

본 문서는 네트워크 구조를 제안한다. Dockerfile/운영 로그인/SQLite 공유 방식이 확정된
release artifact가 아직 없으므로 **실행 가능한 production compose/vhost를 설치하지 않는다**.
그 산출물이 준비되면 exact image digest, mounts, UID, limits, vhost diff를 master approval에
연결해야 한다. 호스트 격리 조치는 [별도 runtime 변경안](runtime-remediation-plan.md)에 있다.

## Android 별도 소유권과 fail-closed 준비 도구

신규 application ID 제안은 `cloud.hyungwon.aicompany`다. 소유자가 확정하기 전에는
등록/서명/배포하지 않는다. 기존 `life.talentaedward.app`이나 인증서 지문을 사용하지 않는다.
신규 key는 신규 소유 경로/비밀 저장소에서 만들고 저장소 밖 백업 및 복구 담당자를 정한다.
Play 배포와 sideload 중 실제 채널을 정한 뒤 **설치 APK의 signing certificate**를 기준으로
Digital Asset Links를 생성한다. Play App Signing의 배포 key는 upload key와 다를 수 있다.
[Android 공식 assetlinks 안내](https://developer.android.com/training/app-links/configure-assetlinks)

`deploy/examples/render_assetlinks.py`는 package 및 실제 SHA-256 fingerprint를 모두 요구한다.
빈 값·잘못된 fingerprint·기존 커플 앱 ID는 오류 종료하고 출력하지 않는다. 파일 출력은
기존 파일을 덮어쓰지 않는다. 이것은 공개 JSON 생성기이며 key 소유권 검증기가 아니다.
유효한 형식으로 생성됐더라도 APK 서명/도메인 대조 전에는 미검증이다.
`python3 -m unittest discover -s deploy/examples -p 'test_*.py' -v`의 준비 도구 테스트
4개가 통과했다. fixture 지문은 합성 값이며 실제 앱 연결 검증을 뜻하지 않는다.

```bash
# Values must come from the NEW approved application and the actual signed APK.
python3 deploy/examples/render_assetlinks.py \
  --package "${AI_COMPANY_ANDROID_PACKAGE}" \
  --cert-sha256 "${AI_COMPANY_APK_SIGNER_SHA256}" \
  --output "${AI_COMPANY_REVIEW_DIR}/assetlinks.json"
```

새 Android 빌드는 local artifact 생성 단계와 master 승인 후 release 게시 단계를 분리한다.
debug APK, 서명 정보 없음, 예상 signer 불일치, package/origin 불일치는 release 단계에서
실패해야 한다. `apksigner verify --print-certs`로 APK를 확인하고 package/version/hash를
승인 대상에 고정한다. API commit과 Android artifact가 달라지면 재검토한다.

## 모바일 웹·TWA 완료 검사 (전부 실제 연결 후 확인)

- 모바일 브라우저에서 login, 역할별 병렬 진행, report와 approval 별도 화면, PM quota 대기,
  네트워크 끊김/재접속과 새로고침을 검증한다. offline 승인 버튼은 성공을 표시하지 않는다.
- 설치 manifest의 name/start_url/scope/icons를 실제 origin과 대조한다. 192/512 PNG 및
  maskable 사용 시 safe zone을 검사한다. vector 아이콘만 있으면 launcher icon 검증은 미완료다.
- `/.well-known/assetlinks.json` HTTPS200/JSON/no redirect, exact app ID와 실제 signer를
  확인한다. unset config에서 `[]`, 미승인 cert에서 미검증으로 남는 음성 검사도 수행한다.
- 별도 테스트 단말에서 신규 APK 설치 및 update, cold start, Chrome login→TWA login 유지,
  logout 뒤 재진입, 외부 링크, back navigation, approval 재전송/만료를 확인한다.
- Android12+ 테스트 단말에서는 `adb shell pm verify-app-links --re-verify <new-package>`와
  `adb shell pm get-app-links <new-package>`의 실제 결과를 기록한다. Chrome TWA의 domain
  validation 결과와 주소창 없는 launch도 별도 확인한다. App Links 성공만으로 TWA 전체
  인증/사용성을 통과시키지 않는다. [공식 Android 검증 절차](https://developer.android.com/training/app-links/verify-applinks),
  [Chrome의 TWA signing 구분](https://developer.chrome.com/docs/android/trusted-web-activity/android-for-web-devs)
- 실제 새 candidate commit에 개발·검사·독립 검수·Astra Ultra 최종 검수와 승인 provenance가
  연결돼야 한다. 모의 DEMO_READY, HTTP200, APK build 성공은 대체 증거가 아니다.

## 승인 요청에 남은 구체적 입력 / 롤백

지금 요청할 수 있는 별도 host 조치는 runtime 문서의 pinned bwrap/profile 설치안이다.
배포 승인은 다음 항목까지 구체화된 뒤 올린다:

- hyungwon.cloud DNS owner/provider, 기존 사용 여부와 record snapshot, 실제 target IP, TTL,
  Caddy vhost/network diff, 신규 release/image digest, login master identity, session/secret 보관자.
- 새 application ID, 새 key 보관/backup 책임자, 배포 채널 및 actual APK signer fingerprint,
  테스트 Android 기기, 신규 release 저장 경로와 비용 상한.
- 변경 commit/artifact/environment, 승인 기한, rollback 실행자, 기존 서비스 health 기준.

배포 rollback은 신규 endpoint를 비활성화하고 승인 전 Caddy 설정을 복원·검증한 뒤 graceful
reload, 새 network 연결만 해제하고 새 service만 중지한다. 기존 app/db/caddy volume은
삭제하지 않는다. DNS는 기록한 원래 값으로 돌리되 TTL 전파 시간 동안 신규 endpoint에서
안전한 maintenance 응답을 제공한다. 신규 SQLite는 일관된 backup으로 보관하고 기존 큐에
병합하지 않는다. APK는 기존 signer로 새 versionCode를 올린 복구 버전을 내거나 배포
목록에서 회수한다. 기존 서명된 앱을 낮은 versionCode로 강제 downgrade하지 않는다.

이번 작업에서는 외부 listener, DNS, TLS, Docker service, 기존 timer/queue, key, Android
release를 변경하지 않았다. API/UI 구현은 이 운영 승인을 기다리지 않고 독립적으로 검증할 수 있다.
