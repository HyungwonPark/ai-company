# PR #25 R25 후속 검증

2026-09-24 KST. 비운영 구현·검수·패키징 기록이다. 운영 배포·병합·PR #10 결정 변경·서명키 작업은 수행하지 않았다.

## 기준과 고정 후보

사용자가 지정한 [PR #23 검수 원문](https://github.com/HyungwonPark/ai-company/blob/eb2e6a862dc85af1ea3da229697e0e6f1ebc45fe/docs/work-reviews/ai-company-pr25-review-dbd868b-2026-09-24.md)을 읽고 R25-1·R25-2와 관련 경계를 수정했다. PR #24 `7522748` 위의 PR #25 변경은 보존했다. [기존 A~E 기록](journey-integration-validation-2026-09-24.md)은 당시 증거로 남기며 이번 보완 전의 무차단 판정을 재사용하지 않는다.

| 항목 | 고정값 |
| --- | --- |
| PR | [초안 #25](https://github.com/HyungwonPark/ai-company/pull/25) |
| 제품·최종 검사 | `14114a71584c65c6dbbd0b33a1a26c7246d1e1ad` |
| 설치 이미지 | `sha256:8005ce1fa85a458d17bdf159a0af2774801a9a19b4c14adecff147b1f059ae01` — arm64 로컬 Docker ID, registry pull 주소 아님 |
| 설치 소스 | 113개 파일 일치, manifest SHA-256 `be595339189b847266015696461c933ca4ca23dbbcf162e12aeff603024bf26b` |
| 미리보기 ZIP SHA-256 | `4d3916a4564b99a7dd9d1fe40f9664501cc1e058a72c4d2abd22ac1bd9e52812` |
| 미리보기 HTML SHA-256 | `606e68539fead877274cd16173f62311614dde87bcfb5413664fb9f7141a4417` |

[후보·CI·artifact 메타데이터](../evidence/journey-r25/candidate.json), [개별 증적과 해시](../evidence/journey-r25/README.md), [적용·복구안](journey-integration-rollout-2026-09-24.md), [전체 목표 대응표](journey-overall-goal-map-2026-09-24.md)를 함께 본다. 문서·패키지 게시 커밋은 제품 커밋 이후이며 제품/검사 코드 변경 없이 기록만 추가한다.

## 최초 실패 → 수정 후 성공

| 항목 | 실패 근거 | 보완과 최종 검사 |
| --- | --- | --- |
| R25-1 | 원본 SW의 Node VM 재현 실패. 검사만 추가한 `e49ad40`의 [실제 Chrome 실패](https://github.com/HyungwonPark/ai-company/actions/runs/35931537172): v8 두 탭 제어·v9 waiting에서 새 app을 캐시한 후 offline 시 `/journey-ui.js` 부팅 실패 | 새 정적 모듈 의존성을 제거해 기존 v8 shell의 `app.js`에 순수 여정 함수를 포함. 새 report named export에도 의존하지 않음. 최종 Chrome에서 v8 active 두 탭/v10 waiting을 실제 client 수로 대조하고 부분·전체 report 갱신 후 offline 부팅 성공. 전체 탭 종료 후 v10 활성화, 역방향 v10 active/v8 waiting과 복구 활성화 성공 |
| R25-2 | 원제품 실제 JS는 완료 0/진행·대기 4, rejected는 미확인. 위 최초 Chrome은 기대 `확인 필요` 대신 `대기`로 실패 | 완료·진행·대기·확인 필요·미확인을 명시 분류. MERGE_READY 완료, 대조·인수인계·NO_ELIGIBLE_AGENT·SUPERSEDED 확인 필요, rejected 반려, 미등록 상태는 미확인. 같은 실행의 조치 필요 역할을 run waiting/running보다 우선 |
| 같은 원인: 종합 보고 | 4f81525의 current_plan 실제 report renderer가 미완료 전체를 진행으로 표시해 독립 회귀 실패 | 같은 분류를 종합 보고에 사용. 조치/미확인 다음 행동을 보완하고 시스템 원문 안내는 상세에 보존. 원래 report·digest·입력은 변경하지 않음 |
| 같은 원인: PM | nested NEEDS_RECONCILIATION을 helper는 읽어도 실제 소비 화면에서 일반 PM 응답 대기로 처리 | 소비 목록 보완, 실제 manager JS 회귀 통과 |
| 같은 원인: 선택기 | c7 후보의 실제 격리 화면에서 본문은 확인 필요지만 선택기는 예약 없는 waiting을 `예약 대기`로 단정. 독립 실제 scopeName 실행도 실패 | 공통 waiting 라벨을 `대기`로 수정. 예약이 없는 operator/unknown/superseded 선택기 문구를 실제 Chrome에서 검사하고 관측값 기록 |

초기 [Node SW](../evidence/journey-r25/initial-node-sw.json)·[Node 상태](../evidence/journey-r25/initial-node-status.json), [Chrome SW](../evidence/journey-r25/initial-browser-sw.json)·[Chrome 상태](../evidence/journey-r25/initial-browser-status.json)를 보존했다. 독립 검수자가 추가 변형을 찾고 정상 기대 회귀를 작성했으며 제품 파일은 작성하지 않았다.

### 중간 검사 실패도 보존

`1b80092`의 [Journey 35932498537](https://github.com/HyungwonPark/ai-company/actions/runs/35932498537)은 SW 부분·전체 offline 부팅은 성공했으나 구버전 로그인 비밀번호가 offline 전환 뒤 유지된다는 추가 기대에서 실패했다. 고정 v8 코드는 offline 이벤트에서 인증 전 DOM을 재생성해 비밀번호를 비운다. 로그인 암호는 업무 draft가 아니다.

검사를 삭제하거나 제품 보호를 완화하지 않았다. 최종 검사는 **업데이트 waiting 설치만으로 입력을 지우거나 전송하지 않음**과 **기존 v8 offline 전환은 비밀번호를 비우며 POST 0임**을 분리한다. [중간 실패 원문](../evidence/journey-r25/intermediate-input-expectation-failure.json)을 보존한다. 실제 PM 업무 초안·통신 불확실 저장 검증은 별도 D 쓰기 검사에 남긴다. 모든 상황에서 모든 입력이 유지된다고 주장하지 않는다.

## 동일 최종 후보의 검증

| 검사 | 결과·범위 |
| --- | --- |
| [Python CI](https://github.com/HyungwonPark/ai-company/actions/runs/35933619222) | Python 3.11·3.12 각각 회귀 467개, 서버 전용 격리 3개 skip 구분. Android 준비 4개 통과는 APK 실기기 검증 아님 |
| [Console UI](https://github.com/HyungwonPark/ai-company/actions/runs/35933619242) | 기존 R1~R5/F1~F2, 그래프 경로·1px 드래그·키보드·갱신, 인증 UI·API·고정 그림 검증 통과 |
| [Journey UI](https://github.com/HyungwonPark/ai-company/actions/runs/35933619143) | C 읽기·G1~G3·D 실제 임시 API 쓰기·preview·SW·상태 분류 모두 통과 |
| 상태 | 실제 JS 26사례+current_plan 4사례. 임시 SQLite 실제 API와 Chrome에서 8상태×Light/Black×320/390/1440=48조합. 최신 current_plan도 같은 분류·다음 행동 대조 |
| 원장 보존 | 상태·읽기·그래프 탐색에서 임시 업무 테이블 29개 전후 일치. 시험 로그인 변경은 별도 인증 테이블이며 비교 제외 |
| SW | Chrome sandbox 활성, HTTP cache 비활성, 실제 controller/waiting client 수, app/report 응답 SHA를 확인. 부분 갱신에서 옛 report·새 app 조합도 통과. private API·POST cache/replay 없음 |
| 이미지 | 네트워크 없음·읽기 전용 root·capability 제거·no-new-privileges·UID 65532·임시 tmpfs에서 실행. 설치 113파일과 실제 HTTP 자원 일치, no-store·익명 API401·다른 Host403 |
| 독립 검수 | [독립 기록](../evidence/journey-r25/independent-review.md). 제품 작성과 분리한 코드·재현·최종 JSON·직접 PNG 열람. 검수 과정의 두 추가 결함도 수정 후 재검증 |

실제 Chrome 자동 조작이며 로컬에서 샌드박스를 끈 검사가 아니다. local host의 Chrome 격리 제약은 우회하지 않고 GitHub CI의 격리 Chrome을 사용했다. 실행 상태·PM 응답·후보는 임시 합성 사실이며 모델을 호출하지 않았다. Automation evidence job의 skipped는 실제 모델 PASS로 해석하지 않는다.

## 화면과 다운로드

- [미리보기 ZIP](../previews/journey/AI-Company-journey-preview.zip), [열기·검사 안내](../previews/journey/README.md), [해시 목록](../previews/journey/SHA256SUMS).
- 로그인 없이 다운로드: [공개 ZIP 원본](https://raw.githubusercontent.com/HyungwonPark/ai-company/feat/journey-product-integration/docs/previews/journey/AI-Company-journey-preview.zip). 브랜치 링크는 후속 변경될 수 있으므로 위 고정 SHA와 대조한다.
- [320 Light 조치 필요](../evidence/journey-r25/screens/journey-status-light-320-completed-operator.png), [390 Black 미확인](../evidence/journey-r25/screens/journey-status-black-390-unknown.png), [390 Black 동일 실행 종합](../evidence/journey-r25/screens/journey-status-black-390-superseded.png).
- [최초 부팅 실패](../evidence/journey-r25/screens/initial-journey-service-worker-failure.png) → [부분 갱신 offline 성공](../evidence/journey-r25/screens/journey-sw-upgrade-partial-offline.png) → [복구 offline 성공](../evidence/journey-r25/screens/journey-sw-rollback-offline.png).

ZIP HTML과 manifest는 최종 CI artifact 그대로다. 합성 merge checkout과 PR head가 달라도 manifest 원본 해시 전부를 제품 커밋과 대조했다. preview는 읽기용 고정 응답이므로 SW나 실제 쓰기 성공의 증거로 대신하지 않는다.

## 완료 범위와 남은 확인

이번 R25 수정·관련 변형·회귀·격리 Chrome·독립 검수·최종 CI·패키지·이미지·적용/복구안 준비까지 완료했다. 운영에는 적용하지 않았다. 서버·worker·DB·카탈로그·모델·예산·재시도·기존 승인 계약은 변경하지 않았다. 기존 운영 큐·커플 서비스·PR #10 pending·서명키를 조작하지 않았다.

후속 운영 변경은 [적용안](journey-integration-rollout-2026-09-24.md)의 웹 1개 교체 범위로 별도 승인 후에만 한다. 실제 APK·hyungwon.cloud 서비스워커 갱신, 사용자의 새 목표/계획 확정과 실제 모델 전체 흐름, 서명키 외부 보관·복원은 미검증/사용자 확인으로 유지한다. 현재의 실제 계정·모델 상태를 다시 관측했다는 의미가 아니다.
