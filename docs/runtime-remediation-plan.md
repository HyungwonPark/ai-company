# 실행 격리 복구안과 설정 증거

2026-09-14. PR #5 `45ea969` 위 후속 구현의 A 작업. 이 문서는 **미적용 호스트 변경안**이다.
패키지 설치, AppArmor 로드, 운영 timer/queue 변경, 실제 개발 완주를 뜻하지 않는다.

## 현재 다시 확인한 사실

- worker PATH는 `/home/edward/.local/bin:/home/edward/.nvm/versions/node/v22.13.0/bin:/usr/bin:/bin`.
  선택된 Codex는 `/home/edward/.local/bin/codex`, `codex-cli 0.154.0`이다.
- `/usr/bin/bwrap`은 없고 `/etc/apparmor.d`에서 bwrap 정의를 찾지 못했다.
  `apparmor`는 `4.0.1really4.0.1-0ubuntu0.24.04.7`이다.
  `kernel.unprivileged_userns_clone=1`, `kernel.apparmor_restrict_unprivileged_userns=1`을 읽었다.
- 18:36:11 KST에 실제 adapter의 transient 옵션과 worker PATH로 최소 재현했다.
  `Type=exec`, `KillMode=control-group`, `TimeoutStopSec=5`, `UMask=0077`,
  이 worktree를 WorkingDirectory로 사용하고 기존 서비스에 작업을 제출하지 않았다.

```text
unit: ai-company-run-unified-probe-fd7df5d730fc4edb8327d6718a771d24.service
executable: /home/edward/.codex/packages/standalone/releases/0.154.0-aarch64-unknown-linux-musl/codex-resources/bwrap
arguments: --unshare-user --unshare-pid --unshare-net --ro-bind / / --proc /proc --dev /dev /usr/bin/true
exit: 1
stderr: bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted
after: LoadState=not-found ActiveState=inactive SubState=dead MainPID=0 ControlGroup=
```

같은 시각 kernel audit의 `comm="bwrap"`, `profile="unprivileged_userns"`에서
`net_admin`과 `setpcap`이 거부됐다. transient unit은 수거됐으며 작업 파일을 쓰지 않았다.
최소 재현과 직전 [실제 Astra BLOCK 기록](pr5-followup-validation.md)은 별개 증거다.
이번 단계에서 유료 모델 호출을 반복하지 않았다.

현재 CLI 도움말은 `codex sandbox [OPTIONS] [COMMAND]...` 형식이다.
`linux`는 하위 명령이 아니다. 예전 문서의 `codex sandbox linux -- ...`를 재사용하지 않고
설치된 버전의 `codex sandbox --help`와 실제 worker가 넘기는 permission profile/state로 검증한다.
일반 호스트 명령의 승인 실행 성공을 agent sandbox 성공으로 세지 않는다.

## 검토 대상 패키지와 영향

이미 다운로드·해제한 파일은 아래 디렉터리에 있다. 키·인증 파일을 포함하지 않는다.

```text
/home/edward/ai-company/workspaces/agent-failover-loop/.ai-company/pr5-followup-2c04c489/
```

| 파일 | SHA-256 |
|---|---|
| bubblewrap_0.9.0-1ubuntu0.1_arm64.deb | `3fb4ca3a8d2060444836568ed49d6897a403467e4ba29c93440900093fb96a38` |
| apparmor-profiles_4.0.1really4.0.1-0ubuntu0.24.04.7_all.deb | `bdac5b74d884643653565c52ed7483c9582e646ff72cce8d95d0eb8467a3139c` |
| package-inspection/usr/share/apparmor/extra-profiles/bwrap-userns-restrict | `11d39094f044f0cda0febb3ad517b830301da6b2ce929664af09ee9e4dd264f9` |

`apt-get --simulate install bubblewrap=0.9.0-1ubuntu0.1`의 현재 결과는 **신규 1개,
업그레이드 0개, 삭제 0개**다. 패키지는 49,694 bytes, Installed-Size 126 KiB이며
현재 설치된 libc6/libcap2/libselinux1을 사용한다. 실제 적용 직전 다시 simulation한다.

패키지 `postinst`는 `sysctl --quiet --pattern '^kernel\.unprivileged_userns_clone$' --system`을
실행한다. 패키지가 전역 값을 전혀 건드리지 않는다고 말할 수 없다. 현재 검색한 sysctl
설정에는 이 clone 값의 별도 override가 없지만 적용 전 전체 우선순위 설정을 다시 확인한다.
두 전역 값은 적용 전후 모두 1이어야 한다. AppArmor restriction 키는 이 pattern에 포함되지 않는다.

변경안은 **bubblewrap만 설치하고, 검토된 단일 profile을 root 소유로 설치·로드**하는 것이다.
전체 `apparmor-profiles` 패키지 설치는 제안하지 않는다. 이미 해제한 배포판 profile만 사용하여
무관한 profile 설치와 기존 daemon 영향 범위를 줄인다. package digest/출처가 달라지면 재검토한다.

프로필은 `/usr/bin/bwrap`에 붙으며 bwrap 준비 단계에 userns/capability를 허용하고
자식에는 `unpriv_bwrap`을 중첩해 capability를 거부한다. 파일·네트워크 정책 자체를 만드는
프로필이 아니므로 Codex의 실제 mount/network 제한을 반드시 별도로 검사한다.
root-owned binary와 profile로 제한하지만 이 경로를 사용하는 **다른 호스트 사용자/프로그램에도
적용될 수 있다**. 완전한 무영향 변경으로 분류하지 않는다. 기존 AppArmor attachment가
생겼으면 중복 로드하지 않고 충돌부터 해결한다. [공식 Codex sandbox 안내](https://learn.chatgpt.com/docs/sandboxing)

## 승인된 범위와 적용 순서

마스터는 2026-09-14에 위 digest의 bubblewrap 패키지와 단일 AppArmor 파일 적용,
실제 worker 및 허용·비허용 쓰기/네트워크/자식 종료/기존 서비스 검증, 실패 시 신규 실행 중지와
이번 변경의 되돌리기를 승인했다. 대상은 이 서버의 `/usr/bin/bwrap`,
`/etc/apparmor.d/bwrap-userns-restrict`와 그 파일의 kernel profile 둘이다.
기존 파일·프로필 발견 시 덮어쓰지 않고 재검토한다. 운영 timer 전환·배포·자동 병합은 제외한다.
이 범위의 승인을 다시 요구하지 않는다. 현재 차단은 아래에 기록한 sudo 인증 부재다.

아래 명령은 **미실행 수동 절차**이며 전체 블록을 자동 실행하는 설치기가 아니다.
인증된 실행자는 각 중단 조건과 되돌리기 절차를 확인한 뒤 단계별로 진행해야 한다.

1. 기존 profile/바이너리가 여전히 없는지, 패키지 simulation이 같은지, 기존 서비스 health와
   timer/queue baseline이 같은지 확인한다. 컨테이너 상태뿐 아니라 ID, StartedAt, PID,
   RestartCount도 비교하여 재시작을 놓치지 않는다. root `dpkg --audit`와 현재 패키지 transaction을
   확인한다. 다른 transaction이나 미구성 패키지가 있으면 중단한다. simulation의 Inst뿐 아니라
   Conf/Remv/Purg도 검사하여 bubblewrap 외의 변경·구성이 없음을 확인한다.
   `sudo aa-status`와 `rg`로 profile 이름·attachment
   충돌을 검사한다. 별도 작업은 중단하지 않는다. 기존 큐에는 진단 작업을 넣지 않는다.
2. 지정 digest의 파일을 root 소유 staging 디렉터리로 복사한 뒤 다시 hash를 검사한다.
   사용자 쓰기 가능한 원본과 root 실행 사이의 교체 위험을 없앤다. 아래 `${...}` 값은 이
   단계에서 사용할 전용 staging 경로는 `/var/tmp/ai-company-bwrap-20260914-reviewed`다.
   기존에 같은 경로가 있으면 자동 덮어쓰지 말고 멈춘다. 이번 파일의 rollback 보관 경로는
   staging 내 `rolled-back-profile`로 한정하며 이 경로가 이미 있어도 멈춘다.
3. 승인된 패키지·profile만 적용한다. `apparmor_parser`는 이미 있는 도구를 사용한다.

```bash
# Reviewed maintenance commands only. None of these mutations have been run.
set -euo pipefail
AI_COMPANY_ROOT_STAGE=/var/tmp/ai-company-bwrap-20260914-reviewed
AI_COMPANY_PACKAGE_SOURCE=/home/edward/ai-company/workspaces/agent-failover-loop/.ai-company/pr5-followup-2c04c489
sudo test ! -e "${AI_COMPANY_ROOT_STAGE}"
sudo install -d -o root -g root -m 0700 "${AI_COMPANY_ROOT_STAGE}"
sudo install -o root -g root -m 0600 \
  "${AI_COMPANY_PACKAGE_SOURCE}/bubblewrap_0.9.0-1ubuntu0.1_arm64.deb" "${AI_COMPANY_ROOT_STAGE}/"
sudo install -o root -g root -m 0600 \
  "${AI_COMPANY_PACKAGE_SOURCE}/package-inspection/usr/share/apparmor/extra-profiles/bwrap-userns-restrict" \
  "${AI_COMPANY_ROOT_STAGE}/"
# STOP unless both copied-file hashes exactly match the table above.
sudo sha256sum "${AI_COMPANY_ROOT_STAGE}/bubblewrap_0.9.0-1ubuntu0.1_arm64.deb" \
  "${AI_COMPANY_ROOT_STAGE}/bwrap-userns-restrict"
sudo apt-get --simulate install --no-install-recommends \
  "${AI_COMPANY_ROOT_STAGE}/bubblewrap_0.9.0-1ubuntu0.1_arm64.deb"
# STOP unless Inst/Conf affect only bubblewrap and dpkg has no pending unrelated work.
# Recheck both sysctl values are 1 immediately before installation.
sudo apt-get install --no-install-recommends \
  "${AI_COMPANY_ROOT_STAGE}/bubblewrap_0.9.0-1ubuntu0.1_arm64.deb"
# Check immediately after package postinst, before loading the profile.
sysctl kernel.unprivileged_userns_clone kernel.apparmor_restrict_unprivileged_userns
stat -c '%U:%G %a %n' /usr/bin/bwrap
# Require root:root, executable, no setuid/setgid, no file capabilities; never chmod +s.
getcap /usr/bin/bwrap
# STOP if profile/local includes/loaded attachments now exist; never overwrite.
sudo install -o root -g root -m 0644 \
  "${AI_COMPANY_ROOT_STAGE}/bwrap-userns-restrict" /etc/apparmor.d/bwrap-userns-restrict
sudo apparmor_parser --skip-kernel-load --skip-cache /etc/apparmor.d/bwrap-userns-restrict
sudo apparmor_parser -r /etc/apparmor.d/bwrap-userns-restrict
sysctl kernel.unprivileged_userns_clone kernel.apparmor_restrict_unprivileged_userns
```

4. 독립 일회성 진단에서 `PATH`와 `/usr/bin/bwrap` 실제 선택을 확인한다. 실행 추적은
   최소 명령의 `execve` 대상만 보관하며 환경·인증을 덤프하지 않는다. Codex의 실제 파일/
   네트워크 제한 설정을 유지한다. 추가 `cap_add`, Docker privileged, sysctl 변경,
   sandbox bypass flag, blanket AppArmor disable은 이 승인 대상에 없다.
5. 아래 양성·음성 검증을 모두 수행한 후 기존 서비스 상태와 timer/queue를 재확인한다.
   하나라도 실패하면 새 실제 모델 작업을 시작하지 않고 되돌리거나 재조정 보고한다.
6. 격리 검사가 통과해도 운영 timer 전환과 배포 승인을 대신하지 않는다. 새 격리 상태
   디렉터리에서만 작은 실제 개발→검사→독립 검수→Astra Ultra 최종 검수로 진행한다.

## 적용 후 필수 검증 (현재 미실시)

| 검사 | 방법과 통과 조건 |
|---|---|
| 실제 worker 최소 실행 | 같은 account/PATH/worktree/transient flags로 root-owned bwrap + true 성공, 실제 선택 경로 기록 |
| 허용 쓰기 | 별도 임시 Git worktree 내 marker 생성·읽기 성공. 기존 서비스 경로 사용 금지 |
| 범위 밖 쓰기 | 같은 uid가 소유한 별도 임시 **비허용 형제 디렉터리**에 marker 생성 시도; sandbox에서 거부되고 호스트에서 파일 없음 확인. `/root` 권한 거부만으로 대체하지 않음 |
| 네트워크 음성 | 네트워크를 금지한 실제 worker permission state에서 DNS 의존 없는 외부 TCP connect가 차단됨. 동일 목적지의 허용된 별도 호스트 baseline과 비교, timeout만이면 inconclusive |
| 자식 capability | bwrap 자식의 `/proc/self/status` CapEff=0; 권한 필요한 namespace 변경이 거부됨. 설치 성공만으로 추정하지 않음 |
| cgroup 종료 | bounded parent/child 진단에 stop/timeout 적용 후 unit MainPID=0, empty cgroup, 자식 종료 확인. 종료 불확실하면 RECONCILIATION_REQUIRED |
| 전역 정책 | 두 sysctl 값 모두 1, AppArmor enforce 유지, root-owned binary/profile hash 동일 |
| 기존 서비스 | talenta app/caddy/db/backup health·포트·config digest 및 운영 timer/queue 일치 |

최소 bwrap `--ro-bind / /` 명령은 쓰기 검증용이 아니다. 음성 검사에서는 실제 worker의
workspace-write permission state를 사용해야 한다. credentials나 기존 서비스 데이터는
테스트 대상으로 삼지 않는다.

## 되돌리기

이 변경으로 새로 설치한 profile이 맞고 다른 운영자가 이를 수정하지 않았는지 hash를 먼저
확인한다. 신규 진단/실행 cgroup 종료 후 `sudo apparmor_parser -R
/etc/apparmor.d/bwrap-userns-restrict`로 이번 profile을 unload하고 두 profile 이름이 사라졌는지
확인한다. 설치 파일을 승인된 backup 디렉터리로 이동한다. `apt-get --simulate remove bubblewrap`
가 다른 패키지를 제거하지 않을 때만 설치한 bubblewrap을 제거한다. `autoremove`는 하지 않는다.
전역 두 값은 1을 유지하고, 이전 bundled bwrap 실패 상태가 복구되었음을 기록한다.
기존 profile/binary가 사전 점검 때 존재했다면 이 삭제 절차를 사용하지 않고 원본 복원안을
새로 검토한다. 기존 timer, 큐, Caddy, Docker Compose 파일을 교체할 단계는 없다.


## 2026-09-14 승인 후 사전 점검 결과

19:34 KST 읽기 전용 [점검 스크립트](../scripts/preflight-reviewed-bwrap.py)를 실행했다.
결과는 `PREFLIGHT_ONLY`이며 설치/격리 성공 결과가 아니다.

- 두 전역 값 모두 `1`; 읽을 수 있는 명시적 sysctl assignment에도 비-1 override가 없다.
- 지정 package/profile SHA-256 일치. 대상 바이너리, profile, optional local include, staging 경로 없음.
- local DEB simulation은 bubblewrap의 Inst/Conf만 포함한다. 신규 1, 업그레이드·삭제 0.
  비-root `dpkg --audit` 출력은 비어 있다. root audit와 transaction 충돌 검사는 미실시다.
- 운영 timer는 active/enabled, 큐는 0건. 기존 timer/service/Caddy/Compose digest는 직전 기록과 같다.
  app/caddy/db는 running/healthy, backup은 running이며 healthcheck가 없다.
  네 컨테이너의 RestartCount는 모두 0; ID/StartedAt/PID를 다음 비교용 기준선에 포함했다.
- `sudo -n true`는 `sudo: a password is required`로 실패한다. 도구의 승인 실행도 uid1002
  edward로 실행되므로 root 인증을 대신하지 않는다. sudo 정책이나 그룹 권한은 변경하지 않았다.
- root의 loaded AppArmor profile 확인, 패키지 설치, profile 로드, 적용 후 제한값 및 실제 worker
  검증은 **미실시**다. 패키지·profile·staging에 변경이 없으므로 되돌릴 호스트 변경도 없다.

현재 Codex 0.154.0의 진단 명령은 명시적 permission profile을 요구한다.
아래의 일회성 설정은 구성 파일을 변경하지 않고 기존 bwrap 오류까지 도달했다.
이는 진단 명령의 옵션 수용 증거이며 실제 worker의 적용 구성 증거가 아니다.

```bash
/home/edward/.local/bin/codex sandbox -P quota_probe \
  -c 'permissions.quota_probe.filesystem={ ":root"="read", ":workspace_roots"={"."="write"} }' \
  -c 'permissions.quota_probe.network.enabled=false' -C /tmp -- /usr/bin/true
# exit 1: bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted
```

검수 미완료 자동 설치 초안은 제거했다. 재개 시 자동화한다면 다음 조건을 먼저 충족해야 한다.

1. 실행 전 진단 unit 식별자를 영속 기록하고, controller 종료 후 모든 해당 cgroup의 종료를 확인한다.
   native adapter의 on_spawn 이후 기록만으로는 프로세스 시작 직후 중단 공백을 보장하지 못한다.
2. 파일 생성과 profile 로드를 별도로 추적한다. 부분 복사 실패가 파일을 남길 수 있으며,
   로드하지 않은 profile의 unload 실패 때문에 다른 정리를 생략하면 안 된다.
3. apt/dpkg 중단 뒤 자식 transaction이 계속 실행 중인지 확인한다. 실행 중인 패키지 작업과
   rollback을 동시에 진행하지 않는다. 확인할 수 없으면 자동 완료로 보고하지 않는다.
4. 하나의 정리 실패가 다른 신규 실행 종료나 두 전역 값 검사를 생략하게 하지 않는다.
   종료·원복이 불확실하면 새 실행을 중단한 상태와 수동 복구 필요 사항을 보고한다.

인증된 서버 터미널을 사용할 수 있게 된 뒤 같은 승인 범위로 재개한다.
비밀번호를 문서·채팅에 기록하거나 전역 sudo 권한을 추가하는 절차는 포함하지 않는다.

## 모델 설정의 확인 수준과 다음 근거

| 계층 | 현재 근거 | 미확인 / 다음 수집 |
|---|---|---|
| 요청 | PR5 실행기의 argv에 Astra High / Claude Opus 5 Ultracode 요청, CLI 수용 | 새 실행에서도 argv·CLI version·binary digest를 execution ID에 묶기 |
| Codex 적용 구성 | `adapters/configuration_evidence.py`가 session/cwd/time/CLI0.154.0으로 제한한 turn_context model/effort | CLI 구성 증거이며 backend model/effort 확인 아님. exporter는 backend_model_verified=False 유지 |
| Claude 지원 목록 | matching initialize control_response의 model 및 supportedEffortLevels | 지원 목록은 실효 설정 아님. applied_effort/applied_ultracode=None 유지 |
| 실제 주 모델 | 기존 Claude 응답 metadata에 claude-opus-5 | Astra backend metadata 미확인. 모델의 자기 설명 제외 |
| 실효 effort / Ultracode | 기존 실행은 둘 다 미확인 | 요청 override/env/cap을 allowlist로 점검하고 해당 실행의 공식 runner/API metadata 필요 |
| 동적 Workflow | 기존 init 도구 목록·실제 실행에서 관찰 못 함 | 적정 substantive task에서 Workflow tool invocation, script/phase/child IDs, permissions, 완료·사용량 사실 수집 |

[공식 Codex 설정 reference](https://learn.chatgpt.com/docs/config-file/config-reference)는
`model_reasoning_effort`와 구성 override를 설명하지만 조회 시 표에는 `ultra`가 없다.
이 표만으로 Astra Ultra 지원 또는 backend 적용을 단정하지 않는다. 마스터의 Astra Ultra
요구를 임의로 `xhigh`로 바꾸지 않고, 계정에 제공된 공식 CLI catalog/control metadata와
실행 결과를 대조한다.

[공식 Claude 모델 설정](https://code.claude.com/docs/en/model-config#adjust-effort-level)에 따르면
Ultracode는 xhigh 요청과 workflow 동작을 묶는 CLI 설정이다. 환경 effort override,
workflow 비활성화, 모델 지원, 조직 cap으로 실효 설정이 달라질 수 있다.
[Workflow 문서](https://code.claude.com/docs/en/workflows)는 `-p`에서도 도구 권한 평가가
적용됨을 설명한다. 작은 읽기 호출에서 Workflow가 없었다고 기능 불가를 확정할 수 없고,
옵션 수용만으로 동작했다고 할 수도 없다. 실제 동작 증거를 수집하지 못하면 완료 조건은
미검증으로 남기고 마스터가 명시적으로 요구를 조정하기 전에는 완료 처리하지 않는다.
