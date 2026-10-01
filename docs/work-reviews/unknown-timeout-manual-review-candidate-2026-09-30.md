# UNKNOWN timeout 수동 검수 경로 후보

이 문서는 `fix/unknown-timeout-policy`의 `c4f9788` 위에 만든 비운영
후보의 적용 경계와 복구 절차를 고정한다. 실제 Claude 호출, 새 예약,
tick, 정산, 계정 변경, worker 재시작, 운영 설치·배포·merge는 이 후보에서
수행하지 않았다.

## 구현 범위

- `review_protocol.py`는 주 프로세스, 모든 하위 작업, Workflow 완료 이벤트,
  결과·사용량 저장, 예약 정산과 계정 보호를 별도 증거로 검사한다. 한 항목이
  없으면 PASS/정상 완료를 만들지 않는다.
- 30분 timeout은 비용 추정이나 자동 정산 없이 `review_timeout` 원문을
  예약 결과에 원자적으로 보관하고 `UNKNOWN_TIMEOUT_UNRESOLVED` 보호를
  유지한다.
- `manual_unknown_review.py`와 `SharedCallLedger.reserve_approved_unknown`은
  일반 `reserve()`와 분리된 승인형 준비 경로다. 승인에는 PR, HEAD, base,
  전체 패치 SHA-256, 기존 UNKNOWN 예약 ID, 이유, 승인자, 시각이 필요하다.
  결정적 새 reservation ID만 허용하며 `force=true` 우회는 없다.
- 기존 UNKNOWN 예약·계정은 변경하지 않고, 새 예약의 사용량·종료·정산은
  별도 행과 이벤트로 기록한다. 새 검수가 UNKNOWN이면 순차 배치를 즉시
  중단한다.
- `review_pr_with_claude.py`는 `git diff --binary`의 PNG 패치 봉투를
  전체 입력으로 유지한다. 바이너리 경로를 제외해 전체 검수로 표시하지
  않는다.

검수 대상 순서는 다음과 같다.

1. PR #35 `1d54d3a`
2. PR #30 `7af4fad`
3. PR #37 `e659fc6`
4. PR #38 `c2abc62`

각 대상의 base와 전체 패치 digest는 실제 호출 직전 원격 PR과 Git 객체에서
다시 계산하고 승인 기록에 넣는다. 하나라도 종료·사용량·정산 증거가 부족하면
뒤의 대상은 예약하지 않는다.

## 적용안 (운영 승인 전)

1. 후보 커밋과 패키지 해시를 대조한다.
2. 비공개 임시 장부에서 `5da6829c…`가 `UNKNOWN`이고 계정이
   `UNKNOWN(timeout)`인지 읽기 전용으로 확인한다. 상태를 `AVAILABLE`로
   바꾸거나 기존 정산을 소급하지 않는다.
3. 네 대상 각각에 대해 승인 기록의 PR·HEAD·base·패치 SHA-256을 원격
   상태와 비교한다. 승인 기록이 없으면 일반 `reserve()`가 차단되는지 먼저
   확인하고 종료한다.
4. 별도 운영 승인 이후에만 `reserve_approved_unknown`으로 결정적 새 예약을
   만들 수 있다. 이 문서의 후보 검수 단계에서는 호출하지 않는다.

## 실패와 되돌리기

- 설치 전: 후보 파일을 별도 디렉터리에 검증하고 기존 설치·장부를 읽기만
  한다. 불일치면 설치하지 않는다.
- 설치 중 검증 실패: 후보 파일만 삭제하고, 백업한 설치 파일과 전환 해시를
  복원한다. DB·원본 증거·UNKNOWN 행은 복원 대상으로 삼지 않는다.
- 새 예약 이후 실패: 두 worker를 중지하고 새 예약을 UNKNOWN으로 보호한다.
  과거 DB 백업으로 덮어쓰지 않고 현 장부와 원문을 보존한다.
- 실제 호출 전에는 Claude 계정·모델·30분 제한·중복 실행 여부를 별도로
  대조한다. 실제 호출이 없는 후보 단계에서는 모두 미검증으로 남긴다.

## 후보 검증 기록

- 새 회귀: 종료 프로토콜, timeout 원자 기록, 일반 예약 차단, 승인된 새
  예약의 구·신 정산 분리, 동일 승인 idempotency, 순차 배치 중단, PNG 패치
  입력 경계를 검사한다.
- 기존 회귀: shared calls, Claude runner, quota checkpoint, Workflow 완료
  재확인을 함께 실행한다.
- 실제 모델 판단과 마스터 조작은 이 후보에서 수행하지 않았다.

커밋·base·전체 패치 SHA-256·CI 결과는 커밋 후 이 문서의 인수인계 기록과
함께 고정한다.
