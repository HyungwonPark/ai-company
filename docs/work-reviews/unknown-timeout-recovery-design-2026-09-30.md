# UNKNOWN(timeout) 복구 설계와 비운영 구현안

## 범위

PR #35 예약 `5da6829c…`의 현재 상태는 `UNKNOWN`이며 연결 계정은
`UNKNOWN(timeout)`이다. 주 프로세스가 목록에서 사라졌다는 사실만으로 종료·하위
작업·비용을 확정할 수 없으므로, 이번 변경은 호출·예약·정산·계정 변경 없이
복구 정책과 회귀 검사만 준비한다.

## 상태 계약

기존 SQLite의 `accounts.state`와 `reservations.state`는 계속 `UNKNOWN`으로
보존한다. 새 상태는 장부 값을 덮어쓰는 새 DB 상태가 아니라, 읽은 사실에서
파생하는 비공개 정책 상태다.

| 파생 상태 | reason code | 의미 | 새 호출 |
| --- | --- | --- | --- |
| `UNKNOWN_TIMEOUT_UNRESOLVED` | `account_unknown_timeout` | timeout으로 표시됐지만 주·하위 종료 증거가 없음 | 차단 |
| `UNKNOWN_RESERVATION_UNRESOLVED` | `reservation_unknown` | 같은 공유 그룹에 미정산 `UNKNOWN` 예약이 있음 | 차단 |
| `UNKNOWN_ACCOUNT_UNRESOLVED` | `account_unknown` | 원인이 확인되지 않은 계정 UNKNOWN | 차단 |
| `ABANDONED_UNKNOWN` | `operator_abandoned_unknown` | 운영자가 명시적으로 포기 처리한 UNKNOWN | 차단 |

`ABANDONED_UNKNOWN`은 시간 경과나 quota 회복으로 자동 생성하지 않는다. 모든
상태는 종료 증거와 별도 정산 검토가 끝날 때까지 새 모델 호출을 허용하지
않는다. 따라서 `UNKNOWN(quota)`의 `reconcile_settled_quota()`를 timeout 경로에서
재사용하지 않는다.

## 구현

- `src/ai_company/timeout_unknown.py`의 순수 분류 함수가 파생 상태와
  `allows_new_calls=False`를 반환한다.
- `SharedCallLedger.reserve()`는 이 분류를 호출해 기존 DB를 변경하지 않고
  `CapacityUnavailable.reason_code`와 `recovery_state`를 함께 반환한다.
- PR 검수 runner의 사전 거부 `facts.jsonl`에는 위 두 필드를 기록한다. 이는
  사용량·종료·정산 사실로 해석하지 않는다.
- 기존 quota 정산·재개 함수와 SQLite 스키마는 변경하지 않는다.

## 복구 순서

1. 현재 예약·계정·facts/events/checkpoint 계보와 프로세스·Workflow 종료 증거를
   읽기 전용으로 대조한다.
2. 주·하위 종료와 비용이 모두 확인되면 기존 예약에 대해 한 번만 증거 기반
   정산한다. 비용을 추정하거나 UNKNOWN을 quota 상태로 바꾸지 않는다.
3. 정산 이벤트와 장부가 일치한 뒤에만 동일 고정 대상의 수동 재개를 준비한다.
   어느 단계에서든 UNKNOWN·미정산·종료 미확인이 남으면 즉시 중단한다.
4. `ABANDONED_UNKNOWN`을 운영 정책으로 도입할 경우 별도 승인·감사 이벤트와
   명시적 정산 절차를 추가한다. 이번 구현에는 그 쓰기 경로가 없다.

## 적용·복구안

이번 후보는 저장소와 격리 테스트에만 존재하며 운영 설치·worker·timer·장부를
변경하지 않는다. 적용 시에는 `timeout_unknown.py`, `shared_calls.py`, runner를
백업하고 파일 해시를 대조한 뒤 설치한다. 설치 검증 실패 시 파일만 백업본으로
복구하고 DB·원본 증거는 되돌리지 않는다. 운영 적용 전 실제 Claude 호출 승인이
별도로 필요하다.

## 검증 기준

- timeout UNKNOWN 계정은 `account_unknown_timeout`과
  `UNKNOWN_TIMEOUT_UNRESOLVED`로 차단되고 새 reservation row가 생기지 않는다.
- 계정이 AVAILABLE이어도 기존 UNKNOWN 예약은 `reservation_unknown`과
  `UNKNOWN_RESERVATION_UNRESOLVED`로 차단된다.
- 명시적 `ABANDONED_UNKNOWN`도 `allows_new_calls=False`다.
- 사전 예약 거부 facts에는 reason code만 남고 calls·runtime·cost·settlement는
  증가하지 않는다.
- 기존 quota 정산·재개 회귀는 그대로 통과해야 한다.
