# PR #22 · F1·F2 보완

## 참조와 범위

- 기준 문서: PR #23의 [최신 목록](https://github.com/HyungwonPark/ai-company/blob/c802be905656a9421b1c1cfe23c93266e2db1a81/docs/work-reviews/README.md)과 [fd18508 후속 검수](https://github.com/HyungwonPark/ai-company/blob/c802be905656a9421b1c1cfe23c93266e2db1a81/docs/work-reviews/ai-company-pr22-review-fd18508-2026-09-24.md). 문서 브랜치를 병합하지 않고 고정 원문을 읽었다.
- 수정 소스: `c8f10ba61551be2e75e139c9538f20b8030846b2`.
- 수정 전: `fd1850870038b1162051d5ce644b8e1a78a68628`. [기존 R1~R5 기록](pr22-review-followup-2026-09-23.md)은 당시 범위와 결과 그대로 보존한다.
- 수정 대상: 별도 작업실 시안의 JS와 브라우저 회귀. 제품 API·DB·worker·실제 모델·운영 승인·서비스·PR #10 pending은 변경하지 않는다. 운영 배포·병합은 없다.

## 수정과 재현

| 항목 | 원인 | 보완 |
| --- | --- | --- |
| F1 | 닫힌 생성 대화상자의 폼이 DOM에 남아 숨은 입력만 바꾸고 반환했다. | 열린 생성 대화상자의 입력과 이미 생성된 프로젝트의 예시 전환을 구분한다. 자유 목표 원문·프로젝트 이름을 보존하고 저장된 목표와 화면을 함께 갱신한다. |
| F2 | 결과 집계가 개발 진행과 결과 전달 후 검수를 같은 분기로 표시했다. | `checks`에서 화면 개발·검사 작성 및 두 결과 전달을 완료로 표시한다. 같은 후보의 검사·원격 CI·독립 검수·최종 검수를 남은 일로 표시하고 전달 근거를 열 수 있다. |

정상 이관 이후에는 진행 중이라는 이유만으로 차단으로 보고하지 않는다. 검수 중 한도·실패 시나리오는 완료된 선행 업무를 유지하면서 통합 검수와 다음 행동·차단 원인을 각각 한도 대기·판단 필요로 표시한다. 완료된 이전 실행과 검수 완료 실행에는 이 예외를 소급 적용하지 않는다. PM 예시 설명과 시스템 집계 구분도 유지한다.

수정 전·후 실제 JS를 같은 Node VM에서 실행했다. 닫힌 생성 폼이 존재하는 상태에서 수정 전에는 `created.example=false`였고 수정 후에는 `true`이며 원래 자유 목표가 보존됐다. 검수 단계의 두 결과 전달 및 전달물 버튼도 수정 전에는 없고 수정 후에는 나타났다. 실행 전 확정 조건·단일 실행·대상 결속·원래 pending 보존 상태 검사도 통과했다. 이는 브라우저 조작과 별개인 코드·상태 재현이다.

## 브라우저 검증

독립 검수자가 `tests/ui/task_workspace_review.cjs`의 F1·F2 회귀를 작성했다. 기존 시안·독립 조작·R1~R5 검사는 유지한다. Chrome 샌드박스를 켠 기존 Console UI 환경에서 실행하며 정적 예시 파일 이외 요청을 차단·집계한다.

수정 소스 `c8f10ba`의 [Python CI](https://github.com/HyungwonPark/ai-company/actions/runs/35884801147)와 [화면 CI](https://github.com/HyungwonPark/ai-company/actions/runs/35884801170) 전체가 성공했다. 화면 작업은 `107262099173`이며 `.github/workflows/console.yml`의 `pull_request` 실행이다. 실제 checkout은 PR 검사용 병합 커밋 `69d8f1b099eaa5b1878e2535352b6c9290357865`다. head를 직접 checkout했다고 보고하지 않는다. 같은 실행의 `workspace-preview` HTML과 공개 ZIP의 HTML이 바이트 단위로 일치한다.

| 검사 | 결과 | 확인한 내용 |
| --- | --- | --- |
| F1 | PASS · 모바일 4조합 | 순서형 320px·한눈형 390px 각각 Light/Black. 자유 목표 생성 직후 닫힌 폼이 DOM에 남은 상태에서 새로고침 없이 전환. 같은 프로젝트·원문·저장 목표·실행 0건과 이후 새로고침 보존. 열린 생성 창 내부의 기존 예시 선택은 전체 흐름 검사에서도 유지. |
| F2 | PASS · 12조합 × 5단계 | 두 배치 × Light/Black × 320/390/1440px에서 병렬·대기·이관·검수·검수 완료의 업무 ID·상태와 결과의 완료·남은 일·차단·다음 행동 대조. 조회가 실행·이벤트·결정을 바꾸지 않음. |
| F2 예외 | PASS | 검수 중 한도·실패에서는 두 선행 업무 완료를 보존하고 통합 검수 대기·판단 필요를 표시. 재생만으로 완료·후보 요청을 만들지 않음. 완료 이후 한도·실패 시나리오를 소급 적용하지 않음. |
| R1~R5 | PASS | 일반 새로고침·초기화, 이전 pending 발견, 명세 버전과 직접 확정·단일 실행, 같은 실행 전달물·대상 결속, 수정 의견·결정 후 원래 pending 보존. |
| 기존 회귀 | PASS | 기존 시안·독립 포인터/터치·키보드·포커스·200% 텍스트·오프라인 검사와 제품 그래프·관리 화면 회귀. Python 3.11/3.12 CI. |

Chrome `152.0.7977.82`, `chromiumSandbox:true`. 확장 회귀 JSON은 29개 검사 묶음 PASS, 캡처 28장, 페이지 오류·허용 밖 요청 0건이다. 구현자와 별도의 코드 검수자가 실제 JS 상태를 대조했고, 독립 시각 검수자는 아래 모바일 캡처 4장을 직접 읽었다. F1의 계획 전환·실행 0건, F2의 단계 일치·가독성을 확인했으며 추가 글자 겹침·가로 잘림은 발견하지 못했다.

- [F1 순서형 Light 320px](../previews/evidence/f1-f2-c8f10ba/task-workspace-f1-converted-journey-light-320.png) · [F1 한눈형 Black 390px](../previews/evidence/f1-f2-c8f10ba/task-workspace-f1-converted-overview-black-390.png)
- [F2 순서형 Light 390px](../previews/evidence/f1-f2-c8f10ba/task-workspace-f2-checks-journey-light-390.png) · [F2 한눈형 Black 390px](../previews/evidence/f1-f2-c8f10ba/task-workspace-f2-checks-overview-black-390.png)
- [단계별 실제 표시·상태 검사 JSON](../previews/evidence/f1-f2-c8f10ba/task-workspace-review-validation.json) · [실행 출처·캡처 해시](../previews/evidence/f1-f2-c8f10ba/provenance.json)

위 자료는 성공한 수정 소스 CI에서 생성됐으며 공개 저장소에 보존한다. 다운로드·문서 게시 커밋의 최종 CI와 공개 다운로드 확인은 [PR #22의 최신 검증 안내](https://github.com/HyungwonPark/ai-company/pull/22)에 연결한다.

## 다운로드

[로그인 없이 미리보기 ZIP 다운로드](https://raw.githubusercontent.com/HyungwonPark/ai-company/design/task-first-workspace/docs/previews/downloads/AI-Company-task-workspace-c8f10ba.zip) → 압축 해제 → `AI-Company-task-workspace.html`을 Chrome 등 브라우저로 연다. [조작 순서](../previews/task-workspace/README.md)와 [파일별 해시](../previews/downloads/task-workspace-c8f10ba.json)를 함께 보관한다.

- ZIP SHA-256: `a5e00337c3ec6aca102084a8892d5463bbfbced982c43ca1333a7b9e857c5d2d`.
- HTML SHA-256: `c14da9d7e0ea5b01268dbe304327422c658774fafec1b09b03647a535e49bf4f`.
- 원본 HTML·JS·CSS는 위 수정 소스에 고정했고 ZIP에는 외부 파일이 필요 없는 HTML 1개만 넣었다. 운영 데이터·비공개 원문·인증 정보는 포함하지 않는다.
- 이전 ZIP·해시·R1~R5 검증은 덮어쓰지 않는다. 새 검사 기록은 F1·F2의 추가 경로를 다룬다.

## 사용자 비교와 남은 확인

수정 시안은 초보 사용자의 순서형·한눈형 비교에 넘기는 후보다. 같은 목표를 사용해 현재 단계·다음 행동·기다리는 이유를 설명할 수 있는지 확인한 뒤 기본 배치를 정한다. 이번 브라우저 회귀를 실제 초보 사용성 시험으로 보고하지 않는다.

APK 실기기, 서비스워커 갱신, 제품 연결, 실제 PM·모델·원격 후보 검수, 기존 그래프 G1~G3 개선은 이번 시안 검증 범위 밖이다. 기존 미완료 확인 항목을 완료로 바꾸지 않는다. 시안 비교에서 제외할 때는 이전 ZIP을 사용하면 되며 운영 데이터 복구는 필요하지 않다.
