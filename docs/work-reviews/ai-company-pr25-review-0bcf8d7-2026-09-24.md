# PR #25 재검수 — 0bcf8d7

2026-09-24 KST. Work 검수자. 구현은 서버 Codex에서 수행한다.

**판정: R25-2 해결, R25-1 최초 부팅 결함 해결. 같은 갱신 원인의 R25-1b(P2) 한 건은 보완 필요.** 인증 전 공개 화면의 부팅 성공을 인증 후 프로젝트 화면의 혼합 버전 호환성으로 확대하지 않는다. 기존 통과 내용과 최초 실패 기록은 유지한다.

## 대상

- [PR #25](https://github.com/HyungwonPark/ai-company/pull/25): open/draft, 미병합.
- 제품·검사 `14114a71584c65c6dbbd0b33a1a26c7246d1e1ad`; 게시 `0bcf8d7e93f2d1bab446503ab9da0e080182bd94`.
- 두 커밋 사이 변경은 문서·증거·미리보기이며 제품·테스트·workflow·배포 코드 차이는 없다.
- 기준은 [이전 검수](ai-company-pr25-review-dbd868b-2026-09-24.md)와 원래 [A~E 명세](overnight-journey-integration-2026-09-24.md)다.
- 저장소 AGENTS와 ai-company-frontend 지침을 읽고, 상태 분류와 갱신 호환성을 나눠 검수했다. Work는 제품 코드·운영 서비스를 수정하지 않았다.

## 해결 확인과 근거

| 항목 | 이번 판정 |
| --- | --- |
| R25-1 최초 원인 | 신규 journey-ui.js 정적 import를 없애고 기존 app.js에 순수 여정 함수를 포함했다. 새 report named export에 의존하지 않으며 구 report의 execution 부재 안내도 있다. 이 부팅 경계는 해결 |
| R25-1 실제 Chrome | 원래 실패 run과 최종 성공 로그·JSON 대조. v8 active/v10 waiting의 부분·전체 갱신, v10 active/v8 waiting 복구에서 제어/대기 client 2/0과 app/report SHA를 확인하는 검사가 있다. HTTP cache 비활성, API/POST 비캐시·비재생 유지 |
| R25-2 원래 재현 | 수정 전 완료 0/진행·대기 4 → 수정 후 완료 1/확인 필요 3. rejected는 상태 미확인→반려됨. 실제 제품 함수의 독립 전후 실행으로 확인 |
| 관련 상태 | 미등록 WAITING 상태, 운영자 확인 우선순위, 다른 project/plan/digest/run 제외, current_plan 종합·PM 중첩 상태·예약 없는 선택기 대기를 확인. 원문·digest·승인 값은 변경하지 않음 |
| 직접 JS 검증 | 상태 26사례와 종합 4사례, 식별자/원문 60사례, 지연 응답 4사례 및 manager/명세 저장 회귀 통과. 별도 원래→수정 함수 대조와 실제 scopeName 실행도 통과 |
| CI | 제품과 게시 커밋 각각 Python·Console·Journey 총 6개 run 모두 attempt 1 success를 API로 조회. 게시 Python 3.11/3.12 로그 각각 467개 실행·3 skipped, Android 준비 4개 및 DEMO_READY 확인 |
| 미리보기 | ZIP·HTML 해시 직접 계산 일치. manifest의 28개 원본을 고정 Git blob/SHA-256과 대조하고 정확히 동일한 HTML을 재생성 |
| 캡처 | 최종 320 Light 완료/확인 필요, 390 Black 미확인·후속 담당 대조, 부분 갱신 오프라인 로그인 PNG 4장 직접 열람. Work의 직접 브라우저/APK 조작과 구별 |

고정 로그:

- [원래 두 결함의 Chrome 실패](https://github.com/HyungwonPark/ai-company/actions/runs/35931537172): e49ad40 검사 추가 후보. MERGE_READY 분류 assertion과 SW 로그인 폼 대기 실패를 로그에서도 확인.
- 제품: [Python](https://github.com/HyungwonPark/ai-company/actions/runs/35933619222) · [Console](https://github.com/HyungwonPark/ai-company/actions/runs/35933619242) · [Journey](https://github.com/HyungwonPark/ai-company/actions/runs/35933619143).
- 게시: [Python](https://github.com/HyungwonPark/ai-company/actions/runs/35934519921) · [Console](https://github.com/HyungwonPark/ai-company/actions/runs/35934519581) · [Journey](https://github.com/HyungwonPark/ai-company/actions/runs/35934519742).
- 제품 Journey checkout `82c794cd2d511950377a3caef71670e55877dc56`, 게시 checkout `368dbc24af0ff396b00a8b6d9c329e72899e87e8`는 PR 합성 merge다. 제품 head와 혼동하지 않는다.

미리보기 ZIP SHA-256 `4d3916a4564b99a7dd9d1fe40f9664501cc1e058a72c4d2abd22ac1bd9e52812`, HTML `606e68539fead877274cd16173f62311614dde87bcfb5413664fb9f7141a4417`. 재생성에는 공개 원본 bytes를 사용하고 Git checkout/clean 메타데이터만 CI manifest 값으로 제공했다. 실제 운영 데이터·계정·모델 호출은 포함하지 않는다.

## R25-1b — P2: 구버전 그래프와 새 앱이 섞이면 인증 후 프로젝트 화면 오류

**영향:** 새 앱의 프로젝트 조회·선택·화면 표시가 예외로 중단될 수 있다. 서버 worker나 저장된 실행 자체가 중단됐다는 주장은 아니다. 운영에서 실제 발생했다는 보고도 아니다.

확인된 코드:

- [새 app.js](https://github.com/HyungwonPark/ai-company/blob/0bcf8d7e93f2d1bab446503ab9da0e080182bd94/src/ai_company/web/app.js) 121행 `selectedScope()`는 `workspaceGraph.getScope()`, 125행 `syncScope()`는 `workspaceGraph.selectScope()`를 조건 없이 호출한다.
- 기준 [7522748 graph factory](https://github.com/HyungwonPark/ai-company/blob/75227480d27d3eb1cd57fa43a07a0ab986be3eb5/src/ai_company/web/workspace-graph-ui.js) 389행의 반환 API에는 두 메서드가 없다.
- 새 앱 292행 render→syncScope, 317행 overview 조회 후 render 경로로 도달한다. 319행 오류 처리도 render를 다시 호출하여 같은 예외를 낼 수 있다.
- 기존 v8 서비스워커는 공개 파일을 개별 network-first/cache fallback으로 반환한다. app 파일은 새 버전, graph 파일은 기존 캐시로 반환되는 조합이 가능하다.

재현 조건:

1. 기존 v8이 제어하는 탭에 구 graph 캐시가 있다.
2. 새 앱 배포 이후 app.js 요청은 성공한다.
3. workspace-graph-ui.js 요청만 일시 실패해 v8이 구 캐시를 반환한다.
4. 인증/프로젝트 API는 정상 응답한다. 완전 오프라인으로 인증 화면에 머무는 사례와 다르다.
5. 새 app의 실제 함수에 구 graph factory를 연결하면 아래 예외가 발생한다.

```text
TypeError: workspaceGraph.getScope is not a function
TypeError: workspaceGraph.selectScope is not a function
```

동일 호출에 새 graph factory를 연결하면 두 함수 모두 통과한다.

| 재현 입력 | SHA-256 |
| --- | --- |
| 새 app | f048a9d5f5826ccb8c40868e81256c3a652c0f3e62c3ac41196f1c7e563a0450 |
| 구 graph | 8819bcb7bab374b71ad9037c8a8a0248f764f582ee4efecd432c24689d3643b5 |
| 새 graph | 2706d60422eb9b1de99239762f9004afd2cd70ec80603f3ec225ee8f91a762eb |

[읽기 전용 재현](repro/pr25-mixed-graph-0bcf8d7.mjs)은 고정 Git의 v8 fetch 처리, 구·신 graph factory, 새 앱의 실제 함수를 실행한다. graph 경로만 실패하는 모사 네트워크에서 새 app/구 graph/정상 인증 응답이 함께 성립하는지도 검사한다. 주 검수자는 GitHub에서 원문을 별도로 가져와 입력 bytes를 대조하고 재실행했다. **Node VM 재현이며 실제 Chrome·APK에서 해당 새 경계를 직접 조작한 결과는 아니다.**

현재 [SW Chrome 검사](https://github.com/HyungwonPark/ai-company/blob/0bcf8d7e93f2d1bab446503ab9da0e080182bd94/tests/ui/journey_service_worker.cjs)는 부분 실패 대상으로 report-ui를 사용하고 세션은 `authenticated:false`다. 공개 로그인 shell 검사에는 새 그래프 메서드 호출이 도달하지 않으므로, 기존 PASS는 이 결함과 모순되지 않는다.

## 최소 수정·수용 조건

1. 기존 graph API와 새 앱이 섞여도 처리되지 않은 예외가 발생하지 않게 한다. 최소 하위 호환 또는 안전한 갱신 안내를 사용하고 전체 캐시 구조 재설계는 필요 조건으로 삼지 않는다.
2. 필요한 graph 기능이 없으면 다른 실행·현재 계획의 데이터를 대신 표시하지 않는다. 조회할 프로젝트/실행 URL과 입력을 보존하고 사용자가 정상 화면으로 회복할 수 있는 행동을 제공한다. 내부 메서드명은 사용자가 읽는 오류 안내로 노출하지 않는다.
3. 실제 격리 Chrome에서 v8 기존 탭·신 worker waiting을 유지하고, 새 app 수신+graph 요청만 실패+정상 인증/프로젝트 API를 구성한다. 정확한 파일 bytes와 controller/waiting을 확인한다.
4. 프로젝트→같은 실행의 진행·결과·승인 탐색 또는 명시적인 안전한 갱신 안내가 가능해야 한다. console의 처리되지 않은 TypeError, 오류 처리의 반복 예외, 잘못된 실행 표시가 없어야 한다.
5. 혼합 상태에서는 업무 쓰기를 새로 만들지 않는다. 원래 제출 중 요청·초안·멱등 키와 기존 승인 경계를 유지한다. 자동 새로고침/재전송으로 입력이나 요청을 잃지 않는다.
6. 새 app+구 report/graph, 구 app+새 모듈의 영향받는 조합을 필요한 범위에서 대조하고, 이미 통과한 부분/전체 갱신·복구·API/POST 보호와 R25-2 상태 회귀를 유지한다.
7. 원래 후보에서 같은 브라우저 검사가 실패하고 수정 후보에서 성공함을 남긴다. 만약 브라우저에서 조건이 성립하지 않는다면 실제 제어자·bytes·캐시/네트워크 응답으로 Node 재현의 달라진 전제를 밝힌다.

이 수용 조건은 시험용 임시 인증/API/DB에 적용한다. 운영 계정·DB나 마스터의 실제 계획 확정을 대신 사용할 필요가 없다.

## 검사 기대값 정정과 남는 한계

v8 인증 전 로그인 암호가 offline 이벤트의 재렌더에서 비워지는 것은 기준 코드의 기존 동작이다. 대기 worker 설치만으로 입력을 지우지 않는 검사와 offline 이후 암호 초기화/POST 0 검사를 나눈 것은 타당하다. PM 업무 초안·결과 불확실 저장 검사는 별도 D에 남아 있으므로 이것을 모든 입력 보존의 실패나 성공으로 확대하지 않는다.

모바일의 반복 구획·긴 결과 보고와 캡처에 보이는 초점된 건너뛰기 링크·일시 알림은 남는 사용성 한계다. 이번 R25-1b 해결 조건으로 전면 디자인 개편을 추가하지 않는다. 기존 순서형 선택은 유지한다.

## 서버 Codex 다음 지시

- R25-2와 원래 모듈 부팅 결함은 해결로 기록한다. PR #25와 기존 증거를 보존하고 **R25-1b와 같은 원인의 혼합 모듈 호환성**만 보완한다.
- 최초 실패→수정 성공, 실제 인증된 임시 API의 격리 Chrome 검사, 영향 회귀와 독립 검수를 같은 최종 후보에 묶는다.
- 제품 변경 후 이미지·미리보기·manifest·해시·CI·적용/복구안을 다시 고정한다. 현재 이미지 `sha256:8005ce1fa85a458d17bdf159a0af2774801a9a19b4c14adecff147b1f059ae01`은 이번 후보의 로컬 Docker ID이며 수정 후 후보를 대신하지 않는다.
- 단계를 진행할 때마다 계속 여부를 묻지 않고 기존 한도 내에서 마무리한다. 운영 배포·병합·PR #10 pending 변경·worker/DB/서명키·추가 모델 연결은 하지 않는다.

Work는 이번에 실제 서버 이미지·운영 도메인·계정·모델 실행·APK·서명키 복원을 검사하지 않았다. 새 후보의 운영 적용 승인은 아직 별도다. 후속 보완을 제외한 기존 합격 항목을 초기화하거나 원래 A~E 구현 전체를 반복하지 않는다.

[최종 수정일: 2026-09-24, v1.0]
