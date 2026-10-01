# 수동 UNKNOWN 검수 패키지 보완 후보

## 범위

PR #39의 운영 연결 결함을 비운영으로 보완한다. 기존 설치본·장부·Claude
`--continue` 세션·worker는 변경하지 않는다. 이 후보에서도 설치·예약·tick·정산·
계정 변경·Claude 호출·worker 재시작을 수행하지 않는다.

## 변경 내용

- `review_pr_with_claude.py`에 명시적 운영자 승인 파일을 받는 경로를 추가했다.
- 승인 경로는 PR·HEAD·base·전체 패치 SHA-256·기존 UNKNOWN 예약 ID가 모두 일치할
  때만 `reserve_approved_unknown()`을 호출한다.
- 승인 인자가 없으면 기존 `reserve()`를 사용하며 UNKNOWN 차단은 유지한다.
- `UNKNOWN(timeout)`만 승인형 경로에 허용하고 `provider_error`와 일반 UNKNOWN은
  계속 거부한다.
- 수동 예약에는 기존 시도와 별도 결정적 ID를 사용하고 timeout 시 새 행도 UNKNOWN으로
  보존한다.
- `package_claude_reviewer.py`가 `runner.py`, `claude_control.py`, `shared_calls.py`,
  `tick.py`, 설치 `manifest.json`을 분리된 운영 패키지로 생성한다. 소스 문서
  아카이브와 운영 설치 패키지는 서로 다른 산출물이다.
- `report_unknown_reservation.py`는 SQLite를 `mode=ro`로 열어 종료·사용량·정산
  증거를 읽기만 한다.

## 추가 Codex UNKNOWN 읽기 전용 조사

2026-10-01 현재 `f3102693…` 예약은 `UNKNOWN`이며 시작 시각과 executor identity만
있습니다. 종료 시각·결과·정산 이벤트가 없고, 연결 계정도 `UNKNOWN`입니다. 따라서
증거 기반 정산이 불가능하며 호스트 슬롯을 해제하지 않습니다. quota 정산이나 계정
해제를 적용하지 않았습니다. 원문 JSON은 서버 비공개 경로에 보존합니다.

## 검증 경계

- 승인 없음 → 일반 예약 경로 차단
- 정확한 승인 + 고정 대상 → 승인형 예약 경로
- `provider_error`·일반 UNKNOWN → 승인형 경로 차단
- 기존 UNKNOWN 행 보존, 중복 ID·중복 정산 방지
- timeout → 새 예약 UNKNOWN 및 배치 중단
- 4파일 패키지 매니페스트와 각 파일 SHA-256 검증
- 실제 Claude 호출·운영 설치·실제 예약은 미실행
