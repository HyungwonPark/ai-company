# 순서형 제품 후보 독립 코드 검수

검수 기준: `75227480d27d3eb1cd57fa43a07a0ab986be3eb5` → 최종 제품 `d46136da9108608e7bdce6bdbfb0da35e825126a`. 후속 검사 커밋 `6a366e69909b204881cc1d0a547a7b5cd06173ee`는 같은 제품 파일을 사용한다. 최초 후보 `a31c8cf`, 응답 경합 수정 `812469b`, 본문 크기 수정 `6f10570`의 실패·수정 이력은 아래에 보존한다.

검수자는 제품 코드 및 최초 수용 검사를 작성하지 않았다. 저장소에는 쓰지 않고 `/tmp`에 실제 함수 추출 재현과 이 기록만 작성했다. 통합 담당자가 발견 결함의 재현을 `812469b`의 `tests/ui/journey_navigation_checks.mjs`로 편입했으며, 이는 최초 구현·수용 검사 작성과 구별한다. 운영 API·DB·worker·계정·모델 제품 호출·네트워크 쓰기를 수행하지 않았다. 도구 구성으로 요청된 Astra/Ultra 개발 검수이며, 제품 운영 최종 검수 완주를 뜻하지 않는다.

## 현재 판정

**최종 제품 `d46136d`: 독립 코드 검수 PASS.** 발견한 P2 두 건은 수정됐고, 실제 임시 API의 PM 요청·계획 확정 지연 응답 회귀가 모두 통과했다. 추가로 재현한 미해결 결함은 없다. 같은 제품을 사용하는 `6a366e6`의 Journey 증거·미리보기 원본 해시 대조도 완료했다. 이 판정은 이번 비운영 제품 코드와 명시한 검증 범위에 한정한다.

전체 Console `35905379779` 및 Python `35905379824`의 SUCCESS는 통합 담당자의 원격 CI 조회 결과로 전달받았다. 두 전체 로그를 이 검수자가 다시 열지는 않았다. 직접 확인한 Journey artifact·원본 해시와 전달받은 전체 CI 성공을 구분하며, 코드 판정을 제품 운영 루프·실기기·배포 완료로 확대하지 않는다.

### 이전 후보의 판정 이력

`a31c8cf`: **수정 필요**. 늦은 응답이 명시한 탐색 대상을 덮는 P2 두 건을 확인했다.

`812469b`: **독립 코드 재검수에서 두 건 수정 확인, 추가 구체 결함 없음**. 고정 커밋을 확인하고 아래 네 가지 독립 단위 재현을 다시 통과했다. 실제 PM 요청 held-response 검사는 성공했으나 계획 본문 16px 기준 검사에서 중단됐다.

`6f10570`: 추가 제품 변경은 `manager.css`의 계획 본문 16px·행간 1.75 규칙뿐이었다. 실제 plan DOM과 CSS 우선순위를 대조했고, 권한/식별자/응답 경합 코드 변경은 없었다. 이후 독립 시각 검수의 닫기 터치 영역 지적을 반영한 `d46136d`를 최종 제품으로 검수했다.

## 확인한 결함

### P2-1 — 쓰기 응답 후 같은 화면의 새 실행 선택을 덮음

- 위치: `src/ai_company/web/app.js:286`, `src/ai_company/web/app.js:331` (`a31c8cf` 기준).
- `requestExecutionPlan()`은 요청 시작 시 project/view만 저장한다. 계획 확정 응답도 현재 project와 `manager` view만 확인한다. 요청 중 같은 프로젝트의 과거 실행 계획으로 이동하면 view는 여전히 `manager`이므로 응답이 사용자의 선택을 덮는다.
- PM 요청 재현: `#manager?project=p`에서 요청을 보류 → `#manager?project=p&run=old-run&snapshot=old-snapshot`로 이동 → 응답 완료 → URL이 `#manager?project=p`로 바뀌어 명시 대상이 사라진다.
- 확정 재현: 같은 탐색을 수행한 뒤 원래 계획의 성공 응답을 완료하면 URL이 `#progress?project=p&run=new-run`으로 바뀐다.
- 서버의 승인·digest·실행 생성 범위가 바뀐다는 결론은 아니다. 사용자의 새 읽기 대상이 늦은 이전 응답에 의해 바뀌는 결함이다.
- 수정 방향: 시작 경로와 탐색 세대를 고정하고 그대로일 때만 자동 이동한다. 사용자가 이동했다면 성공 사실만 알린다. 같은 URL로 돌아오는 A→B→A도 별도 탐색으로 취급한다.

### P2-2 — 렌더링이 아직 처리 전인 새 URL을 이전 프로젝트로 되돌림

- 위치: `src/ai_company/web/app.js:241` (`a31c8cf` 기준).
- 기준 코드의 `state.authenticated && routeMatches`가 제거되고 view만 같은지 검사한다. URL은 새 project/run으로 바뀌었으나 `hashchange`가 아직 state에 반영되기 전에 비동기 완료·갱신 렌더링이 실행되면 이전 state URL을 `history.replaceState`로 기록한다.
- 재현: state는 `progress / old-project / old-run`, URL은 `#progress?project=new-project&run=new-run`인 경계에서 실제 `render()` 실행 → 후보는 `#progress?project=old-project&run=old-run`, 기준 `7522748`은 새 URL을 유지한다.
- 수정 방향: `syncScope()`가 기본 선택을 추가하기 전 실제 URL과 state의 project/run/snapshot/approval/report/all을 대조한다. 일치한 경우에만 기본 선택과 URL 정규화를 수행한다.

## 독립 재현과 수정 확인

- 재현 파일: `비공개 재현 자료/journey-late-scope-repro.mjs`.
- 원래 후보 함수는 `비공개 재현 자료/journey-review-a31-app.js`, 기준 함수는 `비공개 재현 자료/journey-review-baseline-app.js`로 고정했다.
- `node 비공개 재현 자료/journey-late-scope-repro.mjs`: 위 결함들을 실제 제품 함수/전체 submit listener 추출로 재현했다. API, DOM 및 이벤트 순서의 경계는 최소 대역을 사용한다.
- `node 비공개 재현 자료/journey-late-scope-repro.mjs --fixed`: 수정 중인 작업 트리와 이후 고정된 `812469b`에서 각각 PM 응답 뒤 과거 scope 보존, 계획 확정 응답 뒤 과거 scope 보존, hashchange 전 render에서 새 project/run 보존, PM 요청 중 A→B→A 동일 URL 복귀 보존을 확인했다.
- `812469b`에서 `navigationGeneration`은 hashchange와 그래프의 pushState 선택에 증가한다. 초기 기본 scope는 routeMatches를 먼저 계산한 뒤 추가되므로 정상적인 초기 URL 고정과 경합 차단을 구분한다. 새 실제 API held-response 검사 코드는 이전 스냅샷으로 browser Back 후 성공 응답을 해제하고 URL·selector·원래 저장 결과를 확인하도록 되어 있다. 아직 그 브라우저 실행 결과는 확인하지 않았다.
- 이 재현은 브라우저 실행이나 실제 SQLite/API 검사의 대체물이 아니다. 당시 후속으로 요구한 실제 API held-response 회귀는 아래 최종 후보에서 별도로 통과했다.

## 검토 범위

- 저장소 `AGENTS.md`, `.agents/skills/ai-company-frontend/SKILL.md`, 사용자 명세 `git show 1d3b25e:docs/work-reviews/overnight-journey-integration-2026-09-24.md`, 전체 목표 대응표·통합 검증·적용/복구 문서를 읽었다.
- 코드 검수/단순 구현 보존 지침과, 발견된 응답 경합의 관련 경로 조사에 variant-analysis 지침을 적용했다.
- `journey-ui.js`의 project/plan/digest/run 결속, 원문 후보 SHA/subject digest 대조, 명시한 없는 대상의 최신 대상 대체 방지, 전체 요청 모드 구분을 읽었다.
- `app.js`, `manager-ui.js`, `execution-ui.js`, `report-ui.js`의 현재 계획/과거 계획 분리, 명세 저장/새 PM 요청/직접 확정 분리, 확정 응답의 원래 run/plan/digest 확인, PM 전송 불확실성 및 원문 초안 보존, 원문/한국어 전환 후 재확인과 기존 POST 필드 보존을 읽었다.
- `workspace-graph-ui.js`의 봉투 검증·cursor 순서·project/run별 선택과 카메라 보존, G1 기본 카메라·전체 보기, G2 이관/반환 경로의 장애물 회피·기존 경로/라벨 보존, G3 사용법·상세·스크롤·누락 기록·초점 코드를 읽었다.
- 기존 서버·승인/번역 권한 구현은 기준 대비 제품 변경 대상이 아니다. 새 UI가 원문 digest·실행 권한을 번역으로 생성하는 경로는 확인하지 못했다.
- 오프라인 패키지의 고정 fixture, 실제 JS 모듈 임베딩, GET 대역·비GET 거부, 외부 링크 제거, CSP connect/worker 차단 및 fixture `<` 이스케이프를 읽었다.
- 서비스워커 public shell 목록, 네트워크 우선 정책, API/POST 비캐시·비재생, 자동 skipWaiting 없음, v8→v9→v8 시험을 읽었다.
- 위 두 건 외에 구체적인 재현을 갖춘 추가 결함은 찾지 못했다. 전수 무결성 보장을 뜻하지 않는다.

## 제공된 동일 코드 증거 대조

증거 경로: `비공개 검증 자료/a31c8cf-evidence`.

- workspace, graph, preview, service-worker 검증 JSON은 PASS이고 Chrome `153.0.8010.52`, `sandbox:true`를 기록한다.
- workspace/graph의 DB 불변 JSON은 각각 임시 SQLite 업무 테이블 29개의 불변을 기록한다. 인증 테이블은 제외되며 모델·운영 접근은 없다고 명시한다.
- preview manifest의 `source_commit`은 `36d7eeeb80bf6ffd6107e4868dacfbeb25de003d`이다. manifest의 29개 원본 파일 SHA-256을 `git show a31c8cf:<file>`와 독립 대조해 모두 일치함을 확인했다. HTML SHA-256도 일치한다. SHA만 보고 source_commit을 a31c8cf로 바꿔 적지 않는다.
- D의 실제 API 쓰기 검사는 `synthetic proposal and actual specification registration` 단계에서 PM 요청 수 `1 !== 2`로 중단됐다. 이후 계획 검토/확정·후보 결정·메시지 불확실·외국 프로젝트 경계 검사를 통과한 것으로 세지 않는다.
- 캡처 네 장을 직접 열어 확인했다: Light 1440 그래프 초기, Black 320 그래프 초기, 모바일 누락 상세, Black 390 결과. 선택 대상·모의 표시·가독성·누락 안내/닫기·동일 실행 결과와 승인 분리 표시를 확인했다. 모든 캡처·키보드·브라우저 조작을 직접 수행한 독립 시각 검수로 확대하지 않는다.

## 남은 경계

P2 수정은 제품 `812469b`에 결속되고 최종 `d46136d`까지 유지됐다. D 전체와 최종 패키지·이미지 해시는 아래에서 확인했다. 전체 Console·Python 회귀는 통합 담당자의 원격 SUCCESS 조회 전달로 완료 상태를 확인했다. 이전 C 또는 a31c8cf의 일부 성공을 새 후보 전체 합격으로 재사용하지 않는다. 운영 적용, 사용자 APK 직접 계획 확정·승인, 실제 모델/worker 루프, 서명키·추가 계정·ECC 활성화는 미실시다.

## 812469b 실제 증거 후속 대조

`비공개 검증 자료/812469b-evidence`의 JSON을 직접 열었다. workspace/graph/preview/SW는 Chrome `152.0.7977.82`, sandbox 사용으로 PASS다. D 실패 JSON에는 실제 명세 등록·중복 저장·오래된 제안 거부와 **같은 manager 화면에서 browser Back으로 고른 snapshot을 held PM 성공 응답이 보존**한 검사가 성공한 것으로 기록돼 있다. 다음 `saved Korean reading and direct confirmation` 단계에서 `Korean main plan text is readable` 검사가 실패했고 page errors/외부 접근 위반은 비어 있다. 실제 계획 확정 응답 보존과 후반 후보 결정·메시지 불확실 검사는 아직 통과한 것으로 세지 않는다.

통합 담당자가 발견한 글자 크기 실패는 `6f10570`으로 수정됐다. 실제 `plan-ui.js`의 요약, 역할 책임 본문, 목표/완료조건 dd, 전체 완료조건 list에 새 CSS가 적용되고 기존 14px 요약 규칙보다 뒤에 있어 우선하는 것을 코드로 확인했다. 당시 요구한 새 모바일 줄바꿈·본문 크기·overflow 검사는 아래 최종 후보의 실제 브라우저 증거에서 통과했다.

## 최종 후보 결속과 직접 확인한 근거

### 제품·검사 커밋 구분

- 최종 제품: `d46136da9108608e7bdce6bdbfb0da35e825126a`.
- 검사를 추가한 현재 HEAD: `6a366e69909b204881cc1d0a547a7b5cd06173ee`.
- `git diff d46136d..6a366e6 -- src android deploy pyproject.toml uv.lock`는 비어 있다. 전체 커밋 차이는 `.github/workflows/journey.yml`과 `tests/ui/graph_edge_comparison.cjs`뿐이다. `scripts/package_journey_preview.py`와 공개 fixture에도 차이가 없다.
- 두 커밋의 `src/ai_company` Git tree는 모두 `293cc96e1776c23c8514c92e5959ba4bda918d81`이다.
- `812469b` 이후 제품 차이는 `manager.css` 네 줄이며 계획 본문 16px·행간 1.75, 대화상자 닫기 버튼 최소 44×44px이다. 새 API/권한/실행 엔진 변경은 없다.
- 최종 제품과 동일한 app.js에서 `비공개 재현 자료/journey-late-scope-repro.mjs --fixed`의 네 독립 경계 검사를 재실행해 통과했다.

### 최종 실제 API·브라우저 증거

`비공개 검증 자료/d46136d-evidence` 및 `비공개 검증 자료/6a366e6-evidence`의 workspace/graph/writes/preview/SW JSON을 직접 열어 모두 PASS임을 확인했다. Chrome `152.0.7977.82`, sandbox 사용이다. 제공된 원격 실행은 제품 커밋의 Journey `35905007920`, 동일 제품의 후속 Journey `35905379886`이다. run ID와 artifact 다운로드 연결은 통합 담당자의 원격 조회·전달을 사용했고, 이 검수자는 로컬 artifact의 내용과 제품 해시를 독립 확인했다.

두 지연 응답 검사는 `route.fetch()`로 실제 임시 API 저장을 완료한 뒤 응답만 보류하고 browser Back으로 같은 프로젝트의 다른 대상에 이동한다. 응답을 해제한 후:

1. PM 요청은 새 요청이 정확히 기록됐음을 확인하면서 이전 계획 snapshot URL·selector를 유지한다.
2. 계획 확정은 새 run이 원래 plan/digest로 한 번 저장됐음을 확인하면서 사용자가 선택한 이전 run/snapshot의 manager URL·selector를 유지한다.

D 후반까지 직접 확인한 JSON에는 원문/한국어 전환 시 재확인 해제, 원래 digest/spec/translation 직접 확정, 중복 확정 한 run, 대화 갱신 뒤 stale 거부, 같은 후보·원문 digest 결정과 멱등 재시도, 만료 후보 거부, 다른 프로젝트의 계획/후보 거부, CSRF/Origin/로그인 경계, 503·응답 유실 뒤 원문 초안 보존·자동 재전송 없음이 포함된다. PM·번역·후보 사실은 주입 fixture이며 실제 모델 결과가 아니다.

workspace/graph DB 기록은 인증을 제외한 업무 테이블 29개의 불변을 확인하며 모델 호출·운영 접근은 없다. 그래프 G1 측정은 Light/Black × 320/390/1440에서 중앙 오차 0 CSS px, 노드 표시 글자 14/17px다. G3 사용법·선택 상세·스크롤·초점·누락 기록 닫기는 실제 브라우저 기록과 검사 코드를 대조했다.

최종 d461 캡처 중 Light 320 계획 검토, Black 390 후보 결정, held PM 이후 계획, held confirmation 이후 이전 실행 계획 화면을 직접 열었다. 본문과 대상 선택 유지가 보이며, 모든 직접 조작을 이 검수자가 재실행했다는 뜻은 아니다.

### 같은 fixture의 전후 비교

`6a366e6-evidence/graph-baseline/workspace-edges-comparison-validation.json`은 renderer와 CSS 모두 기준 `75227480d27d3eb1cd57fa43a07a0ab986be3eb5`를 사용한다. 최근/과거 실행 × Light/Black × 320/390/1440의 전후 24개 기록에서 관계 ID가 쌍별로 모두 동일하고 PASS다. 비교 생성 코드는 제품 fixture에서 renderer와 CSS만 기준 버전으로 교체한다.

최근 실행 Light 1440의 전후 이미지를 직접 열었다. 주요 노드가 화면 가운데로 이동하고, 두 자기 이관선이 각 노드 가까운 짧은 점선으로 나타나며 반환선과 구별된다. 이는 확인한 특정 fixture의 시각 판단이다. 모든 그래프의 교차·혼잡이 없어졌다는 판정은 아니다.

### 공개할 미리보기 원본 결속

최종 공개 후보는 `6a366e6-evidence/AI-Company-journey.html`의 실제 CI 검사 파일이다.

- HTML SHA-256: `f00fd8a37e30f9c90221e0b0b327b2c6e024ff2c544670f4f9dfcffdb98141ff`.
- 공개 fixture SHA-256: `e84cad2748e99067635252184e8e04b3615b5f5eec175de87ad00bde95810d8c`.
- manifest `source_commit`: `b3d155c1100b5b1386e399e5becaab1b331fb6d9`, `source_dirty:false`.
- manifest의 29개 원본 파일 SHA-256을 `git show d46136d:<file>`와 모두 대조해 **불일치 0건**. HTML bytes와 manifest/preview 검사 SHA도 일치한다.

`source_commit`은 CI의 checkout/merge metadata이며 제품 커밋이나 PR head와 동일하다고 바꿔 적지 않는다. 제품 bytes 동등성은 위 파일 해시와 tree 대조로 확인했다. 앞선 d461 CI artifact의 source_commit은 `e73136ec7a76ba7d6f173c7dcbe4dfd0cc00984e`, HTML SHA는 `ef7dff564ecffc8f009389334d6614011d639a6616769295753a2f8ee9f6115c`이고 그 29개 원본 해시도 모두 최종 제품과 일치한다. metadata 차이로 HTML hash가 달라지는 두 파일을 같은 파일이라고 하지 않는다.

오프라인 검사에는 외부 네트워크 요청 0, 비GET/외부 fetch 거부, fixture 불변, 읽기용 표시가 포함된다. CSP와 대역은 실제 운영 권한이나 모델 실행을 제공하지 않는다.

### 이미지와 격리 HTTP 기록

- 이미지: `sha256:8ec249bbf7a457f2d6187c3e1ce8add91c0d44fe669a2ce61cc28ffa4e74159b`.
- `비공개 검증 자료/image-build-d46136d.log`의 manifest-list 출력과 제공된 이미지 식별자가 일치한다.
- `image-check/source-manifest.json`의 114개 파일을 `git show d46136d:src/ai_company/<file>`와 독립 대조해 **불일치 0건**.
- source manifest SHA-256: `6096acefa7817281d63345b58a52a8b9ff4cb1753cbb18d4bda934938444fb43`; 검사 JSON의 값과 일치한다.
- `image-d46136d-validation.json`은 설치된 114개 파일, 주요 HTTP 자원 bytes/no-store, 익명 API 401, 다른 Host 403, shell v9를 PASS로 기록한다. 검사 스크립트도 읽어 assertion을 대조했다. 기록된 격리는 uid 65532·네트워크 none/loopback·읽기 전용 root·tmpfs state·운영 mount 없음이다. 이 검수자가 컨테이너를 다시 구동한 것은 아니다.

### 전체 회귀 상태와 판정 한계

- 동일 제품 후속 Journey `35905379886`: artifact 직접 대조 PASS.
- Python `35905379824`: 통합 담당자의 원격 조회로 PASS 전달받음.
- Console `35905379779`: 통합 담당자가 원격 조회로 전체 step SUCCESS를 확인했다고 전달함. 이 검수자는 전체 로그를 재열람하지 않았다. 전달된 `console-screenshots` artifact ID는 `10771117656`, SHA-256은 `a7ee025cbc41ed43ff2fbd50e2cfbb63b0ce2e9d7600fe2438e100a9f233dcb3`이며, 이 검수자가 해당 다운로드 bytes나 모든 캡처를 대조한 것은 아니다.

검수 범위에는 실제 모델/PM/worker 운영 루프, 사용자의 APK 직접 확정·후보 수용, 실제 운영 서비스워커·도메인·서명키, 병합·배포가 포함되지 않는다. 이 항목들의 미실시는 이번 독립 코드 PASS와 함께 유지한다.

공개본에서는 비공개 임시 경로만 자료 이름으로 치환했다. 원문은 서버 비공개 작업 기록에 보존한다. 이 디렉터리에는 최종 대표 JSON·캡처를 선별했고 전체 자료는 연결한 CI artifact에 있다.
