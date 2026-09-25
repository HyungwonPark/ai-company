# 순서형 작업실 사용성 검증 — PR #26

2026-09-25. [고정 PM 실행 지시](https://github.com/HyungwonPark/ai-company/blob/82fbe8ff69fab67cc26ff38806634e4efd590b51/docs/work-reviews/journey-usability-pm-execution-2026-09-25.md)를 구현·검수·패키징했다. Work PM의 수용 판정과 운영 적용은 별도다.

## 기준과 후보

- 후속 초안 [PR #26](https://github.com/HyungwonPark/ai-company/pull/26), base `feat/journey-product-integration`.
- 시작은 PR #25 게시 `dec455e63458ef98ac34d8e423463f7d5e30fdad`, 제품 `ffb62c37e4822530f38f15f4b0f64781cdf40bc6`. 원격 시작점 일치를 확인하고 별도 worktree·브랜치를 만들었다. PR #25를 병합하거나 수정하지 않았다.
- 최종 제품·검사 `6e39dfc0b8ede91c235f681a9b18c2708138ec48`. 이후 증적 게시 커밋은 제품/검사/의존성을 바꾸지 않는다. [후보·CI 출처](../evidence/journey-usability/candidate.json).
- 기존 순서형·R25-1/R25-1b/R25-2·실행/승인 계약을 유지한다. 제품 차이는 웹4파일이며 Python·DB·worker·Android·설정·lock 변경이 없다.

## U1~U3 대응

| 항목 | 고친 불편 | 검증·남은 제한 |
| --- | --- | --- |
| U1 승인·상태 | 같은 렌더의 관측 시각으로 목록·상세·건수·결정 버튼을 투영한다. 결정할 요청과 조회된 요청, 프로젝트 전체와 선택 실행을 구별한다. 만료 pending의 승인/반려/수정 요청을 모두 막고 열린 확인창도 기한 경과 시 비활성화한다. 알려진 한국어와 미확인 원문을 구별한다. | 순수함수31개 기한/상태·불변 경계와 실제 인증 API의 같은 실행·다른 실행·빈 요청·브라우저 시계 만료/갱신 검사를 수행했다. 서버 승인 권한과 원문/digest는 그대로다. 프로젝트 목록의 서버 응답 건수는 요청별 기한이 없는 별도 자료이므로 ‘목록 응답’으로 명시하고 현재 상세 건수라고 주장하지 않는다. |
| U2 가독성·조작 | 중요 상태·행동14px, 핵심본문16px. 진행/완료/대기/확인 필요의 글자·색·테두리를 구분했다. 의미 없는 빈 점을 제거하고 머리글 간격을 줄였다. 본문 전체 외곽선 대신 작은 키보드 초점을 표시하며 건너뛰기가 실행 주소를 바꾸지 않게 했다. 갱신 문구는 성공/모든 입력 보존을 약속하지 않는다. | Light/Black×320/390/1440, 긴 이름·빈 상태·대기·미확인·반려·만료, 실제 Tab·본문 이동과 Chrome 200% 확대를 검사했다. 선언색 대비와 PNG 검수는 접근성 인증이나 실기기 결과가 아니다. |
| U3 진행·결과 | 통합 진행 제목·실행 선택기를 하나로 줄이고 독립 그래프 선택기는 유지했다. 결과 맨 위에 결론·남은 일·다음 행동을 표시한다. 0건은 집계에 남기고 역할 상세·시스템 근거/결정·원문은 펼쳐 읽는다. 시스템 요약과 실제 PM 보고를 구분한다. 내부 조회 순서는 원문 상세에 보존한다. | 정확한 snapshot/계획 해시/실행 링크·뒤로가기·카메라/도움말/선택·G1~G3 회귀를 유지했다. 과거 실행 PM 보고와 다른 실행 참조7경계를 검사한다. 실제 통합 검사 차단 근거를 접힌 집계에서 누락하지 않게 수정했다. 원문 보고의 영문 상태·문장은 번역해 덮어쓰지 않았다. |

[설계·파일 소유권](journey-usability-plan-2026-09-25.md). 통합 담당은 JS, 화면 담당은 CSS, 독립 검수 담당은 별도 회귀와 실제 화면 증적을 맡았다. `ai-company-frontend`, `ponytail`, `variant-analysis`, `web-design-guidelines`, `code-review-skill`, 실제 CI 실패의 `gh-fix-ci`를 영향 범위에 적용했다. 스킬 적용 사실을 조작 검수 결과로 대신하지 않는다.

## 실패 → 수정

1. 독립 코드 대조에서 한국어 실행기 상태가 공통 badge fallback에 의해 미확인으로 바뀌고, 과거 실행의 PM 문서가 현재 계획 집계 부재 때문에 누락될 수 있음을 발견했다. 한국어 표시를 보존하고 PM 문서·snapshot의 프로젝트/계획/해시/실행 참조를 대조했다. 관련 독립 함수 회귀가 성공했다.
2. `NEW_RUNNING_STATE`를 미확인이라 쓰면서 진행색을 붙이던 실제 함수 검사와 compact 집계에서 통합 검사 차단 근거를 잃던 검사가 실패했다. 등록된 상태에만 의미색을 붙이고 차단 근거·집계 원문을 복구했다. [실패 자료](../evidence/journey-usability/README.md)와 최종 회귀를 구분한다.
3. 첫 [Journey36086411384](https://github.com/HyungwonPark/ai-company/actions/runs/36086411384)의 기존 상태 검사는 새 중첩 details에서 summary3개를 선택해 strict 오류가 났다. 이후 [Journey36086615574](https://github.com/HyungwonPark/ai-company/actions/runs/36086615574)는 보존된 열린 상세를 다시 클릭해 닫아 timeout이 났다. 검사 대상을 좁히고 닫혀 있을 때만 열도록 수정했다. 상태 분류·판정 기준을 완화하지 않았다.
4. 초기 실제 키보드 캡처에서 본문 건너뛰기가 프로젝트 목록으로 이동하는 문제를 확인했고 선택한 URL을 유지하도록 수정했다. Black320 캡처에서 ‘만/료’ 줄바꿈도 확인해 배지 축소를 막았다. 최종 동일 조건 캡처에서 진행 화면/실행 주소 유지와 ‘만료’ 한 줄을 확인한다.

## 최종 검사

| 검사 | 결과·출처 |
| --- | --- |
| Python3.11/3.12 | [36086876177](https://github.com/HyungwonPark/ai-company/actions/runs/36086876177) PASS. 각467개 실행, 서버전용3 skip. Android 준비4개씩은 실기기와 구분 |
| Journey UI | [36086876188](https://github.com/HyungwonPark/ai-company/actions/runs/36086876188) PASS. 신규사용성·인증 혼합모듈4조합·상태48조합·범위/응답경합·그래프/쓰기·미리보기·SW갱신/복구 |
| Console UI | [36086876022](https://github.com/HyungwonPark/ai-company/actions/runs/36086876022) PASS. 기존 인증·그래프1px드래그·상호작용·고정 그림 포함 |
| 로컬 | 실제 제품 함수의 상태·범위·응답경합·혼합조합·명세저장·manager·graph 및 신규 만료/원문/출처 경계 PASS |
| 이미지 | `sha256:a580921eb1b5b3cf2ee8639d454f3ace8835dc478c24618a38be1fd82b78400c`, arm64. 설치113파일·읽기전용/network none·UID65532·HTTP/no-store·익명401/Host403 PASS |
| 독립 검수 | [검수 기록](../evidence/journey-usability/independent-review.md)에 최종 코드·실제 브라우저 조작 증적·PNG·해시 대조를 별도로 기록 |

Chrome `153.0.8010.52`의 sandbox를 켰다. 신규 검사는 인증된 실제 임시 API에서 흐름8개, 전후36비교, PNG54개를 남겼다. 승인 결정은 제출하지 않았으며 업무29테이블 불변을 확인했다. 만료 전→정확 경계/직후→갱신은 **브라우저의 시계만 모의 이동**했고 저장 기한은 그대로다. 기존 독립 쓰기 검사는 별도 임시 API에서 계획/결정의 보호 경계를 검증한다. 모델·운영 worker·마스터 조작은 수행하지 않았다. Automation evidence skipped를 실제 모델 PASS로 세지 않는다.

## 같은 자료의 전후 비교

원본 fixture SHA-256 `e84cad2748e99067635252184e8e04b3615b5f5eec175de87ad00bde95810d8c`를 보존했다. 같은 실행·테마·폭·details 닫힘 상태다. 아래는 미리보기 안내 띠를 포함한 결과 화면 높이이며 Light와 Black 값이 같다. 높이는 관찰 지표이며 내용을 지우는 목표치로 사용하지 않았다.

| 폭 | 변경 전 | 변경 후 |
| --- | --- | --- |
| 320px | 4813px | 2882px |
| 390px | 4731px | 2803px |
| 1440px | 4047px | 2360px |

[Light390 변경 전](../evidence/journey-usability/screens/usability-before-light-390-reports.png) · [변경 후](../evidence/journey-usability/screens/usability-after-light-390-reports.png) · [Black320 만료](../evidence/journey-usability/screens/usability-after-black-320-approvals.png) · [키보드](../evidence/journey-usability/screens/usability-keyboard.png). [전체 비교 측정·파일 목록](../evidence/journey-usability/journey-usability-validation.json)은 제품 머리글의 본문 시작 좌표와 별도 미리보기 안내 띠 높이를 구분한다.

## 패키지

[로그인 없는 ZIP](../previews/journey-usability/AI-Company-journey-preview.zip) · [열기/검사 안내](../previews/journey-usability/README.md).

- ZIP SHA-256 `de7a7616d3537d8fb868ea4cc8fb42bc2deb8f52c0a61bb3204b50209339118c`
- HTML SHA-256 `3c9ec98f8b632802aa371efca61e87e4c80de39d45ad3b9d294be74829e15029`
- 같은 최종 CI가 검사한 HTML·manifest bytes 그대로. 원본28파일의 SHA를 제품과 대조했다. 합성 merge checkout `b882151bbc8a21b8b03a023c99810bfab3ac09ac`은 제품 head와 구별한다.
- 이미지·ZIP은 이번 후보로 새로 만들었다. 이전 PR #25의 ZIP과 이미지 ID를 재사용하지 않았다.

## 인계와 경계

[웹1개 적용/복구안](journey-usability-rollout-2026-09-25.md)에 고정 후보·백업·60초 정상 상태·실패 조건·DB를 되돌리지 않는 복구를 명시했다. **현재 운영 미적용**이다. 기존 커플 서비스·worker·큐·타이머·원문·사용량·권한·PR #10 pending·서명키를 변경하지 않았고 병합하지 않았다.

이번 산출물은 Work PM의 U1~U3 수용 검수용이다. 실제 초보 사용자 관찰·APK/공개 도메인 갱신·마스터 직접 목표/확정·서명키 외부 복원은 미확인으로 유지한다. 이를 새 개발 미완료로 섞거나 완료로 소급 표시하지 않는다.
