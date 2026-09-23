# PR #25 R25 독립 검수 기록

최종 판정은 아래 **최종 독립 판정 — 14114a7** 절이다. 앞선 보류·실패·중간 후보는 당시 이력으로 보존한다. 제품 파일은 작성하지 않았고 독립 상태 회귀 파일은 검수자가 작성했다.

## 최초 검수 범위와 당시 판정

- 제품 작성과 분리된 독립 검수. 제품 파일 수정 없음.
- 사용자 지정 원문 PR23 `eb2e6a862dc85af1ea3da229697e0e6f1ebc45fe` 검수 문서와 재현 스크립트를 읽었다.
- 초기 제품 기록 기준 `dbd868b1611ef374e9169c2254590d69d745b2dd`.
- AGENTS, ai-company-frontend, code-review-skill, variant-analysis 지침 적용.
- 당시 판정: 원래 R25-1/R25-2 모두 재현. 수정 후보 및 실제 격리 Chrome 증거 재검수 전 완료 판정하지 않음.

## 최초 실패 독립 재현

고정 Git 원문의 수정하지 않은 두 스크립트를 임시 경로로 추출해 실행했다. 기본 도구 격리에서 Node 자식 git의 spawnSync EPERM이 발생했으며 읽기 전용 명령 재실행에서는 아래 결과를 확인했다. 이 EPERM은 제품 결함이 아니다.

1. 상태 실제 JS 재현: REPRODUCED. 완료 0 / 진행·대기 4 / 차단 0. 입력은 MERGE_READY, RECONCILIATION_REQUIRED, NEEDS_RECONCILIATION, NEEDS_CONTEXT_HANDOFF. rejected 실행은 상태 미확인으로 출력됐다. 입력 JSON 불변 확인.
2. Node VM SW 재현: REPRODUCED. v9 cache에 새 모듈이 존재해도 v8 제어자는 journey-ui.js를 가로채지 않는다. 새 app이 v8 캐시에 남은 상태에서 오프라인 import 실패. v9 제어 대조는 성공.

두 재현은 실제 JS/VM이고 실제 Chrome 생명주기, 운영 API, 실기기 검증이 아니다.

## 같은 원인 경계 조사

### 상태 투영

- 확인된 P2 변형: automation.py _run은 resume_at=None인 미완료 역할이 있으면 run.state를 waiting으로 저장한다. 역할이 NEEDS_RECONCILIATION 또는 NEEDS_CONTEXT_HANDOFF인 경우도 해당한다. 결과 상단을 run.state만으로 분류하면 역할 그룹을 고쳐도 대기 안내가 남는다. 정확한 같은 snapshot/run의 역할 조치 상태를 우선해야 한다.
- 확인된 P2 변형: dispatcher의 NO_ELIGIBLE_AGENT는 재개 예약 없는 명시적 설정 결함이다. 알 수 없는 상태를 진행으로 분류하던 보수적 부정 목록의 영향을 받는다.
- 상태 목록 근거: project_report.py DONE/NEEDS_OPERATOR, automation.py _integration/_run, dispatcher.py _advance/step, workspace_graph.py exact task 투영. HANDOFF_PENDING, CHECK_RUNNING, WAITING_CHECKS, WAITING_ROLE_REPAIR, WAITING_PROJECT_BUDGET, SUPERSEDED, DEMO_READY도 검색했다. 미등록 상태는 원문+확인 필요로 보여야 하며 예약이 없는 상황을 임의의 예약으로 만들면 안 된다.
- project_report.py는 current_plan 집계이므로 과거 선택 실행 결과에 재사용하면 안 된다. 기존 exact project/plan digest/run guard를 보존해야 한다.
- 그래프 statusLabels 및 앱 badge의 한국어 라벨 누락을 조사했다. 색상·라벨의 수정만으로 승인이나 실행을 바꾸지 않는지 확인 필요.

### 서비스워커 혼합 버전

- 기존 v8 SHELL에 없던 static import 경로가 원인. 새 worker SHELL만 수정해서 v8 제어자를 고칠 수 없다.
- 업그레이드 waiting뿐 아니라 rollback waiting 조합을 검사해야 한다. 전체 탭 종료 전 기존 제어자는 남는다.
- 검토 중 최소안(report-ui에 helper 이동)은 정상 온라인 전체갱신→오프라인을 해결하지만 새 app이 새 named export를 요구하고 report-ui는 오프라인 fallback으로 옛 bytes인 부분갱신 조합을 별도 검토해야 한다. app cache와 report cache는 원자적으로 교체되지 않는다.
- 강제 skipWaiting/cache wipe/비공개 API cache/POST replay는 회피책으로 허용되지 않는다.

## 검수하지 않은 것

운영 환경·DB·승인·예산·서명키·실제 모델·실기기 APK에는 접근하지 않았다. 수정 후보와 Chrome 증거가 제공되면 같은 후보로 후속 판정을 추가한다.

## 독립 회귀 작성

사용자가 요청한 실제 브라우저 경계 검증을 위해 제품 작성과 분리해 정상 기대값 회귀를 작성했다. 소유 파일은 tests/ui/journey_status_checks.mjs, tests/ui/journey_status.cjs, tests/ui/run_journey_workspace.py 세 개뿐이다. 원제품에서 MERGE_READY 완료 집계 기대 1이 실제 0이라 FAIL임을 확인했다. 브라우저 회귀는 같은 임시 SQLite API에 8개 실행 상태 조합을 저장하고 320/390/1440 × Light/Black에서 결과·진행·원문·동일 실행 선택을 대조한다. seed-only는 실제 API 투영 8개 실행의 role 상태가 저장값과 같은지 PASS했다. 업무 테이블 29개 전후 불변 비교를 유지했다. 현재 단계는 브라우저 CI 실행 전이며 실제 브라우저 PASS로 주장하지 않는다.

## 수정 작업트리 독립 코드 재검수

- app.js 자체에 순수 여정 함수와 역할 분류기를 두었고 새 정적 module/named export 의존성을 제거했다. report-ui 기존 factory에 함수를 주입하므로 기존 v8 export 계약이 유지된다. 구버전 report에 execution이 없으면 현재-plan 집계로 대신하지 않고 화면 갱신 필요를 표시한다. v10 SHELL에서 삭제 모듈도 제거되어 addAll 404를 만들지 않는다.
- 정확한 project/plan/digest/run 역할만 집계한다. 원문 상태를 상세로 보존하며 예약을 생성하거나 저장 결과를 바꾸지 않는다. 완료 1/확인 필요 3, 기록된 반려, waiting 아래 operator, unknown 접두사, NO_ELIGIBLE_AGENT/SUPERSEDED, foreign run/digest 제외를 포함한 실제 JS 26사례 PASS.
- 초기 조사 메모 정정: PM의 pmRequestDisplay는 needs_reconciliation을 추출하지만, 실제 소비자인 requestProblem 목록에는 누락돼 있었다. 이번 수정은 그 누락도 보완한다. 단지 추출 helper가 존재하는 것만으로 안전하다고 본 최초 의견을 정정했다.
- 경미한 비차단 피드백: labels 동일키 묶음 중복 선언을 발견해 작성자에게 정리를 권했다.
- 코드에서 추가 차단 결함을 찾지 못했다. 아직 고정 수정 커밋·격리 Chrome 성공 증거가 없으므로 최종 완료 판정은 보류한다.

## 후속 current_plan 변형 발견과 보완

제품 4f81525를 검토하는 동안 결과 하단의 exact current_plan 종합도 같은 원인임을 확인했다. backend의 in_progress는 미완료 전체이나 화면이 이를 진행으로 출력하며, NO_ELIGIBLE_AGENT/SUPERSEDED/미등록 상태에서는 시스템 next_actions가 작업 계속을 안내할 수 있었다. 작성자에게 P2 관련 경계로 보완을 요청했다.

기존 report-ui 4f81525 원문 bytes에 정상 기대 회귀를 적용하여 current-plan uses the same explicit role classification FAIL을 재현했다(로그 [비공개 임시 증적 경로]). 수정 작업트리에서는 단일 roleStatus 5분류, operator/unknown 다음 행동, 시스템 원문 안내 별도 상세, 보고 digest와 입력 불변의 4사례가 PASS했다. 브라우저에는 최신 superseded 실행의 exact 종합도 선택 실행 요약과 동일 분류인지 대조를 추가했다. PM NEEDS_RECONCILIATION 소비 화면의 정상 안내/일반 응답대기 부재 회귀도 PASS했다.

## 원래 실제 Chrome 실패 증거 독립 확인

검사 전용 e49ad40 / Journey 35931537172의 artifact JSON을 읽었다. 상태 결과는 320 Light completed-operator에서 대기 != 확인 필요 FAIL, SW는 v8 두 탭 제어와 신버전 waiting 확인 후 /journey-ui.js net::ERR_INTERNET_DISCONNECTED 및 로그인 폼 미등장으로 FAIL이다. 기존 Node 재현과 실제 Chrome 경계 결과가 일치한다. 이 단계는 수정 후보 Chrome PASS를 대신하지 않는다.

## 고정 최종 제품 코드 재검수

- 제품 c7f007a064c95c2a37a1e629a6fa8dd92ed801f7, 검사 1b80092b07187b0d2b3a66f2179d9153e1f4f553. 두 커밋 차이는 SW 회귀 1파일뿐이며 제품 bytes는 같다.
- 해당 후보에서 독립 상태 26사례 및 current_plan 4사례, PM 확인 상태 및 기존 manager 회귀를 다시 실행해 PASS했다.
- 최종 이미지 검사 JSON을 읽고 이미지 소스 manifest 113개를 현재 고정 소스 bytes SHA256와 독립 대조하여 모두 일치함을 확인했다. image sha256:93a4ff2448e38ebb2d167fb1371e37b2d050f691d6dc2be387d228a7dfc1b0bb는 arm64 로컬 Docker ID이며 registry pull 주소가 아니다. 이 독립 검수는 이미지를 다시 실행한 검증은 아니며, 작성자가 수행한 격리 이미지 검사 결과와 소스 일치의 대조이다.
- 최종 SW 검사에는 HTTP 캐시 비활성, v8 active/v10 waiting의 실제 클라이언트 수, partial report 원문 SHA, 미전송 기존 입력 보존, v10 active/v8 waiting의 rollback이 포함된다. 실제 최종 Chrome artifact를 확인하기 전까지 결과는 검수 대기로 둔다.

## 1b80092 추가 검사 실패와 검사 범위 정정 독립 검토

1b80092 Journey 35932498537의 실패 JSON에서 upgrade-partial/full 두 경계는 모두 v8 제어 클라이언트 2개, v10 waiting 클라이언트 0개, 정확한 새 app/부분 또는 새 report SHA, 공개 로그인 부팅, 사적 탐색 0으로 성공했음을 확인했다. 이어서 기존 v8 탭의 로그인 비밀번호 입력값이 offline/online 후 그대로 남는다는 추가 기대가 실제 빈 문자열이라 실패했다.

고정 기준 7522748 app.js 316행은 offline에서 render를 호출하며 218행은 인증 전 authScreen으로 DOM을 재생성한다. 로그인 비밀번호는 업무 draft 저장 대상이 아니다. 후보 SW가 기존 실행 중인 v8 JS를 교체하지 않았다는 점과 일치하며, 새 코드의 모듈 부팅 실패와 다른 기존 경계다.

작성자의 검사 분리안을 타당하다고 판정했다. 업데이트 대기만으로 미전송 입력을 없애지 않는지 offline 이전에 확인하고 POST 0을 검증한다. offline 이후 로그인 암호 초기화는 실제 관측된 기존 제약으로 별도 기록하며 전체 상황의 입력 보존을 주장하지 않는다. 인증 업무 PM draft는 기존 D의 실제 API 지연·불확실 저장 검사로 검증한다. 1b80092 실패 기록과 성공한 부분 경계는 숨기거나 PASS로 덮어쓰면 안 된다. 제품·격리·보호규칙 수정 없이 이 구분을 반영하는 것은 정상적인 검사 범위 정정이며, 단순 assertion 삭제로 합격시키는 것과 구분된다.

## 0036fb1 실제 Chrome·패키지·시각 독립 대조

- 검사 0036fb1d9243be34136f2393c60bdeb55f4f2234 / Journey 35932901930의 최종 artifact를 읽었다. Chrome 152.0.7977.82, sandbox=true. 상태 8개 실행 × 6 화면 조합 = 48 PASS, 업무 테이블 29개 불변 및 브라우저 return 0. current_plan superseded 분류/안내도 해당 DOM 검사에 포함됐다.
- SW upgrade-partial/upgrade-complete/rollback 세 실제 경계 모두 PASS. 제어 worker와 대기 worker의 클라이언트 수 2/0, app/report SHA, HTTP 캐시 비활성, 로그인 표시, 사적 탐색 없음, 온라인 복구 및 POST/API 비캐시·비재생을 대조했다. 실패 요청은 의도된 오프라인 API 요청이고 JS errors는 비어 있다. 미전송 입력은 대기 설치에서 유지됐으며 v8 offline 인증전 암호 초기화가 별도로 기록됐다.
- 미리보기 manifest 원본 28개 SHA256를 고정 작업트리와 독립 대조해 일치. HTML SHA256 ecdd6afbe4063a3b3fc47c9d3a39eca332e6fe653f45da2beade695664566916 직접 계산 일치. manifest source_commit dbe9e313bab4f098c07257eba3d5fcdd056125ac는 CI 합성 checkout이며 제품 c7f007a와 구분한다.
- PNG 직접 열람: Light320 completed-operator, Black390 operator-handoff, Light390 unknown, Black320 rejected, SW upgrade-partial offline와 rollback offline. 결과의 완료/확인 필요/미확인 분류, 상태 원문, 운영자 다음 행동, 반려와 역할완료의 구분, 로그인 shell 표시는 읽을 수 있다. 실제 수동 브라우저 조작이나 APK 실기기 검수로 주장하지 않는다.
- current_plan superseded는 별도 PNG가 없으므로 DOM 자동검사 확인만 기록한다. 원문 상세와 긴 보고로 화면이 길고, 테스트 중 비밀번호 변경 toast 및 초점된 본문 건너뛰기 링크가 일부 캡처에 나타난다. 이는 이번 상태 정확성의 차단 결함으로 판단하지 않는다.
- 추가 시각 경계 발견: 결과가 확인 필요/상태 미확인이어도 상단 선택기 scopeName은 raw run.state에 labels.waiting='예약 대기'를 적용한다. 실제 재개 예약이 없는 상태에 예약을 암시하므로 작성자에게 same-cause 보완 또는 정확한 제한 기록을 요청했다. 해당 항목 처리 후 최종 판정을 확정한다.

## 선택기의 잘못된 예약 암시 보완

대표 PNG에서 발견한 선택기 문제를 작성자가 수용했다. 최소 변경은 generic waiting의 표시를 예약 대기→대기로 바꾸는 것으로, 예약은 실제 resume_at 근거에서만 출력한다. 고정 c7 app의 실제 scopeName/labels를 추출한 독립 JS에서 실행 1 · 예약 대기는 정상 기대에 FAIL, 수정 작업트리 실행 1 · 대기는 PASS했다. 원문 상태나 예약값을 변경하지 않는다. [비공개 임시 증적 경로]에 원래 실패를 보존했다.

독립 브라우저 회귀에 재개 예약이 없는 waiting 선택기의 예약 암시 부재, selector_text 관측값, current_plan superseded PNG 6장을 추가했다. 새 고정 후보의 실제 Chrome 결과를 확인한 뒤 최종 판정을 확정한다.

# 최종 독립 판정 — 14114a7

**판정: R25-1·R25-2와 이번 조사에서 발견한 관련 경계 보완 통과. 비운영 코드·회귀·화면 증거 범위에서 추가 차단 결함을 발견하지 못했다.** 이전 c7/0036 판정과 자료는 중간 이력이고, 아래 최종 후보를 대체하지 않는다.

## 고정 후보와 실제 증거

- 제품·검사: 14114a71584c65c6dbbd0b33a1a26c7246d1e1ad. Journey 35933619143의 최종 artifact를 읽고 코드와 대조했다.
- 독립 작성 실제 JS 26상태 + current_plan 4경계 회귀를 재실행해 PASS. manager의 NEEDS_RECONCILIATION 변형과 입력·명세 기존 회귀는 해당 고정 코드에서 통과 기록을 보존했다.
- 실제 격리 Chrome 152.0.7977.82, sandbox=true: 8상태 실행 × Light/Black × 320/390/1440 = 48조합 PASS. 선택기의 실제 selector_text에도 재개 예약 없는 상태가 예약이라고 나오지 않는다. current_plan 종합 5분류와 다음 행동은 같은 실행 결과와 일치한다.
- 상태 브라우저 검사의 실제 임시 API/SQLite 업무 테이블 29개 불변, browser_returncode 0. 원문 상태·승인·digest·예약·사용량을 변경하지 않는다. 인증용 예시 로그인·비밀번호 변경 테이블만 비교에서 구분해 제외한다.
- SW 세 경계 upgrade-partial/upgrade-complete/rollback PASS. v8 active/v10 waiting과 v10 active/v8 waiting의 실제 window client 수 2/0, 정확한 app/report bytes, HTTP cache disable, 오프라인 공개 부팅, 사적 탐색 미표시, 전체 탭 종료 후 활성화, 온라인 복구 및 API/POST 비캐시·비재생을 확인했다. JS errors는 비어 있다. 신규 app SHA256 f048a9d5f5826ccb8c40868e81256c3a652c0f3e62c3ac41196f1c7e563a0450.
- 기존 v8 로그인 암호는 설치 대기에서 보존되며 전송되지 않는다. offline auth 재렌더에서 빈 값이 되는 기존 제약은 여전히 남는다. 1b80092 실패를 보존하고 해당 경계와 업무 draft 보존을 구분한 정정은 타당하다.

## 최종 화면 직접 열람

최종 PNG journey-status-light-320-superseded, journey-status-black-390-superseded, journey-status-light-390-unknown, journey-sw-upgrade-complete-offline을 직접 열었다. 이전 후보의 operator/rejected/partial/rollback 6장 검토와 구분해 최종 새 화면을 확인했다.

- superseded의 역할 후속 담당 대조 안내가 실행 요약과 하단 종합에서 일치하고, '진행 1/확인 필요 1'을 구별한다. 이전 잘못된 계속 안내는 시스템 원문 상세로 보존되고 현재 조치로 제시되지 않는다.
- unknown은 미확인 2 및 원문 상세로 표시하고 자동 재개를 단정하지 않는다. 선택기는 단순 대기로 표시하며 예약을 암시하지 않는다.
- SW 대기/오프라인 공개 로그인은 비밀번호 입력 폼과 연결 실패 안내를 표시하고 사적 화면을 노출하지 않는다.
- 긴 종합 결과의 스크롤 길이, 반복되는 구획, 순간적인 비밀번호 변경 toast 및 초점된 본문 건너뛰기 링크는 기존/검사 시점의 시각 한계다. 전체 UX 개편 완료나 물리적 기기 가독성 검증으로 확대하지 않는다. 이번 상태 정확성의 차단 결함으로 판단하지 않는다.

## 동일 산출물 대조

- 미리보기 manifest의 원본 28파일 SHA256를 고정 제품/패키지 소스와 독립 대조해 모두 일치. HTML 직접 해시 606e68539fead877274cd16173f62311614dde87bcfb5413664fb9f7141a4417. manifest source_commit 82c794cd2d511950377a3caef71670e55877dc56는 GitHub CI 합성 checkout임을 구분한다.
- 이미지 sha256:8005ce1fa85a458d17bdf159a0af2774801a9a19b4c14adecff147b1f059ae01, product_commit 14114a7, arm64 로컬 Docker ID. registry pull 주소가 아니다. 원본 manifest 113파일 해시를 고정 소스와 독립 대조해 모두 일치. manifest SHA256 be595339189b847266015696461c933ca4ca23dbbcf162e12aeff603024bf26b. 이미지 격리 실행 PASS JSON(읽기 전용 root, uid65532, loopback-only network none, 임시 state, 운영 mount 없음, 익명 API401/타 Host403)을 확인했으며 이 검수자가 이미지를 재실행했다는 의미는 아니다.

## 남는 범위와 권한

이 검수는 제품 작성과 분리된 코드 검토, 직접 실행한 순수 JS 회귀, 독립 작성 브라우저 회귀의 GitHub 격리 Chrome 결과 및 캡처 검토다. 검수자가 APK나 수동 브라우저로 전체 흐름을 조작한 것은 아니다. 실제 모델 실행, 현재 운영 상태, 실기기 APK, 실제 도메인 SW 갱신, 서명키 외부 복원은 검증하지 않았다. 운영 배포·병합·PR #10 승인 변경·추가 모델 연결의 승인이 아니다. 초기 제품 결함 두 건, current_plan 변형, 선택기 변형, 중간 검사 기대 범위 실패 기록을 덮어쓰지 않았다.
