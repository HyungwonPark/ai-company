# UNKNOWN timeout 수동 검수 후보 Work PM 수용 기록

## 후보 식별

이번 문서는 `feat/manual-unknown-review-path`의 비운영 구현과 원격 CI를 수용 검수에 제출하기 위한 기록이다.

- 구현 후보 HEAD: `0e6962a75a44bfd23bce61ebcfffd9a627e26877`
- 구현 기준 base: `c4f97888e62328638fe672a5ff237ad9d2731cdf`
- 구현 후보 전체 패치 SHA-256: `c94c6c85f921a859ec35565f93a5a3ff0c7ca3606babc7d244e45034b970c2cd`
- 구현 후보 패키지 SHA-256: `dd7904f32ce8e9cffe59f83015f2d4a4ca077f074ea154fc6ffc910d626f2a20`
- 브랜치: `feat/manual-unknown-review-path`
- 초안 PR: [#39](https://github.com/HyungwonPark/ai-company/pull/39)

수용 기록을 추가한 최종 커밋과 그 커밋을 포함한 전체 패치·패키지 해시는 문서 커밋 후 인수인계 보고에서 다시 고정한다. 위 값은 코드 구현 후보의 고정값이며 변경하지 않는다.

## 로컬 검증

- `uv run --frozen python -m unittest discover -s tests -v`: **744개 통과, 3개 건너뜀**
- 대상·공유 장부 회귀: **23개 통과**
- `git diff --check`: 통과
- Python 구문 검사: 통과
- 건너뜀 3개는 서버 bwrap/AppArmor 프로필이 필요한 기존 실환경 격리 검사다.

확인한 경계는 다음과 같다.

- 주 프로세스·하위 작업·Workflow 완료·결과·사용량·정산·계정 보호가 모두 있어야 정상 완료
- timeout은 비용 추정·자동 정산 없이 원문과 `UNKNOWN_TIMEOUT_UNRESOLVED` 보호를 보존
- 일반 `reserve()`는 `account_unknown_timeout`/`reservation_unknown`으로 계속 차단
- 승인형 새 예약은 고정 PR·HEAD·base·전체 패치 digest와 `UNKNOWN(timeout)` 계정에만 허용
- 기존 UNKNOWN 예약과 새 예약의 사용량·종료·정산은 분리
- 동일 승인 재처리는 결정적 reservation ID로 중복 생성하지 않음
- 새 검수가 UNKNOWN/INCOMPLETE이면 순차 배치를 즉시 중단
- PR #38의 PNG를 포함한 `git diff --binary` 전체 입력을 유지

## 원격 CI

초안 PR #39의 구현 후보에 대한 원격 결과:

- [Python 3.11](https://github.com/HyungwonPark/ai-company/actions/runs/36806177994): **통과**
- [Python 3.12](https://github.com/HyungwonPark/ai-company/actions/runs/36806177994): **통과**
- [Journey UI](https://github.com/HyungwonPark/ai-company/actions/runs/36806178016): **통과**
- [Console UI / Browser](https://github.com/HyungwonPark/ai-company/actions/runs/36806177975): **통과**
- [Android APK](https://github.com/HyungwonPark/ai-company/actions/runs/36806178009): **통과**
- [Deployed console connectivity](https://github.com/HyungwonPark/ai-company/actions/runs/36806178024): **통과**
- Automation evidence: [실행](https://github.com/HyungwonPark/ai-company/actions/runs/36806177973), 조건상 **건너뜀**
- Android runtime: [실행](https://github.com/HyungwonPark/ai-company/actions/runs/36806178011), 조건상 **건너뜀**

## 운영 경계와 다음 승인

이번 단계에서는 다음을 실행하지 않았다.

- Claude 호출 및 실제 모델 판정
- 새 reservation 생성
- tick 실행
- 정산 또는 계정 상태 변경
- worker 재시작
- 운영 설치·배포·merge

기존 `5da6829c…` UNKNOWN 예약, 계정 UNKNOWN(timeout), 원본 장부·실행 기록은 변경하지 않았다. 다음 단계는 별도 승인 후 네 고정 대상(PR #35 → #30 → #37 → #38)을 순차 검수하는 것이며, 어느 한 건이라도 종료·사용량·정산 증거가 부족하면 즉시 멈추고 자동 재시도하지 않는다.

이 문서는 Work PM 수용 제출용이며 운영 적용 승인을 의미하지 않는다.
