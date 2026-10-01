# PR #25 R25-1b 혼합 버전 검증

2026-09-25 KST. 운영 적용·병합 승인이 아닌 수정·검수·패키징 기록이다. [지정 검수 원문](https://github.com/HyungwonPark/ai-company/blob/ca35e931a701cdae60483d2e1b36114b1f0e2754/docs/work-reviews/ai-company-pr25-review-0bcf8d7-2026-09-24.md)을 기준으로 했다.

## 보존한 결과와 이번 변경

PR #25의 기존 순서형 연결·G1~G3·R25-2 상태 분류와 R25-1 공개 로그인 부팅 해결을 보존했다. [앞선 기록](journey-r25-validation-2026-09-24.md)의 공개 로그인 성공을 인증 후 모든 모듈 조합의 성공으로 확대하지 않는다.

새 앱과 구 그래프가 섞인 경우 인증 후 프로젝트 조회가 `getScope/selectScope` 부재로 중단되는 R25-1b를 보완했다. 제품 변경은 **app.js 한 파일**이다. 기존 그래프·보고 factory와 SW 캐시 구조를 재구현하지 않았다.

- 그래프의 실행 선택/조회 기능과 보고의 실행별 표시 기능이 모두 있을 때만 정상 작업실을 표시한다.
- 구 그래프, 구 보고, 둘 다 구버전인 조합은 한국어 **화면 갱신 필요** 안내를 표시한다. 다른 실행·현재 계획을 대신 표시하지 않으며 선택한 프로젝트/실행 URL을 보존한다.
- 업무 쓰기는 화면 버튼·submit 처리 시작과 공통 API 경계에서 차단한다. 초안·불확실 요청의 원문/멱등 키를 지우지 않는다. 인증·조회·로그아웃은 기존 계약을 유지한다.
- **화면 다시 불러오기**는 사용자의 클릭으로만 수행한다. 처리 중인 요청이나 열린 대화상자가 있으면 다시 불러오지 않는다. 자동 갱신·재전송·skipWaiting·전체 캐시 삭제·API 캐시는 추가하지 않았다.
- 구 앱+새 모듈은 기존 factory 계약을 유지한다. 이미 로드된 구 앱에 새로운 실행별 탐색이나 새 가드를 소급 적용했다고 주장하지 않는다.

## 같은 원인의 경계 대조

P2 원인은 개별 파일의 network-first/cache fallback 중 factory의 새 반환 API를 항상 있다고 가정한 것이다. `workspaceGraph.getScope/selectScope`의 모든 호출에서 시작해 같은 app이 사용하는 graph/report factory와 직접 import하는8모듈로 범위를 넓혔다. 고정 기준과 현재의 named import/export 목록은 모두 일치한다. 새 부팅 import를 다시 추가하지 않았다.

실제 추가 반환 기능은 graph의 실행 선택/조회와 report의 실행별 표시다. 기존 report fallback은 읽기 안내만 있었으므로 새 공통 미지원 안내와 업무 쓰기 경계에 함께 포함했다. 그 밖의 기존 graph ingest/capture/mount/disconnect/reset 계약은 기준에도 존재한다. 정상 기능을 가짜 no-op 메서드로 덧씌우거나 기존 실행을 임의로 선택하는 호환 처리는 하지 않았다. 역방향에서 report 분류기 인자가 없으면 기존 보수적 미확인 안내를 유지하며 새 앱의 상태 계약을 구 앱에 적용했다고 세지 않는다. [import/export 대조](../evidence/journey-r25b/module-contracts.json)를 보존한다.

## 실패와 성공을 구분한 기록

| 단계 | 관측 |
| --- | --- |
| 지정 원문 Node 재현 | 고정 v8 fetch와 구/신 factory, 실제 app 함수를 실행해 구 graph의 두 메서드 TypeError 재현. 새 graph는 성공. VM 결과이며 실제 Chrome을 대신하지 않음 |
| 첫 검사 `2547e5c` / [Journey36080247844](https://github.com/HyungwonPark/ai-company/actions/runs/36080247844) | 같은 경로의 hash만 바꾸어 모듈 요청이 발생하지 않아 대기 timeout. **시험 준비 실패**로 기록하며 제품 결함 재현이라고 세지 않음 |
| 준비 수정 `c4d0a1c` / [Journey36080550179](https://github.com/HyungwonPark/ai-company/actions/runs/36080550179) | 실제 문서 reload로 같은 조건을 구성. 정상 인증·프로젝트 API를 유지한 상태에서 v8 active2/v10 waiting0과 새 app/구 graph의 실제 수신 SHA를 확인했고, 처리되지 않은 `workspaceGraph.selectScope is not a function`으로 실패. 수정 전 제품 코드가 `0bcf8d7`과 같음을 대조했다. |
| 제품 수정 `ffb62c37e4822530f38f15f4b0f64781cdf40bc6` / [최종 Journey](https://github.com/HyungwonPark/ai-company/actions/runs/36080894484) | 네 조합 모두 성공. 정확한 파일 SHA·인증/API·v8 active2/v10 waiting0을 다시 대조했고 처리되지 않은 예외0·업무쓰기0, 사용자 명시 갱신 후 같은 실행 탐색과 원래 초안 복원을 확인했다. |

[최초 실패 증적](../evidence/journey-r25b/original-browser-failure.json), [최종 혼합 검증](../evidence/journey-r25b/journey-mixed-validation.json), [독립 검수](../evidence/journey-r25b/independent-review.md)를 연결한다. 검사 준비 실패와 제품 실패, 수정을 거친 성공을 덮어쓰거나 혼합하지 않는다.

고정 미리보기는 원래 승인 기한이 지나면서 별도 검사에서 실패했다. 기존 제품은 frozen `pending`+만료 상태에서 **승인**만 비활성화하고 반려·수정 요청을 표시한다. 실제 API는 만료를 `expired`로 투영하고 만료 결정을 거부하지만 고정 HTML은 원문을 갱신하지 않는다. 따라서 검사는 만료 승인 비활성화와 기존 모든 비GET 쓰기405 검사를 유지하도록 정정했다. 시계·승인 원문·기한·보호 규칙은 바꾸지 않았다. [당시 실패](../evidence/journey-r25b/frozen-expiry-expectation-failure.json)와 독립 대조를 보존했다.

## 인증된 격리 Chrome

시험 서버는 loopback의 실제 `ManagementHTTPServer`·비밀번호 인증·세션·SQLite·프로젝트 API를 사용했다. 공개 정적 파일의 버전과 일부 파일 요청 실패만 시험용으로 주입했다. 인증/프로젝트 응답을 가짜 성공으로 대체하지 않았다. 업무 POST가 서버까지 도달하면 시험에서 기록·거부하고 실패로 판정한다.

1. v8이 제어하는 인증된 두 탭에서 PM 초안을 직접 입력한다.
2. 새 worker를 설치하되 waiting으로 유지하고 구 탭 입력을 대조한다.
3. 새 app+구 graph, 새 app+구 graph/report, 새 app+구 report, 구 app+새 모듈을 각각 구성한다. HTTP cache를 끄고 실제 수신한 app/graph/report SHA, active/waiting의 client 수, 정상 인증·프로젝트 API를 확인한다.
4. 새 앱의 미지원 조합은 명시적 안내·URL/초안 보존·다른 실행 표시 없음·업무 쓰기0·처리되지 않은 예외0을 확인한다. 숨겨진 오래된 버튼/폼이 이벤트를 보내도 새 요청이 나가지 않는지 검사한다.
5. 파일 통신을 복구한 뒤 사용자가 갱신 버튼을 클릭하고 같은 실행의 진행→결과→승인 연결, 원래 PM 초안을 대조한다. 기존 다른 탭의 입력은 유지한다.
6. 역방향 조합은 구 앱의 기존 프로젝트 단위 읽기 계약과 정상 렌더를 확인한다. 구 앱이 새 여정 계약을 갖는다는 주장은 하지 않는다.

실제 Chrome sandbox를 활성화했다. Light320·Black390에서 안내와 실제 버튼을 확인했다. 업무 테이블29개 전후 불변은 인증 시험 테이블과 구분한다. 노드·역할·후보의 실행 사실은 합성 자료이며 모델이나 운영 worker를 호출한 검증이 아니다.

## 고정 후보와 회귀

| 항목 | 값·결과 |
| --- | --- |
| 제품·최종 검사 | `ffb62c37e4822530f38f15f4b0f64781cdf40bc6` |
| Python | [CI](https://github.com/HyungwonPark/ai-company/actions/runs/36080894441), 3.11·3.12 각467회귀/서버전용3 skip 및 Android 준비4 구분 |
| Console | [CI](https://github.com/HyungwonPark/ai-company/actions/runs/36080894592), 기존 그래프1px 드래그·상호작용·인증·고정 그림 보존 |
| Journey | [CI](https://github.com/HyungwonPark/ai-company/actions/runs/36080894484), 기존 읽기/쓰기/G1~G3·상태48조합·공개 SW갱신/복구·새 인증 혼합조합 |
| 함수 회귀 | 실제 구/신 graph factory와 제품 함수, 세 혼합조합·업무18요청 차단·인증/조회·주소·초안·수동 갱신 대기. 기존 상태26+종합4·범위60·지연탐색4·저장/manager 회귀 보존 |
| 이미지 | `sha256:b3353656c9717bc4c67395c86611df61370fc8a53786da2c22e47e9f5df83576` — arm64 로컬 Docker ID, registry pull 주소 아님. 읽기전용 root·network none·임시 상태·UID65532·권한제한에서113설치 파일과 HTTP/익명401/Host403 확인 |
| 소스 manifest SHA | `377374102c7f94a4ffec0d8fd5082fdb3678521afa458a80bbe8e0e1a41bb540` |
| ZIP SHA-256 | `7b9acf62834324edfb26b745d43315697d2f7092db456c8ee470c31a9d1420d0` |
| HTML SHA-256 | `9a380f843e390ddff3233e2627ccada40d65eb92e4938634fa91d02c0b2a4209` |

[후보·CI 출처](../evidence/journey-r25b/candidate.json), [개별 증적과 해시](../evidence/journey-r25b/README.md), [미리보기](../previews/journey/AI-Company-journey-preview.zip), [미리보기 안내](../previews/journey/README.md). HTML과 manifest는 같은 최종 CI가 검사한 파일 그대로이며 원본28파일을 고정 제품과 대조한다. PR 합성 merge checkout과 head는 구분한다. Automation evidence의 skipped는 실제 모델 PASS가 아니다.

## 적용 경계와 남은 확인

[최신 적용·복구안](journey-integration-rollout-2026-09-24.md)에는 새 이미지와 웹1개 교체만 고정한다. worker·DB·카탈로그·예산·모델·재시도·기존 권한은 유지한다. 이번 구현·CI·패키지 게시로 운영 변경은 하지 않았다. 복구 시에도 DB·사용량·승인 기록을 과거 백업으로 덮어쓰지 않는다.

실제 APK·공개 도메인의 갱신/로그인, 실제 모델 전체 작업, 서명키 외부 보관/복원은 이번 검증 밖이다. 기존 v8 비인증 로그인 암호의 offline 초기화 제약은 PM 업무 초안과 구분해 보존한다. 기존 순서형·모바일 사용성 한계를 전면 디자인 개편 완료로 바꾸지 않는다. PR #10 pending·커플 서비스·운영 큐·타이머·서명키는 조작하지 않았다.
