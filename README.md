# ai-company

여러 코딩 에이전트를 일관된 조건으로 실행하는 **하네스**와, 목표를 구현·검사·검수·수정하여 완료 또는 중단까지 이끄는 **작업 루프**를 함께 만듭니다.

개발 루프는 가짜 개발자·검수자로 반려 후 수정, 한도 중단, 중복 접수와 재시작 복구를 검증합니다. 별도의 `session` 명령은 Claude Code·Codex의 실제 비대화형 프로세스를 실행하고, 사용량 제한에 걸리면 SQLite에 저장한 뒤 같은 세션을 예약 재개합니다. 실제 세션 실행 성공을 개발 작업의 검사·검수 완료로 간주하지 않습니다.

## 실행

Linux, Python 3.11 이상, uv가 필요합니다. 이 저장소의 잠긴 의존성을 설치한 뒤 실행합니다.

```bash
uv sync --frozen
uv run --frozen ai-company demo --task examples/demo-task.json
uv run --frozen python -m unittest discover -s tests -v
```

기본 데모는 한 번 반려한 뒤 수정·재검수를 수행합니다. 결과의 `status`는 `DEMO_READY`, `attempt`는 `1`입니다. 모의 커밋·검사 결과만 사용하므로 실제 병합 준비를 의미하지 않습니다. 동일 명령을 다시 실행하면 저장된 작업을 확인하고 중복 에이전트 실행 없이 결과를 반환합니다.

상태는 `.ai-company/`에 저장합니다. 별도 시나리오는 새 상태 경로를 사용합니다.

```bash
uv run --frozen ai-company demo --task examples/demo-task.json --reject-first 20 --state-dir .ai-company/limit-demo
```

이 시나리오는 최초 구현 뒤 최대 3회 수정하고 `STOPPED`로 끝나며 종료 코드 `2`를 반환합니다. 동일 작업 ID의 명세·정책·어댑터 구성을 바꾸면 접수를 거부합니다. 새 작업은 새 ID로 등록합니다.

## 구현 범위

| 구성 | 현재 동작 |
|---|---|
| 하네스 | 불변 작업 맥락, 역할 등록, 어댑터 선택, 실행별 작업 공간, 실행·조회·취소·결과 검증 |
| 루프 | LangGraph의 구현·검사·검수·수정 분기, 수정·실행 횟수·시간 한도, 최종 증적 확인 |
| 저장·복구 | SQLite 체크포인트, 별도 실행 사실 기록, 단일 조정기 잠금, 완료된 실행 재사용 |
| 검증 | 명세·정책·실행·커밋 일치 확인, 미해결 지적 유지, 누락·건너뜀·오래된 결과 거부 |

`NEEDS_RECONCILIATION`은 이전 실행의 종료·결과를 확인해야 하는 상태입니다. 자동 재실행하지 않으며, 현재 버전에는 이 상태를 수동 승인해 실제 외부 작업을 재개하는 기능이 없습니다.

작업 공간 분리는 OS 권한 격리가 아닙니다. 기존 개발 루프의 레지스트리는 계속 시뮬레이션만 허용합니다. 세션 큐의 CLI 감독·취소·오류 분류는 생성한 프로세스 fixture로, 예약 재개는 실제 사용자 systemd timer와 모의 CLI로 검증합니다. 실제 계정의 모델 호출·인증·사용량과 원격 Git·필수 CI의 개발 루프 통합은 별도 검증 항목입니다.

## 사용량 제한 자동 대기·재개

`session submit`은 작업을 영속 큐에 등록하고, `session worker`는 실행 시각이 된 작업 하나를 처리한 뒤 종료합니다. `WAITING_QUOTA` 시간은 기본 300초의 **개별 CLI 실행 제한시간에 포함하지 않습니다**. reset 시각이 없으면 제한된 지수 백오프를 적용합니다.

Codex는 저장된 식별자를 사용해 `codex exec resume <session_id>`로 재개합니다. 인증·권한·승인·테스트·코드 오류는 자동 재시도하지 않습니다. 컨텍스트 부족은 `NEEDS_CONTEXT_HANDOFF`와 체크포인트로 분리하고, `session handoff`로 새 세션을 예약합니다.

[설계·명령·타이머 설치와 운영 방법](docs/quota-resume.md)을 참고하세요. 실제 모델 호출이 가능한 명령은 `session worker`이며, `demo`와 테스트는 모의 실행입니다.

## 이어서 작업하기

- **후속 구현 목표:** [역할별 에이전트 자동 이관과 작업 흐름 유지](docs/agent-failover.md). 사용량 제한 시 적격 대체자에게 우선 이관하고, 대체자가 없을 때 예약 대기합니다. Astra Ultra의 PM·최종 검수 역할과 일반 프로그램 배정기를 분리하는 요구사항이며, 현재 구현 완료를 뜻하지 않습니다.
- [하네스·루프 및 서버 연결 계획](docs/plans/2026-09-13-bootstrap-and-v0.1.md)
- [구현 구조와 복구 경계](docs/harness-foundation.md)
- [서버 세션 인수인계](docs/server-handoff.md)
- [사용량 제한 대기·재개와 컨텍스트 인수인계](docs/quota-resume.md)

최종 수정일: 2026-09-13, v0.1 기반 구현

## 역할 이관과 개발·검수 후속 초안

`ai-company flow`는 공유 계정 한도를 고려한 배정, quota 이관/예약 복구, 실제 CLI 결과와 개발·검수 그래프를 연결한다.
운영 방법은 [flow dispatcher](docs/flow-dispatcher.md), 모의·실제 호출의 검증 범위와 차단 요인은 [서버 검증 기록](docs/flow-validation-2026-09-14.md)을 참고한다.
기존 `session` 큐는 그대로 유지하며, 새 flow는 검증된 모델 설정과 현재 후보의 원격 CI 증적 없이는 실제 병합 준비를 승인하지 않는다.


## 역할별 병렬 운영과 관리 콘솔 후속 초안

[통합 목표·작업 분담](docs/plans/company-control-plane.md)을 기준으로 PR #5의 복구·CI 출처
검증 위에 역할별 관리 기반을 확장합니다. 별도 Git clone의 독립 작업은
`ai-company flow worker --state-dir <새 상태 경로> --parallel 2`로 한 번 배정합니다.
같은 Git common dir의 worktree는 기존 안전 잠금으로 직렬화됩니다.

[관리 API](docs/management-api.md)는 같은 SQLite의 프로젝트·역할·대화·하네스·승인 기록을
제공합니다. 콘솔 UI는 후속 PR에서 연결합니다. 실제 PM 응답·하네스의 자동 실행 연결,
실제 개발 완주와 공개 도메인/TWA는 아직 완료되지 않았습니다.
[격리 복구안](docs/runtime-remediation-plan.md)과 [도메인·TWA 준비](docs/domain-twa-plan.md)를
확인하세요. 기존 운영 서비스·timer·queue는 변경하지 않습니다.
