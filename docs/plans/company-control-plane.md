# 통합 목표 후속 실행 계획

후속 자동 연결은 [`352a31a` 보존 자동 PM 초안](../automatic-pm-flow.md)에서 진행한다.
아래 내용은 관리 기반 단계의 범위 기록이며, 자동 연결의 새 상태·실제 검증과 구분한다.

2026-09-14. 요구사항 원본: [통합 목표](ai-company-unified-goal-2026-09-14.md).
원본은 제품 요구사항과 완료 기준으로 보존한다. 첨부 문서 자체를 운영 배포·자동 병합·
기존 서비스 변경 승인으로 해석하지 않는다.

## 기준과 작업 공간

PR #5의 현재 기준은 `45ea96962790ab46f1f25116b5edf251adb7a7f9`이다.
PR #5를 되돌리거나 운영 checkout을 바꾸지 않고 별도
`feat/company-control-plane` 작업 트리에서 후속 변경을 만든다.
PR #5 → 관리 기반 → 콘솔 UI 순서의 초안 PR로 검토한다.

통합 목표는 hyungwon.cloud에서 마스터와 Astra Ultra PM이 목표·역할·하네스를 정하고,
역할별 에이전트가 독립 작업을 병렬 실행하며 보고·실행 승인을 별도 화면으로 제공하고
Android TWA까지 연결하는 것이다. 이 변경은 그 목표의 관리 기반이며 실제 완주나 배포가 아니다.

## 소유권과 의존 관계

| 작업 | 소유 파일 | 이번 단계 |
|---|---|---|
| A 실행 격리·설정 근거 | docs/runtime-remediation-plan.md | 동일 worker 최소 재현, 전용 프로필 적용·되돌리기 계획 |
| B 실행 병렬화 | dispatcher.py, sessions.py, storage.py, cli.py, test_parallel_dispatcher.py | 실행 중 전역 배정 잠금 해제, 작업 소유권·중단 복구 유지 |
| B 관리 API | management.py, management_server.py, test_management*.py | 기존 SQLite의 프로젝트·역할·하네스·대화·승인 및 상태 API |
| C 콘솔 | src/ai_company/web/**, tests/ui/** | 매니저·역할 진행·보고·승인·프로젝트 화면, 실제 HTTP 연결 |
| D 도메인·TWA | docs/domain-twa-plan.md, deploy/examples/** | 기존 커플 서비스 조사, 분리 구성·서명·연결 검증안 |
| 통합·독립 검수 | 이 계획, 검증 기록, 초안 PR | 회귀·동시성·API/UI 검사 후 검수 결과와 미검증 항목 기록 |

B/C는 API 규격 합의 후 병렬 구현한다. A의 모델 실행 차단은 B/C/D의 문서·코드·
로컬 fixture 검증을 멈추지 않는다. 실제 모델 통합은 A의 격리·설정 증거가 준비되어야 한다.

## 실행 규칙

- 프로젝트 역할은 화면 개발·서버 개발·검증·운영 등 동적인 책임 단위다.
  기존 `pm/developer/check/reviewer/final/gate`는 각 산출물의 검증 단계이며 역할 큐와 다르다.
- 역할 task link는 이미 Dispatcher에 제출된 `flow_task_id`를 참조한다.
  별도의 실행 큐·모델 엔진을 만들지 않는다. 당시 활성 하네스 버전을 link에 저장한다.
- 각 flow worker는 짧은 1회 배정만 수행한다. `flow worker --parallel 2`는 최대 두 독립
  worker를 호출하고 종료한다. 기본은 1이며 state 디렉터리 전체의 실행 슬롯은 2개다.
  운영 timer 설치·변경은 이 CLI 실행에 포함되지 않는다.
- 장시간 CLI·검사·CI 조회 동안 전역 scheduler 잠금을 풀고 task 소유권을 유지한다.
  모델 실행과 로컬 검사는 저장소 잠금도 유지한다. 완료 전 잠금을 다시 얻어 결과를 반영한다.
- **현재 동시 실행 작업은 별도 Git clone을 사용한다.** 같은 `git_common_dir`를 공유하는
  worktree는 PR #5의 cross-queue 실행 guard를 유지하므로 직렬화된다.
  worktree별 잠금으로 단순 치환해 Git 공유 상태 보호를 약화하지 않는다.
- `process=None`인 실행 시작 직전에도 저장소 잠금을 보유하면 살아 있는 worker다.
  복구는 저장소 잠금을 얻은 뒤 RUNNING 사실을 확인하며 불확실한 실행은 재생하지 않는다.
- 다음 배정 전에 모든 저장된 WAITING_QUOTA/WAITING_RETRY 결과를 먼저 반영한다.
  진행 중 이미 시작한 다른 작업은 강제로 중단하지 않으며 이후 실행은 공유 한도를 지킨다.
- 실행 가능한 후보가 없는 대기는 영속화한다. 기존 정책상 구성 자체가 부적격인
  NO_ELIGIBLE_AGENT는 운영자 정책 수정이 필요하며 한도 대기와 구분한다.
- 관리 HTTP 요청은 모델을 호출하지 않는다. PM 메시지는 `awaiting_pm`으로 저장된다.
  실제 PM worker 소비·응답 연결은 아직 없으며 시스템 응답을 Astra의 말로 꾸미지 않는다.
- 초기 하네스/변경 초안은 실행 FlowSpec 정책을 자동 변경하지 않는다. 정책 변경은
  기존 명시적 migration 및 증거 재검증 규칙을 따른다.
- 승인 저장은 실행이 아니다. 대상 digest·산출물 SHA·환경·비용·기한·영향·되돌리기·
  검증 조건을 재확인하는 API 계약을 제공하며 배포 실행 endpoint는 추가하지 않는다.

## 남은 제품 연결

1. bwrap 전용 조치와 허용/비허용 쓰기·네트워크·cgroup 종료·실제 worker 검증은 완료했다.
   [실제 실행 기록](../runtime-validation-2026-09-14.md)에 감독하의 작은 개발/검사/독립/Astra 검수와 남은 범위를 구분한다.
2. 실제 Astra Ultra PM 및 최종 검수, 개발/검수 후보의 모델·effort·Ultracode·Workflow
   증거 수집. 지원 목록과 실제 적용·동적 동작은 별도로 판정한다.
3. 저장된 PM 요청을 기존 flow와 연결하는 소비기, 실제 PM 응답 및 승인된 역할/하네스
   변경 반영. 정책을 임의 완화하거나 API에 직접 모델 호출을 추가하지 않는다.
4. 실제 task 제출·역할 link·작업 공간 제공을 마스터의 합의된 하네스에서 연결하고,
   실제 개발→검사→독립 검수→Astra Ultra 최종 검수를 같은 후보의 승인 CI와 대조한다.
5. 명시적 운영 연결 절차 이후 hyungwon.cloud DNS/TLS·로그인 및 Android
   패키지/서명·Digital Asset Links·설치·로그인 유지·승인 화면을 실제 기기에서 검증한다.

샌드박스 오류, 적용 설정 미확인, 브라우저 실행 환경 부족, 도메인/서명/기기 미검증을
화면이나 fixture 성공으로 대체하지 않는다. 기존 커플 서비스·timer·queue는 유지한다.
