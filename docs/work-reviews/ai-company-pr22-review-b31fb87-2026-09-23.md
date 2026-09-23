# PR #22 검수 — b31fb87

기준일: 2026-09-23 · 버전: 1.0

## 판정

**비교 시안 제출과 연결된 CI 성공은 확인했다. 초보자 핵심 흐름은 보완이 필요하다. 현재 결과를 제품 연결·운영 적용의 합격으로 처리하지 않는다.**

‘순서형’ 최종 선택이나 운영 승인으로 간주하지 않는다. 이번 검수에서는 직접 렌더링·클릭·터치·APK 조작을 수행하지 않았으므로 시각적 우열을 확정하지 않는다.

## 확인 범위

- [PR #22](https://github.com/HyungwonPark/ai-company/pull/22): open, draft, merged=false.
- 검수 대상: `b31fb876a79b2548de908e295fc376bb5e451554`, 기준 브랜치 커밋 `f901f7861a0ff08c4fdaab6444211a1d84a20c9a`.
- 변경 9개 파일: 별도 시안 HTML·JS·CSS, 문서, 패키징, UI 검사, CI 연결. 변경 목록에 제품 API·DB·worker 소스는 없다. 실제 운영 서버를 이번 검수에서 점검한 것은 아니다.
- [Python CI](https://github.com/HyungwonPark/ai-company/actions/runs/35862598830)와 [화면 CI](https://github.com/HyungwonPark/ai-company/actions/runs/35862598798)의 성공을 GitHub 메타데이터로 조회했다.
- 화면 CI 작업 `107186190980`에서 두 신규 시안 검사 단계의 success와 작업 로그의 PASS 기록을 확인했다. 검사는 GitHub PR 검사용 병합 커밋을 checkout한 실행이며 연결된 PR head는 위 검수 대상과 일치한다.
- 예시 미리보기 및 캡처 산출물이 해당 실행에 연결되고 만료되지 않은 것을 확인했다. 캡처 ZIP을 내려받아 직접 눈으로 확인한 것은 아니다.
- 소스와 기존 테스트를 독립적으로 검토하고, 아래 R1은 원본 JS의 초기화 부분을 Node VM에서 재현했다.

## R1. 예시 초기화가 입력을 지우지 못함 — 확인된 결함

우선순위: P2. 제품 운영 데이터 문제가 아닌 격리 시안의 입력 초기화 결함이다.

근거: [preview.js 9행](https://github.com/HyungwonPark/ai-company/blob/b31fb876a79b2548de908e295fc376bb5e451554/docs/previews/task-workspace/preview.js#L9), [115행](https://github.com/HyungwonPark/ai-company/blob/b31fb876a79b2548de908e295fc376bb5e451554/docs/previews/task-workspace/preview.js#L115), [127행](https://github.com/HyungwonPark/ai-company/blob/b31fb876a79b2548de908e295fc376bb5e451554/docs/previews/task-workspace/preview.js#L127).

저장값이 없는 첫 진입에서 얕은 객체 복사로 `state.draft`와 `initial.draft`가 공유된다. 입력 이벤트가 기본값까지 변경한다. 초기화는 오염된 기본값을 복제하므로 입력이 남는다.

원본 초기화 코드를 사용한 재현 결과:

```json
{
  "sharedDraft": true,
  "afterReset": {
    "name": "초기화 검수",
    "goal": "빈 입력으로 되돌아가야 합니다",
    "reply": "검수 답변"
  },
  "resetCleared": false
}
```

이는 JS 상태 재현이며 브라우저에서 버튼을 누른 결과로 보고하지 않는다.

수정·수용 기준: 기본 상태를 매번 독립적으로 생성한다. 저장값 없는 새 탭에서 이름·목표·답변 입력 → 예시 초기화 → 새 프로젝트를 열면 세 입력과 확정 기록이 빈 기본 상태여야 한다. 초기화하지 않고 새로고침할 때의 입력 보존은 그대로 유지한다.

## R2. 승인 시안에 결정 종류가 없음 — 흐름 검수 공백

우선순위: P1. 실제 운영 승인 취약점이 아니라 초보자가 결정을 수행하는 시나리오의 미완성이다.

근거: [preview.js 106~107행](https://github.com/HyungwonPark/ai-company/blob/b31fb876a79b2548de908e295fc376bb5e451554/docs/previews/task-workspace/preview.js#L106).

이전 실행 → 승인 → 후보 검토에는 근거 확인 체크와 ‘예시 선택 기록’만 있다. 저장값에는 실행·승인 ID·digest·preview_only만 있고 사용자가 어떤 판단을 했는지 나타내는 값이 없다.

수정·수용 기준: 실제 제품 정책에서 허용하는 ‘수용 / 수정 요청 / 보류’ 등의 선택을 예시 상태로 구별한다. 각 선택 후 다음 행동이 어떻게 달라지는지 보여 준다. 운영 승인이나 원래 요청의 pending을 변경하지 않고, 별도의 예시 선택값에만 기록한다. 수정 요청에 의견이 필요한지도 확인한다.

## R3. 이전 실행의 미처리 승인이 기본 화면에서 드러나지 않음 — 발견 가능성 문제

우선순위: P1. 실행별 승인 원본은 정확하게 분리돼 있으나, 찾는 동선이 부족하다.

근거: [preview.js 46~50행](https://github.com/HyungwonPark/ai-company/blob/b31fb876a79b2548de908e295fc376bb5e451554/docs/previews/task-workspace/preview.js#L46), [67~68행](https://github.com/HyungwonPark/ai-company/blob/b31fb876a79b2548de908e295fc376bb5e451554/docs/previews/task-workspace/preview.js#L67).

기본 진입은 현재 2차 작업이다. 이 상태에서는 ‘현재 실행의 요청 없음’, ‘요청이 없어요’라고 표시한다. 하지만 이전 1차 작업에는 pending 후보가 1건 남아 있다. 프로젝트 목록이나 기본 요약에는 그 요청을 발견할 표시가 없다. 사용자가 실행 선택기를 이미 알아야 찾을 수 있다.

수정·수용 기준: 프로젝트 목록·승인 진입점에 ‘확인할 일 1건’을 표시하고 해당 실행의 요청으로 바로 이동한다. 여러 실행의 요청을 목록에서 모아 보여 주되, 각 요청의 프로젝트·계획·실행·후보·digest 결속은 유지한다. 목록 통합을 승인 원본 혼합으로 구현하지 않는다.

## R4. 새 프로젝트에서 한 사이클을 체험할 수 없음 — 명시된 시안 한계

우선순위: P1. 실제 모델을 호출해야 한다는 요구가 아니다.

근거: [preview.js 53~58행](https://github.com/HyungwonPark/ai-company/blob/b31fb876a79b2548de908e295fc376bb5e451554/docs/previews/task-workspace/preview.js#L53), [65행](https://github.com/HyungwonPark/ai-company/blob/b31fb876a79b2548de908e295fc376bb5e451554/docs/previews/task-workspace/preview.js#L65), [99행](https://github.com/HyungwonPark/ai-company/blob/b31fb876a79b2548de908e295fc376bb5e451554/docs/previews/task-workspace/preview.js#L99).

새 목표·답변은 저장되지만 계획은 고정된 로그인 예시다. 확정 후에는 ‘시작 대기’에서 멈춘다. 진행·이관·검수·승인을 보려면 다른 샘플 프로젝트와 과거 실행으로 이동해야 한다. PR과 명세는 이 한계를 올바르게 표시하고 있다. 다만 현재 검사로 확인한 범위는 ‘새 프로젝트 계획 확정’과 ‘기존 기록 탐색’ 두 조각이다.

수정·수용 기준: 하나의 고정 예시 목표를 선택해 PM 대화 → 계획 확인·수정 → 확정 → 병렬 진행·대기·이관 → 검수 결과 → 승인 또는 수정 요청을 같은 프로젝트의 연속된 이야기로 체험하게 한다. 모의 진행 버튼·단계 재생은 검수용임을 명시하고 자동으로 실제 모델을 호출하지 않는다. 임의 목표에 대응하지 못한다면 예시 목표 선택으로 범위를 명확히 한다.

## R5. 주 버튼이 여전히 내부 처리 순서를 노출함 — UX 보완

우선순위: P2. 백엔드 사건 분리를 없애자는 제안이 아니다.

근거: [preview.js 40행](https://github.com/HyungwonPark/ai-company/blob/b31fb876a79b2548de908e295fc376bb5e451554/docs/previews/task-workspace/preview.js#L40), [57~58행](https://github.com/HyungwonPark/ai-company/blob/b31fb876a79b2548de908e295fc376bb5e451554/docs/previews/task-workspace/preview.js#L57).

사용자는 PM 제안을 본 뒤 ‘명세 저장 → 새 계획 요청 → 계획 검토’를 수행해야 한다. 제안을 봤는데 왜 다시 계획을 요청하는지, 명세와 계획이 어떻게 다른지 이해해야 한다.

수정·수용 기준: 사용자 행동은 ‘내용 정리 → 계획 확인 → 이 계획으로 시작’으로 설명한다. 명세 저장·버전 고정·계획 생성은 내부에서 각각 기록하고, 저장이나 계획 요청만으로 개발을 시작하지 않는다. 계획 준비 중·실패·다시 검토가 필요한 상태도 보여 준다. 범위·완료 조건·한도는 확정 전에 읽을 수 있어야 한다.

## 보존할 좋은 점

- 업무·팀·기록을 나누고 업무명과 모델명을 분리했다.
- 담당 이관에서 같은 업무 ID를 유지하고 현재·이전 실행의 담당을 구별한다.
- 명세 저장·계획 생성·직접 확정은 서로 다른 사건으로 유지한다.
- 모의 결과·관측 미확인·운영 미연결을 표시한다.
- 문서가 Work 원문을 직접 읽었다고 잘못 주장하지 않고 대화 기반 후속안이라는 출처를 밝힌다.
- CI 성공과 초보 사용자 이해도·실기기 검증을 구별한다.

## 그래프와 시각 검수의 남은 범위

이번 업무 흐름은 고정된 3개 업무와 CSS 배치·연결 표시다. 기존 그래프의 복잡한 선·드래그·확대 문제를 해결했다는 증거로 사용하지 않는다. 그 기능을 재사용할지, 새 표현으로 대체할지와 대체 검증은 제품 연결 단계에서 결정한다.

순서형·한눈형을 다시 처음부터 만들 필요는 없다. R1~R5를 공통 상태·흐름에 반영한 뒤 같은 과제로 비교한다. 기존 CI 회귀와 실제 브라우저 조작을 유지하되, 초보 사용자 관찰은 별도로 한다.

## 서버 전달 지시

> PR #22의 구조 비교는 유지하고 이 검수의 R1~R5를 보완해 주세요. 특히 새 탭의 초기화 결함, 이전 실행의 미처리 승인 발견, 의미 있는 예시 결정 선택, 같은 프로젝트의 전체 체험 경로를 먼저 해결해 주세요. 명세 저장·계획 생성·명시적 실행 확정의 내부 분리는 유지하면서 사용자 버튼과 안내는 단순화해 주세요. 예시 데이터와 별도 시안 범위에서 수정하고 실제 모델·운영 승인·DB·worker·배포·병합·PR #10 상태는 변경하지 마세요. 동일 기준의 화면·실제 조작 결과와 미실시 항목을 구분해 제출해 주세요.

[최종 수정일: 2026-09-23, v1.0]