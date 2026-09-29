# PR #31 운영 연결 전 검수와 수정 지시

2026-09-26 KST · v1.0 · Work PM

## 판정

검수 대상은 [PR #31](https://github.com/HyungwonPark/ai-company/pull/31)의 `653017689585478413ff3634e297a93fa0a086ae`다. **현재 후보의 운영 연결 승인은 권하지 않는다.** 공용 호출 관리의 방향과 적용 범위는 타당하지만, 운영 경로의 두 결함을 먼저 수정해야 한다. 실제 평가 실행기 두 건도 같은 비운영 수정 묶음에서 해결한다.

| 구분 | 현재 판정 |
| --- | --- |
| PR #28·#29 기존 비운영 수용 | 유지. 이전 R28·R29 해결을 다시 여는 검수가 아님 |
| PR #31 운영 연결 | R31-1·R31-2 수정과 재검수 전 보류 |
| 실제 Astra E1–E6 평가 | 위 조건과 R31-3·R31-4 해결, 운영 공용 장부 참여 확인, 계정 제한 회복 후 진행 |
| 현재 허용할 후속 작업 | 서버에서 비운영 최소 수정·모의 재현·회귀·검수·고정 적용/복구안 갱신 |
| 미포함 | 운영 교체·병합·PR #10 pending 변경·서명키·마스터 대리 확정 |

이 문서는 PM의 검수·후속 개발 지시다. 사용자의 운영 적용 승인으로 해석하지 않는다. GitHub 게시만으로 서버 작업이 시작됐다고 보고하지 않는다.

## 확인한 근거와 한계

- 정확한 HEAD의 [Python CI](https://github.com/HyungwonPark/ai-company/actions/runs/36192064286), [Journey CI](https://github.com/HyungwonPark/ai-company/actions/runs/36192064172), [Console CI](https://github.com/HyungwonPark/ai-company/actions/runs/36192064200)는 모두 attempt 1, success다.
- Work에서 변경된 23개 파일의 고정 소스를 읽고 역할별 독립 검수를 병행했다.
- Work의 읽기용 사본에서 `tests.test_claude_pr_review`와 `tests.test_service_worker` **25개를 직접 통과**했다.
- 넓은 관련 검사 묶음은 Work 환경의 `langgraph` 의존성 부재로 4개 모듈을 불러오지 못했다. 전체 회귀를 Work에서 재현했다고 주장하지 않으며 이 환경 오류를 제품 결함으로 세지 않는다. 서버 전체 회귀 결과와 원격 CI를 구별한다.
- 아래 진단은 모델 호출 0건, 운영 변경 0건이다. R31-1은 실제 검수기 main·실제 SQLite 장부에 모의 CLI 결과를 넣었다. R31-2는 실제 장부 상태 전이를 재현하고 Dispatcher의 호출 경로를 대조했다. R31-3·4는 고정 소스에서 원본 run_case 함수를 추출하여 worker·시간을 모의했다. 전체 Dispatcher/Automation 통합 재현 또는 실제 모델 품질 평가와 동일시하지 않는다.
- 적용안의 기존 카탈로그 유지, 과거 사용량·제한 대조, 대조 실패 시 중단, 운영 DB를 과거 백업으로 덮어쓰지 않는 조건은 타당하다. 번역·두 서비스 연결에서 이번 범위의 추가 확정 결함은 찾지 못했다. 서버 설치 상태와 비공개 운영 기록은 직접 점검하지 않았다.

## R31-1 — 복구 시각이 없는 제한 응답이 계정을 다시 사용 가능하게 함

**P2 · 운영 연결 전 필수 수정**

[review_pr_with_claude.py](https://github.com/HyungwonPark/ai-company/blob/653017689585478413ff3634e297a93fa0a086ae/scripts/review_pr_with_claude.py)의 554–568행: 제한 거부 사실과 reset 시각 추출을 하나의 판단으로 사용한다. `rate_limit_event.status=rejected`인데 `resetsAt`이 없으면 reset은 None이다. 자식 종료는 확인되었으므로 `blocked`로 정산하고, 실제 장부는 같은 계정을 AVAILABLE로 변경한다. 다른 큐가 그 계정을 바로 예약할 수 있다.

직접 재현:
```text
reset_present=True  -> COOLDOWN
reset_present=False -> AVAILABLE
other_queue_can_start=RESERVED
```

수정·수용 기준:

1. 제한 거부 여부와 복구 시각의 유효성을 분리한다. 시각을 모르면 UNKNOWN/대조 대기 등으로 계정의 새 호출을 차단한다. 임의의 짧은 대기 뒤 AVAILABLE로 바꾸지 않는다.
2. 종료가 확인된 호출의 실제 사용량·원시 오류·시도 기록은 한 번만 보존·정산한다. 계정 차단과 호스트 프로세스 종료 사실을 구별한다.
3. reset 정상·누락·잘못된 형식, 같은 계정의 다른 큐, 재시작·중복 정산을 검사한다. 정상 reset 회복 후 기존 안전한 재개 경로는 유지한다.
4. 인증 오류와 제한 오류를 같은 재시도 사유로 취급하지 않으며 기존 R29 입력·범위·종료 검증을 보존한다.

재현: [pr31-reviewer-unknown-quota-6530176.py](repros/pr31-reviewer-unknown-quota-6530176.py).

## R31-2 — 미시작 예약 취소 후 같은 작업을 다시 시작하지 못함

**P2 · 운영 연결 전 필수 수정**

[dispatcher.py](https://github.com/HyungwonPark/ai-company/blob/653017689585478413ff3634e297a93fa0a086ae/src/ai_company/dispatcher.py) 824–851행은 큐가 BUSY/IDLE이고 아직 claim·guard·프로세스가 없으면 예약을 CANCELLED로 정리한다. 다음 실행은 attempt_count가 증가하지 않아 같은 예약 ID를 계산한다.

[shared_calls.py](https://github.com/HyungwonPark/ai-company/blob/653017689585478413ff3634e297a93fa0a086ae/src/ai_company/shared_calls.py) 173–177행은 기존 CANCELLED 행을 그대로 돌려준다. 이어 started()는 이를 거부한다(219–220행). 단순한 큐 잠금 경합 뒤에도 정상 작업이 시작되지 않는 경로다.

실제 장부 재현:
```text
reserve -> CANCELLED -> 같은 ID reserve가 CANCELLED 반환
started -> start has no matching live reservation
```

수정·수용 기준:

1. 취소 이력을 보존하며 새 예약 세대를 만들거나, 계정·호스트 용량을 다시 검사하는 원자적 재취득을 구현한다. CANCELLED를 무조건 RESERVED로 바꾸지 않는다.
2. 실제 큐의 session-worker 잠금 점유 → 첫 tick BUSY/미시작 취소 → 잠금 해제 → 같은 작업이 한 번 실행되는 통합 검사를 추가한다.
3. 재취득 시 다른 큐가 동일 계정 또는 호스트 2칸을 점유했으면 기다린다. 동시 재취득에서도 한 호출만 시작해야 한다.
4. 중단 복구·미확인 프로세스 유지·한 번 정산·기존 확정 실행 복구를 보존한다. 실제 프로세스가 있었던 예약을 미시작 취소로 낮추지 않는다.

재현: [pr31-cancelled-reservation-6530176.py](repros/pr31-cancelled-reservation-6530176.py). 이 파일은 장부 수준의 확정 재현이다. 위 실제 큐 통합 검사는 서버에서 추가 제출한다.

## R31-3 — 자동 수정 작업이 남았는데 평가 실행기가 종료됨

**P2 · 실제 E1–E6 평가 전 필수 수정**

[evaluate_pm_behavior.py](https://github.com/HyungwonPark/ai-company/blob/653017689585478413ff3634e297a93fa0a086ae/scripts/evaluate_pm_behavior.py) 147–153행은 요청 pending/running 또는 계획 reviewing만 계속 실행한다. 계획이 `needs_revision`, `revision_action=automatic`이고 PM 요청은 completed이면 `observed`를 반환한다.

Automation의 마지막 reconcile()은 이때 pm-revise-* 작업을 제출할 수 있으며 실제 실행에는 다음 tick이 필요하다. 실행기가 먼저 종료하면 자동 수정·재검수 흐름이 끝나지 않는다. observed가 PASS를 의미하지는 않지만, 필요한 평가 경로를 끝까지 실행하지 못한다.

원본 함수의 모의 재현:
```text
worker_ticks=1
state=observed
plan_states=[needs_revision]
automatic_repair_pending=true
```

수정·수용 기준:

1. 현재 사례의 자동 수정·재검수 작업이 남아 있으면 계속 진행한다. 과거 계획의 종결 기록 때문에 무한 대기하지 않도록 현재 계획과 연결 작업을 확인한다.
2. 마스터 결정 대기, 공급자 제한 대기, 수정 상한 도달, 완료·실패를 구분해 결과를 기록한다. 이 상태들을 일괄 observed로 닫지 않는다.
3. 실제 Automation 모의 경로로 PM 계획 → 독립 검수 REVISE → 기술 수정 → 재검수까지 검사하고, 사용자 결정이 필요한 경우 답변을 만들어 넣지 않는지도 확인한다.
4. E4 주입 자료는 평가용임을 보존하며 제품의 실제 검수/수정 경로에서 처리되는 근거를 제출한다. 문구에 지적을 붙인 사실만으로 경로 평가를 완료 처리하지 않는다.

## R31-4 — 제한 대기·중단 시간이 누적 실행 예산을 소모함

**P2 · 실제 E1–E6 평가 전 필수 수정**

같은 파일 207–214행은 최초 started-at.json 시각 + 1800을 고정 마감으로 삼는다. 136행은 현재 시각과 호출 timeout으로 예산을 판정한다. 그러므로 호출이 0건이어도 제한 대기/중단 30분 후 같은 평가를 다시 실행하면 budget_wait로 끝난다.

원본 함수의 시간 모의 재현은 누적 calls=0·repairs=0, 최초 시작 시각+1800 경과 조건에서 **worker 실행 0회, budget_wait**다. 이는 한도 우회가 아니라 안전하게 대기한 평가의 재개를 막는 문제다. 앞선 지시의 누적 1800초와도 다르다.

수정·수용 기준:

1. 평가 전체의 실제 실행시간과 미정산 실행 예약을 기준으로 남은 예산을 계산한다. 공급자 제한·예약 대기·프로그램 중단 시간은 누적 실행시간에서 제외한다.
2. 호출별 실행 제한시간과 종료 미확인 예약은 계속 유지한다. started-at 초기화나 새 trial-root로 예산을 다시 주는 방식을 해결책으로 사용하지 않는다.
3. 대기는 저장하고 실행기를 종료할 수 있게 하며, 같은 사례·입력/설정 해시·장부 기록으로 재개한다. 새로고침/재시작으로 예산이 늘어나면 안 된다.
4. 2시간 제한 대기 후 재개, 실제 누적 1800초 소진, 중단 뒤 미정산 실행, 중복 재개를 모의한다. 24회·누적 수정 2회·호스트 병렬 2개의 전체 범위도 유지한다. 새 달러·턴 제한을 추가하지 않는다.

R31-3·4 재현: [pr31-evaluation-continuation-6530176.py](repros/pr31-evaluation-continuation-6530176.py).

## 서버 작업 순서와 제출물

1. PR #31을 초안으로 유지하고 위 네 건만 최소 수정한다. R31-1·2와 R31-3·4는 파일 담당을 나누어 병행하되, 통합 담당자가 예약·시간·종료 판정을 함께 확인한다. 새 프레임워크나 UI 범위를 추가하지 않는다.
2. 각 결함에 최초 코드 실패 → 수정 코드 성공의 독립 회귀를 만든다. 아래 진단 스크립트는 현재 결함 확인용이므로 수정 후에도 같은 출력이 나와야 하는 회귀로 복제하지 않는다.
3. 기존 관련·전체 회귀와 최종 HEAD CI를 확인한다. 독립 정적 검수, 모의 실행, 실제 모델 검수의 결과를 분리한다. Claude 한도가 회복되지 않으면 그 검수만 대기로 기록하고 기록 삭제·빈 커밋·다른 큐로 우회하지 않는다.
4. 수정 후보의 전체 SHA, worker 릴리스/설치 파일과 두 unit의 해시, 카탈로그 경로·내용 보존 확인, 비공개 설정 키, 초기 장부 대조 기준, 복구 절차를 다시 고정한다. 이전 후보 6530176의 승인 요청을 새 코드의 승인으로 간주하지 않는다.
5. 비공개 운영 자료·인증 정보는 공개 문서에 넣지 않는다. 실제 운영 DB를 과거 백업으로 덮어쓰지 않는다. 새 장부에 예약/호출 기록이 생긴 뒤 구버전 worker로 무조건 되돌리지 않는다.
6. 수정·모의 검증·적용안이 끝나면 Work PM에 한 번에 제출한다. 운영 연결은 해당 수정 후보의 구체적 적용 승인을 받은 뒤 수행한다. 연결 후 모든 관리 호출자의 공용 장부 참여를 확인하고 계정 제한이 회복된 다음 실제 Astra 평가·Claude 검수를 진행한다.
7. 현재 운영 APK에 새 PM 흐름이 반영됐다고 가정하거나 마스터에게 지금 직접 확정하라고 요청하지 않는다. 실제 모델 결과를 검수한 뒤 시험 UI와 직접 조작 안내를 준비한다.

재현 실행(후보 읽기용 checkout 경로를 인자로 지정):
```bash
python docs/work-reviews/repros/pr31-reviewer-unknown-quota-6530176.py /path/to/pr31-checkout
python docs/work-reviews/repros/pr31-cancelled-reservation-6530176.py /path/to/pr31-checkout
python docs/work-reviews/repros/pr31-evaluation-continuation-6530176.py /path/to/pr31-checkout
```

기존 커플 서비스·운영 기록·번역 설정·카탈로그·PR #10 pending을 보존한다. 전체 Workflow 완료, 실제 PM 판단 품질, 마스터 직접 확정은 여전히 별도 검증이다.
