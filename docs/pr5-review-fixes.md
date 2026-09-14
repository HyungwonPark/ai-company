# PR #5 추가 검수 수정 (2026-09-14)

기준은 PR #5의 `79af8279f266aea42bb9fedf5d862d48cb797289`이며 같은 구현 브랜치에서 수정했다.
운영 quota timer, 기존 checkout과 큐는 변경하지 않았다.

## 대기 결과 저장 직후의 중단

기존 `_tick`은 `WAITING_QUOTA`/`WAITING_RETRY`를 미반영 완료 사실에서 제외했다.
그 결과 queue commit과 `_handle_job` 사이에서 중단되면 다음 실행을 시작하기 전에
사용량·작성 세션·공유 cooldown·변경된 HEAD/부분 파일을 반영하지 못했다.
수정 전에 신규 재현 테스트 4개가 모두 실패했다.

`_ingest_waiting_result`가 영속 실행 사실을 먼저 반영한다. 실행 attempt별 observation marker,
사용량/작성 이력, 공유 cooldown, 저장소 snapshot, 인수인계 파일 참조 및 세션 체크포인트 갱신을
같은 SQLite 트랜잭션에 묶었다. 상태 반영 전에 다시 중단되면 전부 rollback되고,
반영 후 배정 전에 중단되면 다음 worker가 marker를 보고 중복 집계하지 않는다.
배정기는 어느 작업을 선택하기 전에 모든 미반영 대기 결과를 복구하므로 다른 논리 작업도
이미 제한된 공유 계정을 가용 후보로 선택하지 않는다. 다른 실행의 더 늦은 cooldown을 줄이지 않는다.

종료/guard 확인과 저장소 lock 안에서 실제 snapshot을 queue의 저장된 실행 사실과 대조한다.
일치하는 변경 HEAD·dirty/new files는 인수인계에 반영한다. 외부 변경이 있거나 기존 실행이
불확실하면 `NEEDS_RECONCILIATION`으로 중단하며, 이미 발생한 사용량·작성 이력·계정 한도는 보존한다.
이 때문에 살아 있는 이전 실행 테스트의 기대 상태도 일반 BLOCKED에서 재조정 상태로 구체화했다.

새 회귀 사례는 quota/retry 저장 직후 중단, dirty/new files 및 커밋된 HEAD 복구,
동일 세션 재개 전에 이전 시도 집계, 다른 작업 배정 전에 공유 한도 복구,
외부 변경 시 비용 보존, 반영 트랜잭션 도중/직후의 재중단을 포함한다.

## CI 출처와 실행 정의

기존 Verifier에 미승인 workflow의 run을 넣으면 같은 이름의 성공 검사와 일치하는 artifact 내용만으로
승인됐다. 기준 커밋의 Verifier를 별도로 로드해 그 수락을 재현했고 수정본의 거부를 확인했다.

이제 승인된 workflow 파일명으로 GitHub의 workflow ID/경로를 조회하고 다음을 결합한다.

- run의 workflow ID/경로, 실행 저장소, head, 완료/성공, check suite, run attempt.
- 실제 workflow 정의의 ref/SHA. PR 이벤트는 현재 merge SHA, push/branch workflow_dispatch는 head를 사용한다.
  head와 실행 정의 SHA 양쪽의 workflow 내용이 승인된 digest와 같아야 한다.
- 해당 run의 **현재 attempt jobs**가 필수 check-run URL·이름·SHA·성공 상태를 소유해야 한다.
- artifact 상세 metadata의 workflow_run ID와 head가 해당 run과 일치해야 한다.
- artifact 안의 `workflow_ref`, `workflow_sha`, `run_attempt`가 위 실행 출처와 일치해야 한다.
- 증적 조회 종료 전에 PR과 run을 다시 확인한다.

`pull_request_target`, 확인하지 못하는 이벤트, 별도 승인 정의 목록이 필요한 reusable workflow는
현재 승인하지 않는다. URL 문자열만으로 검사 소속을 인정하지 않는다.
동일 이름/내용의 미승인 workflow, 잘못된 workflow ID, 변경된 실행 정의, 다른 run/attempt의 check,
다른 run/커밋의 artifact, 누락된 정의 metadata를 거부하는 테스트를 추가했다.

`docs/examples/flow-ci.yml`의 inline producer는 GitHub가 제공하는 `GITHUB_WORKFLOW_REF`,
`GITHUB_WORKFLOW_SHA`, `GITHUB_RUN_ATTEMPT`를 기록한다.
기존 artifact에 이 필드가 없으면 `WAITING_CHECKS`이며 소급 승인하지 않는다.
producer 변경은 workflow digest를 바꾸므로 기존 고정 정책을 덮어쓰지 않고 새로 승인된 명세가 필요하다.
현재 `migrate-policy`는 CI 승인 조건 변경을 허용하지 않는다. 운영 기존 session 큐에는 영향이 없다.

출처: [GitHub workflow runs API](https://docs.github.com/en/rest/actions/workflow-runs),
[GitHub 기본 환경변수](https://docs.github.com/en/actions/reference/workflows-and-actions/variables).

## bwrap: 같은 worker 환경의 최소 재현과 원인

Codex 0.154.0의 bundled helper:

```
/home/edward/.nvm/versions/node/v22.13.0/lib/node_modules/@openai/codex/node_modules/@openai/codex-linux-arm64/vendor/aarch64-unknown-linux-musl/codex-resources/bwrap
```

실제 `run_session`과 같은 사용자 systemd transient unit 설정(`Type=exec`, `KillMode=control-group`,
`TimeoutStopSec=5`, `UMask=0077`, working directory/PATH)에서 아래 최소 명령을 실행했다.

```bash
BWRAP=/home/edward/.nvm/versions/node/v22.13.0/lib/node_modules/@openai/codex/node_modules/@openai/codex-linux-arm64/vendor/aarch64-unknown-linux-musl/codex-resources/bwrap
"$BWRAP" --unshare-user --unshare-pid --unshare-net \
  --ro-bind / / --proc /proc --dev /dev /usr/bin/true
```

exit 1, `bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted`.
`NoNewPrivileges=yes`를 추가한 경우에도 동일했다. `--version`은 성공했다.
9월 14일 09:39:48 KST 커널 audit에는 같은 bundled bwrap 경로/PID에 대해
`userns_create` 시 `unprivileged_userns`로 전환하고 `capname="net_admin"`을 DENIED한 기록이 있다.
다른 재현에서도 `setpcap` 거부가 함께 나타났다.
`kernel.unprivileged_userns_clone=1`, `kernel.apparmor_restrict_unprivileged_userns=1`이며,
배포판 `/usr/bin/bwrap` 및 `bwrap-userns-restrict` 프로필은 설치되어 있지 않았다.
따라서 모델·네트워크 사용량 문제가 아니라 이 서버의 AppArmor user namespace 정책과
bundled bwrap 사이의 호환 문제라는 근거를 확보했다.

격리를 유지하는 조치는 [공식 Codex sandbox 문서](https://learn.chatgpt.com/docs/sandboxing?sandbox-os=ubuntu-debian)의
Ubuntu 24.04 절차다. 검토할 구체적 변경은 배포판의 root 소유 `bubblewrap` 설치,
`apparmor-profiles`의 **bwrap 전용** `bwrap-userns-restrict` 프로필 설치·로드다.
프로필은 먼저 `apparmor_parser --skip-kernel-load`로 검사할 수 있다.
승인된 유지보수 단계에서 적용 후 아래를 검증해야 한다.

1. 실제 worker PATH에서 배포판 bwrap이 선택되는지 확인한다.
2. 위 user/pid/net namespace 최소 명령과 `codex sandbox linux -- /usr/bin/true`가 성공하는지 확인한다.
3. 같은 정책으로 worktree 밖 쓰기와 허용되지 않은 네트워크가 계속 차단되는지 확인한다.
4. global userns 제한값이 계속 1이고 호출별 cgroup 종료/운영 timer 경로가 유지되는지 확인한다.

이번 요청의 운영 배포 금지 범위에 따라 패키지/커널 프로필 설치·로드는 하지 않았다.
전역 sysctl 변경, AppArmor 해제, sandbox 우회, CAP_NET_ADMIN 부여로 통과시키지 않았다.
위 조치는 문서와 재현 근거를 갖춘 미적용 운영 변경이며, 성공했다고 보고하지 않는다.

## 설정 증거 연결과 실제 작업 재확인

`configuration_evidence.py`가 CLI가 제공하는 구성 증거를 결과에 별도로 붙인다.
증거의 종류를 분리하며 기존 `observed_models/observed_efforts/observed_ultracode` 승인 조건을 낮추지 않는다.

- Codex: 공식 [app-server](https://learn.chatgpt.com/docs/app-server)의 `thread/read`에서 모델과 추론 설정을
  확인했고, `model/list`에서도 Astra의 high/ultra 지원을 확인했다. 설치된 schema는 thread 설정이
  **per-turn execution telemetry가 아님**을 명시한다. 따라서 그것만으로 실제 실행 모델을 승인하지 않는다.
  worker는 CLI 0.154.0이 저장한 해당 session의 rollout 중 현재 실행 시간·worktree·session ID에 맞는
  `turn_context`만 읽는다. 설정 모델/effort와 turn ID를 기록하되 backend model 검증과 구분한다.
  불안정한 기록 포맷이므로 다른 CLI 버전/다른 세션/과거 turn은 확인 불가로 남긴다. 인증 파일은 읽지 않는다.
- Claude: 같은 `--model claude-opus-5 --effort ultracode` 신규/재개 옵션을 유지한다.
  stream-json `initialize` control response의 모델 목록에서 `claude-opus-5` 및 `xhigh` 지원을 확인한다.
  이 응답은 지원 목록이므로 적용 effort/Ultracode는 null로 남긴다. 계정 metadata는 구성 증거에 복사하지 않는다.
  실제 assistant/system의 주 모델은 Opus 5로 확인됐다. `/effort` 확인은 synthetic CLI 응답만 반환했고
  모델 사용량은 없었으나 실효 Ultracode 설정을 제공하지 않았다. 동적 Workflow 실행의 새 증거도 확보하지 못했다.

실제 새 호출 결과:

| 호출 | 결과 |
|---|---|
| Codex High 작은 호출 | 성공, session `01a09d56-e832-7d50-b7c4-6aea8bf852d6`, 해당 turn 설정 Astra/high 관찰 |
| Claude Opus/Ultracode 작은 호출 | 성공, session `a768bc58-8513-4e7a-ba69-c1a5e435e845`, Opus 5 응답 및 xhigh 지원 목록 확인 |
| 격리된 임시 저장소의 실제 개발 | session `01a09d59-94d7-7492-916e-5e917b756f7a`, Astra/high 설정 관찰, 첫 명령의 bwrap 오류로 BLOCK |

개발 재시도는 변경·검사·커밋 없이 중단됐고 HEAD/작업 파일은 그대로였다. CLI 세션 종료 category는 success지만
구조화 작업 결과는 BLOCK이므로 작업 완료로 승격하지 않는다. 각 호출의 cgroup 종료도 확인했다.
따라서 실제 개발→검사→독립 검수→Astra 최종 검수의 완주는 이번에도 차단됐다.
후속 단계를 건너뛰거나 모의 증거를 실제 승인으로 바꾸지 않았다. 실제 계정 한도를 소진하지 않았다.
원문 증거는 `.ai-company/review-fixes/`에 비공개 보관하고 인증 정보나 전체 환경변수를 게시하지 않았다.

## 최종 검증

`uv sync --frozen`, 전체 **140개 테스트**(기존 116 + 신규 24), 기존 demo `DEMO_READY`를 확인했다.
위 새 Codex/Claude 세션을 명시 ID와 동일 설정으로 재개하는 작은 호출도 모두 성공했고 cgroup 종료를 확인했다.
원격 CI와 변경 커밋은 PR #5 본문에 현재 head 기준으로 갱신한다.
