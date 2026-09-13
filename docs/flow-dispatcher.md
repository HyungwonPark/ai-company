# 역할 이관과 개발·검수 배정기

PR #3 (`e7ca36a`)의 세션 큐 위에 PR #4 (`62bdc5a`)의 정책을 구현한 후속 초안이다.
기존 `session` 명령과 운영 큐는 그대로 유지한다. 새 `flow` 큐로 기존 작업을 자동 수입하지 않는다.

## 실행과 상태

`flow_contracts.py`는 역할별 후보, 계정 공유 한도, 모델 설정, 경로·기능 권한, 누적 예산과
원격 CI 조건을 고정한다. `dispatcher.py`는 Python/SQLite로 후보를 선택한다. 배정 자체에는
모델을 호출하지 않는다. 현재 CLI 인증은 제공자별 기존 계정 하나만 사용하므로, 동일 제공자의
자격 증명 별칭을 바꿔 별도 계정으로 등록하는 것도 거부한다.

- PM/최종 검수: `gpt-6-astra`, `ultra`. PM과 최종 검수는 새 세션으로 분리한다.
- 개발/1차 검수: `gpt-6-astra`, `high` 또는 `claude-opus-5`, `xhigh`, `ultracode_enabled=true`.
- 작성에 참여한 모든 세션 ID를 기억하며 같은 세션의 자기 승인을 거부한다. 가능한 경우 1차 검수는 다른 제공자를 먼저 선택한다.
- `examples/flow-agents.json`은 구성 예다. 검증 필드는 비어 있으므로 실사용 후보로 자동 활성화되지 않는다.

quota/rate limit이면 계정 전체를 cooldown으로 기록하고 적격 대체자를 우선 배정한다.
없으면 `WAITING_CAPACITY`, `WAITING_PM`, `WAITING_FINAL_REVIEW`와 예약 시각을 저장한다.
하위 세션에는 PR #3의 `WAITING_QUOTA`, reset+여유 시간, 제한된 백오프, 동일 세션 ID가 남는다.
확인된 일시 통신 오류는 동일 세션 예약 재시도만 한다. 인증·권한·샌드박스·승인·결제·코드 오류는
이관/자동 재시도하지 않는다. 컨텍스트 부족은 `NEEDS_CONTEXT_HANDOFF`에서 명시적인 새 세션 인수인계로 진행한다.
세션 ID 없이 제한된 경우 종료가 확인되고 전체 체크포인트를 만들 수 있어야 다른 제공자로 넘긴다.
그 대체자도 없으면 재조정이 필요하다.

PM이 대기해도 `approved_plan=true`인 독립 작업은 진행한다. 최종 검수 대기는 그 단계에만 적용한다.
명시적인 작업 의존성은 선행 작업의 실제 `MERGE_READY`가 있어야 해제한다. 모의 완료로 실제 의존성을 해제하지 않는다.

## 소유권·복구·예산

SQLite `flow_tasks`/`flow_events`에는 논리 task ID, 명세 digest, 단계, generation, 실행/세션 ID,
worktree/HEAD/dirty digest, 지적·검사·검수, 재개 시각, 누적 실행 횟수·시간·비용·수정 횟수를 저장한다.
세션의 retry/lease/process 사실은 기존 `session_jobs`/`session_events`에 유지한다.
이관은 같은 DB 트랜잭션에서 이전 예약을 `SUPERSEDED`로 해제하고 새 generation을 만든다.
새 세션 제출은 execution ID를 멱등 키로 사용한다. 일반 `session worker`는 flow 소유 행을 건너뛴다.
회복한 이전 제공자가 소유권을 빼앗지 않으며, 이전 generation의 늦은 결과도 거부한다.

인수인계는 모델 요약 없이 명세, 계획, 단계, 미해결 지적, 검증 결과, 다음 행동과 Git/file 상태로 만든다.
변경 파일·새 파일은 private content-addressed 사본으로 보존한다. 삭제도 기록한다.
허용 경로 밖의 변경, 민감 파일 경로, symlink, 과도한 파일은 차단한다. 부분 변경은 미검증 상태로 전달한다.
검사·검수에 들어가기 전에 후보 커밋의 worktree가 깨끗해야 한다.

배정기 lock, 저장소 lock, 세션 lease, 재시작에도 남는 저장소 실행 guard를 함께 사용한다.
실행 전/이관 전 저장소 fingerprint와 기존 실행 종료를 검사한다. 실제 CLI와 검사는 호출별
`ai-company-run-<uuid>.service`의 systemd 사용자 cgroup에서 실행한다. `KillMode=control-group`으로
내부 workflow/하위 에이전트 및 분리된 process group을 함께 종료하고, unit/cgroup 종료를 확인한다.
종료 확인이 불가능하거나 실행 중 worker가 죽으면 `NEEDS_RECONCILIATION`으로 멈춘다.
lease 만료만으로 실행 중 작업을 재전송하지 않는다. 검사 중 중단도 guard를 보존하고 자동 재실행하지 않는다.
전역 권한/관리 정책은 해제하지 않는다. user systemd를 사용할 수 없는 live 환경은 이 실행 경로를 사용할 수 없다.

실행시간에는 실제 호출·검사 시간만 더하고 예약 대기시간은 더하지 않는다. 이관으로 예산은 초기화되지 않는다.
확인되지 않는 비용은 `cost_unknown`으로 기록한다. 금액 상한이 지정된 정책에서는 확인 가능한 Claude 비용과
CLI 금액 상한이 필요하며, 비용이 관측되지 않으면 추가 실행을 차단한다. Codex에 금액 상한을 지원한다고 가정하지 않는다.
금액 상한이 없는 경우에도 누적 실행시간/횟수/수정 상한은 적용한다. CLI 비용은 제공자가 보고한 추정값이다.

## 개발·검수 그래프와 CI

LangGraph의 한 단계씩 실행하는 그래프는 `PM → 개발 → 검사 → 독립 1차 검수 → 최종 검수 → gate`를 연결한다.
검사 실패/수정 요청은 누적 수정 한도 안에서 개발·재검사로 돌아간다. 최종 검수자는 Astra Ultra로 고정한다.
SQLite 외부 실행 사실을 먼저 복구하고 순수 그래프 전이를 재계산한다. 그래프 checkpoint가 실행 사실을 덮어쓰지 않는다.

`SESSION_COMPLETED`는 CLI 종료 사실일 뿐이다. 구조화된 `StageReport`의 execution/generation/role/task/policy,
현재 커밋, 검사 증적 digest, 미해결 지적을 검증해야 다음 단계로 간다. 실제 실행의 모델·추론·Ultracode
메타데이터가 없거나 다르면 결과를 합격시키지 않는다. 모델의 자기 설명은 설정 증거가 아니다.

실제 gate는 다음 GitHub 증거를 요구한다.

1. PR의 현재 head와 원래 task base가 일치하고, 승인한 workflow 파일의 SHA-256이 일치한다.
2. 필수 GitHub Actions check들이 최신 성공이며 같은 run을 가리킨다.
3. 해당 성공 run의 `ai-company-evidence` artifact에 실제 tested SHA, head/base, task/policy digest, run ID가 있다.
4. 그 증거에 묶인 독립 1차/최종 검수가 모두 PASS이며 지적이 해결됐다.

실제 MERGE_READY도 다음 timer 호출에서 gate를 재확인한다. CI 증거가 바뀌면 검사·두 검수를 다시 요구한다. 외부 코드 변경은 차단하여 재조정하게 한다.
원격 attestation이 없으면 `WAITING_CHECKS`다. 모의 모드는 항상 `DEMO_READY`이며 병합 준비로 승격하지 않는다.
`docs/examples/flow-ci.yml`은 고정 action SHA와 inline attestation 생산자를 포함한 예다.
완전한 기본값을 포함한 `FlowSpec.model_dump(mode="json")`을 `flow-spec.json`으로 저장해 사용한다.
실제 작업의 승인된 필수 검사 명령에 맞게 workflow를 검토한 뒤 그 파일 digest를 정책에 고정한다.
이 저장소의 기존 회귀 CI 성공은 특정 실사용 작업의 attestation이나 최종 승인과 별개다.

## 운영과 명시적 정책 이행

프로젝트 전용 `uv sync --frozen` 환경에서 실행한다.

```bash
ai-company flow submit --spec /private/task-flow.json --state-dir /private/flows
ai-company flow status --state-dir /private/flows
ai-company flow worker --state-dir /private/flows
ai-company flow context-handoff --task-id TASK --state-dir /private/flows
ai-company flow migrate-policy --task-id TASK --spec /private/replacement.json --reason '승인된 예산 변경' --state-dir /private/flows
ai-company flow rollback-policy --migration-id MIGRATION --reason '이전 정책 복원' --state-dir /private/flows
```

`worker`는 준비된 한 작업의 한 단계를 처리하고 종료한다. quota 해제까지 sleep하지 않는다.
`deploy/systemd/ai-company-flow.{service,timer}`는 별도 이름의 opt-in 설치 템플릿이다.
검토된 release를 `%h/ai-company/releases/flow`에 준비하고 private state 경로와 PATH를 확인한 뒤,
해당 두 파일을 사용자 unit 디렉터리에 설치하여 `daemon-reload`, `enable --now ai-company-flow.timer`로 활성화한다.
개발 worktree로 운영 경로를 교체하지 않는다. 이번 PR에서는 이 운영 배포를 수행하지 않았다.

기존 PR #3 큐는 기존 worker로 배출하고 새 작업부터 별도 flow 큐에 제출하는 방식이 기본 이행이다.
기존 행·세션·예약은 수정하지 않는다. 동일 논리 작업을 양쪽 큐에 재제출하는 것은 이행 방식이 아니다.
기존 진행 작업의 자동 수입은 제공하지 않으며, 원래 정책의 검사 근거를 새 정책 승인으로 바꾸지 않는다.
백업은 worker가 유휴 상태일 때 SQLite backup API를 사용해 DB/graph checkpoint/hand-offs/logs를 함께 보관한다.

새 flow의 정책 변경은 위 명시적 명령만 허용한다. 저장소·기존 실행이 정지된 상태여야 하고,
이전 전체 기록, 변경 이유, 누적 예산을 보존한다. 기존 예약을 해제하고 정책에 묶인 검증/승인을 다시 요구한다.
작업 자체의 acceptance, check 명령, 원격 CI 조건, 계정 공유 한도는 이 명령으로 바꿀 수 없다.
rollback도 새 이행 기록을 만들고 현재 누적 사용량은 유지한다. 이전 DB를 덮어써 실행 횟수를 되돌리지 않는다.
배포 되돌리기는 새 timer를 중지하고 실행 중 transient unit/저장소 guard를 먼저 확인한 뒤 release를 되돌린다.
기존 quota timer와 큐는 계속 유지한다. 불확실한 guard를 자동 삭제하는 복구 명령은 제공하지 않는다.

호스트에서 모의 예약 재개를 검증하려면 새 디렉터리에 `scripts/verify_flow_timer.py init`을 실행하고,
별도 일회성 systemd timer가 같은 스크립트의 `tick`을 호출하게 한다. `assert`는 명시 세션 재개와 총 2회 호출을 확인한다.
이 도구는 기존 디렉터리 교체를 거부하고 `fixture/flow-proof` 저장소·fixture 큐만 다룬다.

실제 서버 결과와 남은 제약은 [검증 기록](flow-validation-2026-09-14.md)을 참고한다.
