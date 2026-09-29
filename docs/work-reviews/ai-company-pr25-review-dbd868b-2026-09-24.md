# PR #25 통합 후보 검수 — dbd868b

2026-09-24 KST. Work 검수자 검토. 구현은 서버 Codex에서 수행한다.

**판정: 보완 요청 2건(P2).** 순서형 연결·G1~G3·기존 응답 경합 방어와 제출 패키지의 동일성은 확인했다. 다만 R25-1 갱신 대기 중 오프라인 부팅, R25-2 결과 상태 분류를 보완하기 전에는 “이번 범위 차단 결함 없음”과 E 완료 판정을 유지하지 않는다. 이미 해결한 PR #22의 F1·F2를 다시 연 것이 아니다.

## 대상과 실제 확인

- [PR #25](https://github.com/HyungwonPark/ai-company/pull/25): open/draft, 미병합. 기준 PR #24 `75227480d27d3eb1cd57fa43a07a0ab986be3eb5`.
- 제품 `d46136da9108608e7bdce6bdbfb0da35e825126a`, 검사 `6a366e69909b204881cc1d0a547a7b5cd06173ee`, 기록 `dbd868b1611ef374e9169c2254590d69d745b2dd`.
- 기준 명세: [야간 A~E 지시](overnight-journey-integration-2026-09-24.md). 순서형 선택·비운영 범위·기존 권한과 한도는 그대로다.
- 저장소 AGENTS와 `ai-company-frontend`를 읽고 적용했다. 코드 흐름·그래프·출시 경계를 나누어 검수하고, 두 결함의 실제 JS 재현을 주 검수자가 다시 실행했다.

| 항목 | 이번 Work 검수 |
| --- | --- |
| 코드 범위 | 세 후보의 제품 tree `293cc96e1776c23c8514c92e5959ba4bda918d81` 일치. 제품 이후는 검사/문서/패키지 변경. 과거 운영 기록 `2087ecc` 대비 제품 변경은 웹 10파일이며 worker·DB 코드·Android·배포 정의·의존성 변경 없음 |
| CI | [Python](https://github.com/HyungwonPark/ai-company/actions/runs/35905379824)·[Console](https://github.com/HyungwonPark/ai-company/actions/runs/35905379779)·[Journey](https://github.com/HyungwonPark/ai-company/actions/runs/35905379886) 모두 검사 커밋 `6a366e6`, attempt 1, success를 API 조회. Journey 로그의 실제 checkout은 PR 합성 merge `b3d155c1100b5b1386e399e5becaab1b331fb6d9` |
| 검사 범위 | Python 3.12 로그에서 467개 실행·3 skipped, Android 준비 4개와 DEMO_READY 확인. 3.11 job 성공은 확인했으나 이번 직접 로그 조회는 도구 오류로 읽지 못함. Automation evidence skipped는 실모델 검증으로 세지 않음 |
| 미리보기 | ZIP SHA-256 `10467c136710cdbc135bf0c465b0b7467798af97de186a30d41f2a608d238b44`, HTML `f00fd8a37e30f9c90221e0b0b327b2c6e024ff2c544670f4f9dfcffdb98141ff` 직접 계산 일치. manifest의 29개 원본 파일을 고정 Git 소스와 대조하고 동일 HTML 재생성 일치 |
| 재생성 조건 | 공개 소스·fixture·글꼴을 사용하고 Git commit/clean 메타데이터만 CI manifest 값으로 제공했다. 제품 동작을 고쳐 패키지와 맞춘 것이 아님 |
| 동선·응답 경합 | 실제 JS의 식별자/입력 60개와 지연 응답 4개 검사 재실행 통과. 파일의 상대 경로를 위한 검수 디렉터리 구성만 조정 |
| 그래프 | 실제 JS 기존 38관계·480개 생성 배치·경로 유지·장애물 재탐색·정확한 snapshot 검사 통과. 독립 함수 계산에서 검사 자기 이관 PC 985→128/모바일 749→128, 개발 자기 이관 581→115, 반환 PC 826→706/모바일 767→647 |
| 화면 | 공개된 PC/390 Light 그래프, 390 Light 계획 확정, 390 Black 후보 결정 캡처 4장을 직접 열람. 주요 노드 중앙·가독성과 짧은 이관선을 확인. 이것은 Work의 직접 브라우저/APK 조작이 아님 |
| 적용안 | 고정 이미지가 로컬 Docker image ID임을 명시. 현재 버전 재확인→웹 1개 교체→실패 시 직전 웹 복구, worker/DB 보존 경계는 적절. 실제 서버 이미지·현재 운영 상태는 이번에 조회하지 않음 |

기존 CI 성공과 새로운 경계 결함은 양립한다. 검사 수나 PASS 보고를 이유로 아래 재현을 제외하지 않는다.

## R25-1 — P2: 기존 서비스워커가 제어하는 탭에서 새 모듈을 오프라인으로 읽지 못함

**근거:** [새 app.js](https://github.com/HyungwonPark/ai-company/blob/dbd868b1611ef374e9169c2254590d69d745b2dd/src/ai_company/web/app.js)의 첫 줄은 `./journey-ui.js`를 정적으로 가져온다. 기존 기준의 [v8 sw.js](https://github.com/HyungwonPark/ai-company/blob/75227480d27d3eb1cd57fa43a07a0ab986be3eb5/src/ai_company/web/sw.js) 4·7행은 해당 경로를 SHELL에 포함하지 않고, 포함된 경로만 network-first 처리한다. [현재 서비스워커 검사](https://github.com/HyungwonPark/ai-company/blob/dbd868b1611ef374e9169c2254590d69d745b2dd/tests/ui/journey_service_worker.cjs) 49~54행은 기존 탭의 테마 변경만 확인한 뒤 모든 탭을 닫고, 오프라인 검사는 v9 활성화 후에 한다.

재현 순서:

1. v8이 제어하는 탭 두 개가 열린다.
2. 새 후보의 v9가 설치되지만 기존 탭 때문에 활성화를 기다린다.
3. 한 탭을 온라인 새로고침하면 새 app.js가 v8 캐시에 들어간다.
4. 다른 탭을 열어 둔 채 오프라인 새로고침한다.
5. v8은 새 app.js를 캐시에서 반환하지만 신규 journey-ui.js 요청을 가로채지 않는다. v9 캐시에 그 파일이 있어도 v8의 경로 검사에서 빠져 네트워크 요청이 실패한다. 정적 import 실패는 앱 초기 실행을 막을 수 있다.

[읽기 전용 재현 스크립트](repro/pr25-waiting-worker-offline.cjs)는 저장소의 수정하지 않은 v8/v9 소스를 Node VM에서 실행하고 CacheStorage·네트워크를 모사한다. 주 검수자가 고정 GitHub 원문과 입력 bytes를 대조한 뒤 재실행해 REPRODUCED를 확인했다. **실제 브라우저 서비스워커 생명주기나 APK에서 직접 재현했다는 뜻은 아니다.** v9가 제어할 때 같은 모듈은 오프라인으로 반환되는 대조도 포함한다.

수정·수용 조건:

- 기존 제어 탭에서도 새 shell이 정상 부팅할 수 있는 최소 호환 처리를 선택한다. 새 v9의 SHELL만 바꾸어 기존 v8도 고쳐졌다고 판단하지 않는다.
- 격리된 실제 Chrome에서 다른 구버전 탭을 유지한 채 온라인 새로고침→v8 controller/v9 waiting 확인→오프라인 새로고침을 추가한다. 새 app bytes를 받았음과 로그인/공개 shell 표시, import 오류 없음, 사적 탐색 숨김을 확인한다.
- 원래의 전체 탭 종료 후 활성화·온라인 복구·구버전 복구 검사도 유지한다. 복구 대기 상태에도 같은 종류의 혼합 버전 위험을 확인한다.
- API·POST 비캐시/비재생, 작성 중 입력과 기존 탭 보존을 유지한다. 강제 skipWaiting·앱 데이터 삭제·운영 캐시 초기화로 회피하지 않는다.
- 브라우저에서 성립하지 않는다는 결론이면 controller·waiting·받은 파일 bytes·네트워크/캐시 근거로 Node 재현의 어떤 전제가 달랐는지 설명한다.

## R25-2 — P2: 완료·운영자 확인 상태를 진행·대기로 오분류

**위치:** [report-ui.js](https://github.com/HyungwonPark/ai-company/blob/dbd868b1611ef374e9169c2254590d69d745b2dd/src/ai_company/web/report-ui.js) 14행의 새 `execution()` 분류와 [journey-ui.js](https://github.com/HyungwonPark/ai-company/blob/dbd868b1611ef374e9169c2254590d69d745b2dd/src/ai_company/web/journey-ui.js) 23~36행.

실제 렌더 함수에 같은 실행의 역할 네 개를 넣으면 아래 결과가 나온다.

| 저장 상태 | 현재 새 결과 화면 | 필요한 의미 |
| --- | --- | --- |
| MERGE_READY | 진행·대기 | 기존 역할 완료 분류와 일치. 마스터 수용·배포 완료로 확대 금지 |
| RECONCILIATION_REQUIRED | 진행·대기 | 운영자 확인 필요 |
| NEEDS_RECONCILIATION | 진행·대기 | 종료/실행 결과 대조 필요 |
| NEEDS_CONTEXT_HANDOFF | 진행·대기 | 인수인계 조치 필요 |

현재 출력은 **완료 0 / 진행·대기 4 / 차단 0**이다. 기다리기만 하면 되는 일과 사용자/운영자가 조치해야 하는 일을 혼동시킨다. [기존 project_report.py](https://github.com/HyungwonPark/ai-company/blob/dbd868b1611ef374e9169c2254590d69d745b2dd/src/ai_company/project_report.py) 4~7행은 해당 완료·운영자 확인 상태를 이미 정의한다. [dispatcher.py](https://github.com/HyungwonPark/ai-company/blob/dbd868b1611ef374e9169c2254590d69d745b2dd/src/ai_company/dispatcher.py) 507~516행은 NEEDS_CONTEXT_HANDOFF/NEEDS_RECONCILIATION을 예약 시각 없이 생성하며, [workspace_graph.py](https://github.com/HyungwonPark/ai-company/blob/dbd868b1611ef374e9169c2254590d69d745b2dd/src/ai_company/workspace_graph.py) 120행은 작업 상태를 역할 노드로 전달한다.

같은 누락의 변형으로 실제 실행 `state=rejected`도 새 여정에서는 **상태 미확인**이 된다. [automation.py](https://github.com/HyungwonPark/ai-company/blob/dbd868b1611ef374e9169c2254590d69d745b2dd/src/ai_company/automation.py) 377~387행에서 실제 반려 결과를 이 상태로 저장하므로 미등록 상태가 아니다.

[읽기 전용 재현 스크립트](repro/pr25-status-projection.mjs)로 고정 소스의 실제 렌더 함수·journeyStatus를 실행했다. 원본 입력은 변경하지 않았다. 브라우저·운영 승인 호출은 없다.

수정·수용 조건:

- 기존 제품의 완료·대기·운영자 확인·반려 상태 계약을 대조하고, 실행 범위에 맞게 일관되게 표시한다. 현재 계획의 집계를 과거 실행에 복사하는 방식으로 해결하지 않는다.
- 위 네 상태와 반려 상태, 정상 진행·예약 대기·알 수 없는 신규 상태를 실제 JS 검사 및 임시 API 화면에 포함한다.
- 미등록 상태를 암묵적으로 진행·대기에 넣지 않는다. 확인 불가 상태를 드러내고 원문을 상세에서 확인하게 한다.
- 운영자 확인이 필요한 상태에서 “기다리세요”로 끝내지 않고 필요한 다음 행동을 안내한다. 역할 완료/통합 검수/마스터 수용/배포를 별개로 유지한다.
- 저장 상태·digest·승인 결과·예약·예산은 고치지 않는다. 이번 수정은 투영과 동선의 정확성을 위한 것이다.

## 비차단 UX 메모

모바일에는 진행 제목·대상 선택기가 반복되고, 그래프 전에 여러 구획이 쌓이며 결과 보고도 길다. 캡처에서 확인했고 제출 문서도 한계로 인정한다. 사용자 목표에 비추어 다음 사용성 개선 대상이지만, 이번 두 결함 수정과 함께 전체 구조를 다시 설계하도록 요구하지 않는다. 첫 화면 밖의 외곽 관계는 전체 보기·이동·목록으로 접근하는 현재 선택을 유지한다.

## 서버 Codex 후속 지시

1. PR #25와 기존 변경을 보존하고 R25-1·R25-2를 재현한다. 구현은 서버에서 수행하고 Work의 문서 브랜치에는 제품 코드를 넣지 않는다.
2. 두 결함과 같은 원인의 관련 상태/경로만 보완한다. 배치 선택을 다시 묻거나 A~E 전체를 처음부터 재구현하지 않는다.
3. 최초 후보 실패→수정 후보 성공을 보존한다. 기존 의미 있는 회귀, 실제 격리 브라우저 경계와 독립 검수를 같은 최종 후보에서 확인한다.
4. 제품 bytes가 바뀌므로 기존 이미지·ZIP 해시를 재사용하지 않는다. 필요한 이미지·미리보기·서비스워커·manifest·CI·적용/복구안을 새 후보에 맞춰 갱신한다. 기록 전용 커밋과 제품 커밋을 구분한다.
5. 결과는 두 항목의 해결 여부·재현/화면 근거·최종 제품/검사/기록 커밋·새 해시·남은 한계로 제출한다. 운영 교체·병합·PR #10 pending 변경·서명키·새 모델 연결은 하지 않는다.

재현 스크립트는 Node 18+에서 고정 커밋을 가진 저장소의 Git 원문을 읽는다. 제품 파일·운영 네트워크·승인 기록에 쓰기 작업을 하지 않는다. 출력 REPRODUCED는 **원래 결함 재현 성공**이며 제품 합격이 아니다. 후속 후보의 회귀 검사로 옮길 때에는 기대 결과를 정상 동작 기준으로 작성한다.

이번 검수는 공개 코드·CI·패키지·대표 캡처와 실제 JS 검사를 대상으로 했다. 실제 운영 DB·계정·현재 서버·APK 직접 조작·새 모델 전체 실행·외부 서명키 복원은 확인하지 않았다. 이번 문서 게시나 검수 완료는 운영 적용 승인이 아니다.

[최종 수정일: 2026-09-24, v1.0]
