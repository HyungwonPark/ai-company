# 사용량 제한 자동 대기·재개

기준일: 2026-09-14. 기존 개발 루프는 simulation이며, 이 기능은 같은 `Task` 명세를 사용하는 CLI 세션 실행 단계이다. `SESSION_COMPLETED`는 모델 세션 실행 완료를 뜻하고, 테스트·독립 검수·원격 CI 통과 또는 병합 준비를 뜻하지 않는다.

## 상태와 저장 계약

| 상태 | 처리 |
|---|---|
| `READY` | worker가 실행할 수 있는 세션 |
| `RUNNING` | 외부 실행을 시작하기 전에 커밋한 실행 사실과 lease |
| `WAITING_QUOTA` | 계정 사용량 또는 rate limit. 같은 세션을 예약 재개 |
| `WAITING_RETRY` | 확인된 일시적인 통신 오류. 같은 세션을 예약 재개 |
| `SESSION_COMPLETED` | 세션 실행 완료. 이후 worker가 중복 실행하지 않음 |
| `BLOCKED` | 인증·권한·승인·테스트·코드·결제 오류 또는 분류 불가. 자동 재시도 없음 |
| `NEEDS_RECONCILIATION` | 이전 실행·저장소 상태를 확정할 수 없음. 자동 재시도 없음 |
| `NEEDS_CONTEXT_HANDOFF` | 컨텍스트 부족. 체크포인트를 저장하고 새 세션 인수인계 대기 |

`<state-dir>/sessions.sqlite`에 작업별 `task_id`, `agent_id`, provider, `session_id`, worktree 절대 경로, `head_commit`, 마지막 완료 단계, 체크포인트, 총 재시도 횟수·현재 재시도 묶음 횟수, `reset_at`, `resume_at`(UTC Unix 초), 실행 횟수, lease 소유자·만료 시각, PID·PGID·부팅 ID·프로세스 시작 tick, 결과 로그 참조를 저장한다. 상태 전이는 `session_events`에도 저장한다. 기존 LangGraph 체크포인트·`executions.sqlite`는 변경하지 않는다.

동일 task/agent 접수는 고정된 명세·정책·세션 설정과 일치하면 기존 결과를 반환한다. 다른 설정으로 같은 작업을 덮어쓰지 않는다. 새로운 논리 작업에는 새 task ID 또는 agent ID를 사용한다. 멱등성 범위는 같은 영속 큐이며, 다른 큐로 같은 작업을 복제하지 않는다.

worker는 시작 전 `RUNNING`과 lease를 저장한다. 상태 디렉터리의 flock, Git 공통 디렉터리의 저장소 flock, 영속 `ai-company-session-active.json` 실행 표식을 함께 사용한다. 표식은 spawn 전에 fsync하고, 자식 프로세스 식별 정보를 추가한다. 관리 프로세스만 죽어도 이 표식이 남아 다른 작업·다른 상태 디렉터리의 동시 실행을 막는다. 외부 실행의 종료 확인과 결과 트랜잭션이 완료된 뒤에만 해제한다.

만료된 lease는 재실행 허가가 아니다. `RUNNING` 중 worker가 중단되면 기존 실행을 확인하고 `NEEDS_RECONCILIATION`으로 멈춘다. 저장된 대기·완료 사실은 재사용한다. 결과 저장 후 표식 해제 전에 중단되면, 원래 큐가 저장된 상태와 프로세스 종료를 재확인해 표식을 해제한다. 깨진 저장소 하나는 해당 작업을 중단시키며 다른 정상 저장소의 작업을 막지 않는다.

재개 직전 worktree의 정규화 경로, origin 저장소, Git 공통 디렉터리, HEAD·브랜치, index·추적 파일 변경·무시되지 않은 untracked 파일 내용의 fingerprint를 비교한다. 대기 중 외부 변경이 있거나 이전 프로세스가 살아 있으면 실행하지 않는다. 파일 잠금·fingerprint는 OS 권한 격리를 제공하지 않는다.

## 재개 시간과 오류 분류

기본 정책은 reset 시각 이후 30초 여유, reset 미제공 시 60초부터 2배 증가·최대 3,600초 백오프, 재시도 3회마다 최소 3,600초 cooldown이다. 제한이 반복돼도 실패로 바꾸지 않고 재대기한다. reset 시각이 미래에 있으면 그 이전으로 상한을 잘라 재개하지 않는다.

개별 CLI 실행 제한시간은 300초이며 최대 설정값은 3,600초다. worker는 매 실행마다 이 제한시간을 새로 적용한다. SQLite에서 기다린 시간은 차감하지 않는다. 별도 누적 비용/실행 시간 예산이 필요한 경우, 이 실행별 제한과 구분해 추가해야 한다. 기존 simulation의 작업 전체 300초 정책은 그대로이고 세션 큐에는 적용하지 않는다.

허용한 provider 오류 envelope·코드만 분류한다. 모델의 일반 본문·도구 출력·stderr의 `quota` 문자열은 재시도 근거로 쓰지 않는다. 인증, 권한·sandbox, 승인 대기, 테스트 실패, 코드 오류, 결제·지출 상한, CLI turn/budget 제한, 알 수 없는 오류는 자동 재시도하지 않는다. timeout·종료 불확실성도 일시 통신 오류로 단정하지 않는다. 기존 개발 루프의 반려→수정 정책은 별도의 정책이다.

- Codex 0.154.0 `exec --json`은 `thread.started.thread_id`와 message 기반 오류를 제공한다. 확인된 오류 envelope의 제한 문구만 인식한다. 로컬 시간으로 표현된 reset 문구는 날짜·시간대가 불명확해 추측하지 않고 백오프로 처리한다. 명시적 UTC epoch/시간대 포함 reset 값이 제공되면 사용한다. [해당 버전의 이벤트 정의](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/exec/src/exec_events.rs), [오류 정의](https://github.com/openai/codex/blob/rust-v0.154.0/codex-rs/protocol/src/error.rs)
- Claude는 공식 SDK의 `rate_limit_event.rate_limit_info.status=rejected`와 `resetsAt`, `assistant.error`, 최종 result 실패 필드를 바탕으로 분류한다. `allowed_warning`과 내부 API 재시도 이벤트는 별도 외부 재개를 유발하지 않는다. 공개 SDK 규격의 fixture를 검증했으며 설치된 계정의 실제 전송은 미검증이다. [공식 파서](https://github.com/anthropics/claude-agent-sdk-python/blob/main/src/claude_agent_sdk/_internal/message_parser.py)

quota인데 session ID를 얻지 못했다면 `WAITING_QUOTA`를 보존하고 `resume_at=null`로 둔다. 작업을 새 세션으로 무조건 시작하지 않는다. 정상적인 자동 복구에는 저장된 세션 식별자가 필요하다.

## 실행과 운영

현재 서버 checkout은 `/home/edward/ai-company/workspaces/harness-loop-foundation`, 운영 큐는 `/home/edward/ai-company/state/sessions`이다. CLI 경로는 기존 서버 설치를 사용하며 인증 파일을 복사하거나 출력하지 않는다.

아래 `submit`은 등록만 한다. 활성화된 타이머가 있는 운영 큐에 실제 작업을 등록하면 모델 CLI가 실행된다. task JSON의 repository는 worktree의 GitHub origin과 일치해야 한다. checkpoint JSON에는 완료된 단계와 인수인계 근거를 넣는다.

```bash
cd /home/edward/ai-company/workspaces/harness-loop-foundation
.venv/bin/ai-company session submit \
  --task /path/to/task.json --agent codex --agent-id codex-developer \
  --worktree /path/to/isolated-worktree --session-id YOUR_SAVED_SESSION_ID \
  --last-completed-stage verify --checkpoint /path/to/checkpoint.json \
  --state-dir /home/edward/ai-company/state/sessions

.venv/bin/ai-company session status --state-dir /home/edward/ai-company/state/sessions
.venv/bin/ai-company session worker --state-dir /home/edward/ai-company/state/sessions
```

새 작업 세션을 시작하려면 `--session-id`를 생략한다. CLI가 출력한 ID가 저장된다. Codex 재개 argv는 `codex exec resume <session_id> --json -`, Claude는 `claude -p --output-format stream-json --verbose --resume <session_id>`이며 프롬프트는 stdin으로 전달한다. `--last`를 사용하지 않는다.

`--retry-policy /path/to/policy.json`으로 접수 시 정책을 고정할 수 있다.

```json
{
  "reset_grace_seconds": 30,
  "backoff_initial_seconds": 60,
  "backoff_max_seconds": 3600,
  "retries_per_cycle": 3,
  "cycle_cooldown_seconds": 3600,
  "execution_timeout_seconds": 300
}
```

### 사용자 systemd timer

`deploy/systemd/ai-company-quota.service`와 `.timer`는 현재 서버 경로를 사용하는 사용자 unit이다. 다른 서버에서는 checkout·Python과 PATH의 Node/Codex 경로를 먼저 조정한다. 신규 타이머 설치 방법:

```bash
install -d -m 700 ~/.config/systemd/user
install -m 644 deploy/systemd/ai-company-quota.service ~/.config/systemd/user/
install -m 644 deploy/systemd/ai-company-quota.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now ai-company-quota.timer
loginctl enable-linger "$USER"
systemctl --user list-timers ai-company-quota.timer
```

2026-09-14 이 서버에서 `ai-company-quota.timer`의 **enabled/active**와 `Linger=yes`를 확인했다. 매분 due 작업 하나를 처리하고 worker가 종료된다. 예약이 없으면 즉시 `IDLE`로 끝난다. 타이머는 재부팅/로그아웃 후에도 사용자 manager가 실행할 수 있도록 구성했다. 실제 서버 재부팅 시험은 하지 않았다. 기존 서비스는 재시작하거나 변경하지 않았다.

```bash
# 신규 예약 실행 중지. 이미 실행 중인 worker는 별도 확인한다.
systemctl --user disable --now ai-company-quota.timer
systemctl --user status ai-company-quota.service
# 이 프로젝트의 진행 중 실행을 취소해야 하는 경우에만 사용한다.
systemctl --user stop ai-company-quota.service
```

worker를 강제 중단하면 다음 실행에서 불확실한 작업은 자동 재시작하지 않는다. `NEEDS_RECONCILIATION`은 로그·프로세스·Git 결과를 운영자가 대조해야 한다. 자동 승인 또는 DB 수정 명령은 제공하지 않는다. 활성 프로세스가 있을 수 있는 실행 표식을 수동 삭제해 재호출하지 않는다.

### 컨텍스트 부족

컨텍스트 부족은 사용량 제한으로 스케줄하지 않는다. `<state-dir>/handoffs/`의 체크포인트에는 task, 기존 session ID, 마지막 완료 단계, 저장소 fingerprint, 완료 근거 및 결과 로그 참조를 저장한다. 아래 명령은 그 체크포인트를 전달할 **새** 세션을 예약한다.

```bash
.venv/bin/ai-company session handoff --job-id JOB_ID \
  --state-dir /home/edward/ai-company/state/sessions
```

quota 대기 작업에는 이 명령을 허용하지 않는다. 새 세션은 기존 세션과 다르게 기록되고 이전 session ID 목록을 보존한다. handoff도 재개 전 저장소 변경·기존 실행 여부를 확인한다.

## 검증과 변경 파일

자동 검증은 계정 한도를 소비하지 않는다.

```bash
uv sync --frozen
uv run --frozen python -m unittest discover -s tests -v
uv run --frozen ai-company demo --task examples/demo-task.json
# 실제 사용자 systemd timer + 생성한 Codex fixture. 비어 있는 새 출력 경로 사용.
.venv/bin/python scripts/verify_quota_timer.py --output-dir .ai-company/verification/new-timer-smoke
```

단위 테스트는 reset 유무, 백오프·cooldown, 동일 세션 재개 성공·재대기, 인증·권한·sandbox 오류 재시도 금지, 2시간 대기 후 300초 실행 한도 유지, 재시작 복구·중복 차단, 저장소 변경, 미확정 실행 표식, 깨진 저장소와 다른 작업 진행, 컨텍스트 handoff를 포함한다.

타이머 검증은 `모의 제한 → WAITING_QUOTA 커밋 → 최초 worker 종료 → 다른 systemd worker → 예약 시각 도달 → codex exec resume <같은 ID> → SESSION_COMPLETED`를 확인한다. 이후 추가 timer tick에도 총 호출 2회(최초 1회 + 재개 1회)여야 한다. 검증용 임시 unit은 자동 제거하며 운영 타이머는 유지한다. 실제 모델 서비스 호출·계정 quota 소진·원격 개발 작업 CI 통합은 이 시험에 포함되지 않는다.

2026-09-14 서버 검증 결과:

| 검증 | 결과 |
|---|---|
| `uv sync --frozen` | 성공, lock 변경 없음 |
| 전체 unittest | **79개 통과**, 16.528초: 기존 루프 27 + CLI fixture 27 + 큐/복구/명령 25 |
| 기존 demo | `mode=simulation`, `DEMO_READY`, `attempt=1` |
| 실제 사용자 timer + 모의 Codex | 성공. `WAITING_QUOTA` → 다른 worker PID → `codex exec resume quota-timer-session` → `SESSION_COMPLETED` |
| 중복 재개 | 총 CLI 호출 2회, 재개 1회. 완료 뒤 추가 timer tick에서 호출 증가 없음 |
| 재개 시각 | 예약 UTC epoch `1789328509.4922016`, 실제 모의 재개 `1789328510.1093438` |
| 전용 운영 timer | `enabled`, `active`, worker `Result=success`, `ExecMainStatus=0`, `Linger=yes` |
| 독립 검수 | 살아 있는 고아 CLI의 동일/다른 큐 실행 차단 재현 통과. 깨진 저장소의 큐 정체 수정 및 회귀 검증 |

원본 로그는 서버 checkout의 `.ai-company/verification/quota-all-tests.log`, `quota-demo.json`, `quota-sync.log`, `timer-smoke-20260914/result.json`에 있다. 운영 큐는 검증 당시 비어 있었고 실제 계정 모델 호출은 수행하지 않았다. 임시 검증 unit은 제거했다. 위 timer 성공은 로컬 서버 검증이며 GitHub 원격 CI 결과와 구분한다.

| 파일 | 변경 |
|---|---|
| `src/ai_company/sessions.py` | 영속 상태·예약·lease·저장소 검증·실행 표식·handoff |
| `src/ai_company/adapters/session_cli.py` | Codex/Claude argv·이벤트 분류·세션 수집·프로세스 감독 |
| `src/ai_company/cli.py` | session submit/status/worker/handoff 명령 |
| `tests/test_session_queue.py`, `tests/test_session_cli.py` | 큐·재시작·프로세스·오류 분류 동작 검증 |
| `deploy/systemd/ai-company-quota.*` | 매분 실행하는 사용자 서비스·타이머 |
| `scripts/verify_quota_timer.py` | 실제 타이머와 모의 CLI의 프로세스 간 재개 검증 |
| `README.md`, `AGENTS.md`, `docs/` | 운영 범위·기존 개발 루프와의 구분·인수인계 |
