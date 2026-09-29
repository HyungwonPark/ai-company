# PR #22 재검수 — 4ffd4e7

기준일: 2026-09-24 KST · 버전: 1.0

## 판정

**F1·F2는 이번 격리 시안 범위에서 해결된 것으로 판정합니다. 초보 사용자 비교에 넘길 수 있습니다.** 이번 두 수정과 직접 관련된 추가 차단점은 발견하지 못했습니다. 전체 제품·기존 그래프·운영 적용·APK의 합격 판정은 아닙니다.

- 대상: [PR #22](https://github.com/HyungwonPark/ai-company/pull/22), 게시 커밋 `4ffd4e702ea2f125569c3f410328a57d3976fc84`.
- 수정 소스: `c8f10ba61551be2e75e139c9538f20b8030846b2`.
- 이전 기준: [fd18508 후속 검수](ai-company-pr22-review-fd18508-2026-09-24.md).
- 원래 [R1~R5 검수](ai-company-pr22-review-b31fb87-2026-09-23.md)는 당시 근거와 함께 보존합니다.

## 확인 결과

| 항목 | 판정 | 확인 근거 |
| --- | --- | --- |
| F1 자유 목표 → 예시 전환 | 해결 | 열린 생성 대화상자와 닫힌 폼을 구분합니다. 생성 직후 새로고침 없이 전환되며 원래 목표·프로젝트명·동일 프로젝트를 보존합니다. 확정 전 실행 0건과 새로고침 후 선택 보존을 독립 상태 재현으로 확인했습니다. |
| F2 검수 중 결과 집계 | 해결 | 정상 검수에서 선행 두 업무 완료·결과 전달·남은 통합 검수·차단 없음이 일치합니다. 한도·실패에서는 완료된 선행 업무를 보존하고 검수의 대기·판단 필요를 결과와 다음 행동에 반영합니다. |
| 예외·기록 보존 | 확인 | 검수 중 한도·실패를 모의 재생만으로 완료시키지 않으며 실행·이벤트가 유지됩니다. 완료 뒤 예외 설정을 바꿔도 완료 기록과 후보 요청 1건을 보존합니다. |
| 기존 회귀 | 원격 성공 확인 | 기존 시안·독립 조작·R1~R5와 확장 F1·F2 검사 단계가 최종 CI에서 성공했습니다. 전체 회귀를 이번 Work 환경에서 다시 실행한 것은 아닙니다. |

구현 근거: [preview.js](https://github.com/HyungwonPark/ai-company/blob/4ffd4e702ea2f125569c3f410328a57d3976fc84/docs/previews/task-workspace/preview.js), [확장 브라우저 검사](https://github.com/HyungwonPark/ai-company/blob/4ffd4e702ea2f125569c3f410328a57d3976fc84/tests/ui/task_workspace_review.cjs).

이전·현재의 실제 JS를 같은 Node VM과 최소 DOM 대역에서 실행했습니다. 이전 F1·F2를 재현한 뒤 현재 코드에서 위 조건이 통과하는 것을 확인했습니다. 별도 검수자가 작성한 상태 검사도 재실행해 종료 코드 0과 전후 결과를 확인했습니다. 이는 브라우저 클릭·터치 재실행과 구별합니다.

## 원격 CI와 재실행 이력

- 최종 [Python CI](https://github.com/HyungwonPark/ai-company/actions/runs/35886024982): head `4ffd4e7`, 첫 시도 성공.
- 최종 [화면 CI](https://github.com/HyungwonPark/ai-company/actions/runs/35886024906): 같은 head, 두 번째 시도 전체 성공.
- 화면 CI의 첫 작업 `107266256309`은 기존 `tests/ui/collaboration.cjs:83`의 요소 캡처에서 `Element is not attached to the DOM`으로 실패했습니다. F1·F2 검사 단계는 이 첫 시도에도 성공했습니다.
- 두 번째 작업 `107270005046`의 전체 성공을 확인했습니다. 두 작업 모두 PR 검사용 병합 커밋 `0a2f8114390e815a7f0386fa699a211ebcb403d3`을 사용했습니다.
- 두 시도 모두 확장 회귀 로그는 `PASS, checks=29, screenshots=28`입니다. 29개는 검사 묶음 수이며 모든 사용자 조작의 개수를 뜻하지 않습니다.
- 재실행 성공은 F1·F2와 전체 회귀의 이번 성공 근거입니다. 기존 요소 캡처 경합을 수정했다는 증거로 쓰지 않습니다.

[서버의 검증 기록](https://github.com/HyungwonPark/ai-company/blob/4ffd4e702ea2f125569c3f410328a57d3976fc84/docs/product-experience/pr22-f1-f2-validation-2026-09-24.md)도 실패 이력과 검사 범위를 구별해 기록합니다. 캡처 경합은 기존 검사 안정성의 후속 항목으로 남기며, 이번 시안 비교를 다시 막는 조건으로 확대하지 않습니다.

## 미리보기·대표 캡처 확인

GitHub에 게시된 ZIP과 메타데이터를 읽어 다음을 대조했습니다.

- ZIP SHA-256: `a5e00337c3ec6aca102084a8892d5463bbfbced982c43ca1333a7b9e857c5d2d`.
- 내부 HTML은 1개이며 SHA-256: `c14da9d7e0ea5b01268dbe304327422c658774fafec1b09b03647a535e49bf4f`.
- 원본 HTML·JS·CSS의 해시가 [게시 기록](https://github.com/HyungwonPark/ai-company/blob/4ffd4e702ea2f125569c3f410328a57d3976fc84/docs/previews/downloads/task-workspace-c8f10ba.json)과 일치합니다. 같은 패키징 방식으로 재구성한 HTML도 ZIP 내부 HTML과 바이트 단위로 같습니다.
- [확장 검사 JSON](https://github.com/HyungwonPark/ai-company/blob/4ffd4e702ea2f125569c3f410328a57d3976fc84/docs/previews/evidence/f1-f2-c8f10ba/task-workspace-review-validation.json)의 해시는 [출처 기록](https://github.com/HyungwonPark/ai-company/blob/4ffd4e702ea2f125569c3f410328a57d3976fc84/docs/previews/evidence/f1-f2-c8f10ba/provenance.json)과 일치합니다. PASS·29개 묶음·28장·오류 및 금지 요청 0건을 읽었습니다.
- 자료의 출처는 수정 소스 `c8f10ba`의 [성공한 화면 CI](https://github.com/HyungwonPark/ai-company/actions/runs/35884801170)입니다. 이후 게시 커밋은 이 시안 소스를 바꾸지 않았습니다.

이번에는 저장소에 보존된 다음 두 캡처를 직접 읽었습니다.

1. [F1 순서형 Light 320px](https://github.com/HyungwonPark/ai-company/blob/4ffd4e702ea2f125569c3f410328a57d3976fc84/docs/previews/evidence/f1-f2-c8f10ba/task-workspace-f1-converted-journey-light-320.png): 기존 프로젝트 이름 아래 로그인 예시 계획과 실행 0건이 표시됩니다.
2. [F2 한눈형 Black 390px](https://github.com/HyungwonPark/ai-company/blob/4ffd4e702ea2f125569c3f410328a57d3976fc84/docs/previews/evidence/f1-f2-c8f10ba/task-workspace-f2-checks-overview-black-390.png): 두 결과 전달, 남은 검수, 차단 없음, 다음 행동이 같은 검수 단계를 설명합니다.

두 캡처에서 주요 글자 겹침이나 화면 밖 가로 잘림은 발견하지 못했습니다. 다른 상태·폭 전체를 눈으로 확인했다거나, 정지 이미지로 포커스·터치·화면 갱신을 검증했다는 의미는 아닙니다. 두 캡처는 서로 다른 단계이므로 배치의 우열을 비교하는 근거로 사용하지 않습니다.

## 다음 단계: 같은 과제로 사용자 비교

이제 기능 결함 수정과 사용자 이해도 평가를 구별합니다. 이번 F1·F2만으로 추가 구현을 계속 늘리지 않고, 기존 시안의 순서형·한눈형을 같은 내용과 단계에서 비교합니다.

| 과제 | 사용자가 설명하거나 찾아야 할 것 |
| --- | --- |
| 목표와 계획 | 무엇을 만들고 어디까지 하는지, 어느 버튼이 실행을 시작하는지 |
| 진행과 대기 | 진행 중인 일과 기다리는 일, 기다리는 이유 |
| 담당 변경 | 모델이 바뀌어도 같은 업무를 이어가는지 |
| 결과 | 끝난 일과 남은 일, 지금 할 수 있는 행동 |
| 결정 | 내가 확인할 요청, 수정 요청·수용·보류의 차이 |

운영과 연결되지 않은 [최신 미리보기 ZIP](https://raw.githubusercontent.com/HyungwonPark/ai-company/4ffd4e702ea2f125569c3f410328a57d3976fc84/docs/previews/downloads/AI-Company-task-workspace-c8f10ba.zip)에서 진행합니다. 같은 로그인 예시를 두 배치로 보고, 필요하면 시안 초기화로 입력 상태를 맞춥니다. 초기화는 해당 시안 탭의 예시 입력·선택만 지우므로 보관할 비교 메모는 먼저 적습니다.

관찰 기록에는 배치·화면 폭·막힌 위치·도움을 받은 내용·사용자의 설명을 남깁니다. 화면 상태는 검수용 재생으로 준비할 수 있지만 버튼 위치나 답을 먼저 알려 주지 않습니다. 처음 쓴 배치의 학습 효과를 기록하고, 한 사람의 선호를 전체 사용자의 사용성으로 일반화하지 않습니다.

초보 사용자가 아직 참여하지 않았다면 미실시로 유지합니다. 마스터의 직접 조작 평가는 우선 기록할 수 있지만 그것만으로 초보자 검증을 대신하지 않습니다. 기본 배치의 최종 선택은 실제 비교 후에 합니다.

## 서버 전달 지시와 남은 범위

> PR #23의 4ffd4e7 재검수를 참조해 F1·F2를 해결 상태로 기록해 주세요. 현재 후보를 사용자 비교 기준으로 유지하고, 위 동일 과제의 순서형·한눈형 관찰 결과를 정리해 주세요. 실제 관찰 전에는 자동 테스트나 AI의 평가를 초보 사용자 결과로 채우지 마세요. 비교에서 확인한 문제와 기본 배치 선택을 바탕으로 제품 연결·그래프 G1~G3 처리 범위를 다음 계획으로 작성해 주세요. 이번 문서만으로 구현 범위·운영 승인·배포·병합·PR #10 pending을 변경하지 마세요.

남은 항목은 초보 사용자 비교·기본 배치 선택, 제품 연결 계획, 기존 그래프 G1~G3, APK 실기기·서비스워커 확인입니다. 모델 연결과 실제 운영 루프의 기존 미완료 범위도 이번 시안 검수로 완료 처리하지 않습니다.

[최종 수정일: 2026-09-24, v1.0]
