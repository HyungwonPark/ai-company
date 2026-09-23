# 순서형 작업실 통합 검증

2026-09-24 KST. [초안 PR #25](https://github.com/HyungwonPark/ai-company/pull/25). **비운영 제품 연결·검수·패키징 작업이다.** 운영 배포·병합·PR #10 결정·서명키 작업은 수행하지 않는다.

기준 명세는 PR #23 `1d3b25ec41f9558a19a8d8a55fdb1f6682cc960a`의 [야간 지시](https://github.com/HyungwonPark/ai-company/blob/1d3b25ec41f9558a19a8d8a55fdb1f6682cc960a/docs/work-reviews/overnight-journey-integration-2026-09-24.md), 연결 계획은 PR #24 `75227480d27d3eb1cd57fa43a07a0ab986be3eb5`다. PR #25는 PR #24 위에 쌓은 별도 브랜치 `feat/journey-product-integration`이다. 기존 PR #21/22/24와 별도 작업 공간을 보존했다.

## 최종 판정과 후보

**A·B → C → D → E 비운영 구현·검수·수정·패키징 완료.** 조건을 통과한 뒤 다음 단계로 진행했고, 발견한 결함의 실패 기록을 보존했다. 아래 고정 제품에 Python·기존 Console·새 Journey CI와 독립 코드·화면 검수를 연결했다. 운영 적용과 실제 마스터/모델/APK 전체 실행 완료를 뜻하지 않는다.

| 대상 | 고정 값 |
| --- | --- |
| 제품 코드 | `d46136da9108608e7bdce6bdbfb0da35e825126a` |
| 최종 검사 코드 | `6a366e69909b204881cc1d0a547a7b5cd06173ee` — 제품 변경 없이 기준 `7522748`의 JS/CSS 전후 비교 추가 |
| 제품 tree | `293cc96e1776c23c8514c92e5959ba4bda918d81` — 두 커밋의 `src/ai_company` 일치 |
| 웹 이미지 | `sha256:8ec249bbf7a457f2d6187c3e1ce8add91c0d44fe669a2ce61cc28ffa4e74159b` — arm64, 로컬 Docker image ID이며 공개 registry pull 주소 아님 |
| 서비스워커 | `ai-company-shell-v9`; API/POST 비캐시·비재생 |
| ZIP SHA-256 | `10467c136710cdbc135bf0c465b0b7467798af97de186a30d41f2a608d238b44` |
| HTML SHA-256 | `f00fd8a37e30f9c90221e0b0b327b2c6e024ff2c544670f4f9dfcffdb98141ff` |

[미리보기 ZIP](../previews/journey/AI-Company-journey-preview.zip) · [열기 안내·확인법](../previews/journey/README.md) · [파일 해시](../previews/journey/SHA256SUMS) · [기계 판독 후보 기록](../evidence/journey-integration/candidate.json).

제품 `d46136d` 이후 이 문서·선별 증거·ZIP을 게시하는 커밋은 기록 전용이다. 마지막 운영 기록 `2087ecc` 대비 제품 변경은 웹의 10개 JS/CSS 파일뿐이며, 웹 밖 `src`·`android`·`deploy`·`pyproject.toml`·`uv.lock` 차이는 없다. worker·DB·카탈로그 교체가 필요 없는 후보임을 코드 대조로 확인했다. 현재 운영 상태를 재조회한 주장은 아니다.

## 최종 CI·독립 검수

모두 검사 커밋 `6a366e6`, attempt **1**의 결과다. 앞선 실패 run을 새 성공으로 덮지 않았다.

| 검사 | 결과·범위 |
| --- | --- |
| [Python CI](https://github.com/HyungwonPark/ai-company/actions/runs/35905379824) | PASS. Python 3.11/3.12 각각 467개 실행·3개 skipped, Android 준비 4개 및 demo 통과. 준비 검사는 APK 전달·실기기 성공이 아님 |
| [Console CI](https://github.com/HyungwonPark/ai-company/actions/runs/35905379779) | PASS. PR22 R1~R5/F1~F2, 기존 UI/관리 API·독립 그래프·실제 1px 왕복·터치·고정 그림/긴 이름·독립 그림 검수 |
| [Journey CI](https://github.com/HyungwonPark/ai-company/actions/runs/35905379886) | PASS. 실제 임시 API 읽기/C·쓰기/D·G1~G3·752 기준 전후 비교·패키지·SW 갱신/복구 |
| [조건부 Automation evidence](https://github.com/HyungwonPark/ai-company/actions/runs/35905379766) | skipped. 실제 모델 개발 루프나 그 CI 출처 검증 완료로 세지 않음 |
| [독립 코드 검수](../evidence/journey-integration/independent-code-review.md) | PASS. 도구 설정으로 요청한 Astra/Ultra 검수. 작성자와 별도 담당자가 P2 두 건 재현→수정 확인, 동일 제품·실제 API 기록·미리보기 29개와 이미지 114개 소스 해시 대조. 운영 제품의 Astra 최종 루프와 구별 |
| [독립 화면·조작 근거 검수](../evidence/journey-integration/independent-visual-review.md) | PASS. 최종 PNG 11장 직접 열람과 실제 Chrome 검사 코드/JSON 대조. 검수자 직접 브라우저/APK 조작은 아니며 초보 사용자 관찰도 아님 |

Journey workflow는 `.github/workflows/journey.yml`, workflow ID `365380671`, suite `97227355318`에서 실행됐다. artifact `journey-evidence` ID `10770523110`, GitHub archive digest `sha256:3aa830d4a803d6b6da948fa53bc42e0598f65dcd49e36b0c37d933304d1779d3`다. Console artifact `console-screenshots` ID `10771117656`, digest `sha256:a7ee025cbc41ed43ff2fbd50e2cfbb63b0ce2e9d7600fe2438e100a9f233dcb3`다. artifact 전체는 GitHub 보존기간·로그인 조건이 있으며 대표 근거와 ZIP은 저장소에도 보존했다.

미리보기 HTML은 **이 Journey artifact의 실제 검사 파일 그대로**다. manifest의 checkout `b3d155c1100b5b1386e399e5becaab1b331fb6d9`는 PR 합성 merge 커밋이며 제품·PR head로 바꿔 적지 않는다. 29개 원본 파일을 최종 제품 Git blob과 대조해 전부 일치했다. 이미지에는 같은 제품 114개 파일이 설치됐고, 네트워크 없음·UID 65532·읽기 전용 root·capability 제거·임시 tmpfs 상태에서 HTTP 자원/no-store·익명 API 401·다른 Host 403을 확인했다. [이미지 검사](../evidence/journey-integration/image-validation.json)와 [원본 manifest](../evidence/journey-integration/image-source-manifest.json)는 운영 health 검사가 아니다.

## 대표 화면과 조작 기록

| 화면 | 실제 캡처 |
| --- | --- |
| 계획 검토 | [390 Light](../evidence/journey-integration/screens/journey-write-plan-review-light-390.png) · [320 Black](../evidence/journey-integration/screens/journey-write-plan-review-black-320.png) · [1440 Light](../evidence/journey-integration/screens/journey-write-plan-review-light-1440.png) |
| 후보 결정 | [320 Light](../evidence/journey-integration/screens/journey-write-candidate-review-light-320.png) · [390 Black](../evidence/journey-integration/screens/journey-write-candidate-review-black-390.png) |
| 그래프 | [390 Light](../evidence/journey-integration/screens/journey-graph-light-390-initial.png) · [1440 Light](../evidence/journey-integration/screens/journey-graph-light-1440-initial.png) |
| 같은 조건 전후 | [7522748 이전](../evidence/journey-integration/screens/workspace-edges-before-recent-light-390.png) · [최종 후보](../evidence/journey-integration/screens/workspace-edges-after-recent-light-390.png) |
| 실제 1px 왕복 | [+8px](../evidence/journey-integration/screens/workspace-drag-after-light-1440-plus8.png) · [+9px](../evidence/journey-integration/screens/workspace-drag-after-light-1440-plus9.png) · [측정값](../evidence/journey-integration/workspace-drag-stability-validation.json) |

최종 Chrome `152.0.7977.82`, sandbox `true`. Light·Black × 320/390/1440px에서 명세·계획·후보 18개 화면을 검사했다. 계획 본문 16px 이상, 모달 닫기 44×44px 이상을 실제 DOM에서 확인했다. 200% 항목은 CSS viewport 재배치 상당 시험이며 물리 기기의 확대 조작이 아니다. 기존 드래그 검사는 실제 포인터 제스처의 문턱을 넘긴 뒤 +8/+9px 왕복의 경로·이름표 변화가 1.02px 이하임을 확인한다. 장애물이 바뀔 때의 재탐색은 별도 검사다.

[증거 목록](../evidence/journey-integration/README.md)에 실제 API·쓰기/권한·DB 불변·그래프·패키지·서비스워커·기존 그림 검증을 나눴다. 서비스워커 v8→v9 대기/활성화→v8 복구는 격리 localhost 출처에서만 시행했다. 운영 도메인·APK 캐시를 비우거나 갱신하지 않았다.

## 구현과 책임

| 묶음 | 구현 | 보존한 계약 |
| --- | --- | --- |
| A | 프로젝트→계획→진행→승인→결과, 현재 계획과 과거 실행의 계획 구분, 같은 실행 URL·결과·요청 | 없는 실행을 최신 실행으로 대체하지 않음. source plan/digest/run/후보 SHA 대조 |
| B | 주요 노드 중심의 초기 화면, 폭 변경 기본 카메라 보정, 가까운 자기 이관/반환, 사용법·상세·초점 | 사용자가 옮긴 카메라 유지, 유효 경로/라벨 유지, 실제 장애물만 재탐색, 원본 관계 보존 |
| C | 실제 임시 API/SQLite 읽기·지연 응답·오프라인·401·모바일·키보드 검수 | 로그인 요청과 업무 쓰기를 분리. 29개 업무 테이블·실행·승인·사용량 불변 |
| D | 현재 계획 안에서 범위 확인·명세 저장·새 계획 요청·원문/한국어 검토·직접 확정·후보 결정 | 기존 POST 필드·digest·버전·멱등·만료·CSRF/Origin 정책. 전송 불확실 시 자동 재전송 금지 |
| E | 최종 코드와 미리보기·이미지·CI·독립 검수 결속, v8→v9→v8 시험 | public shell만 캐시, API/POST 비캐시·비재생. APK 실기기/운영 확인과 구분 |

A/D는 같은 UI 파일 담당자가 순차 수정했다. B는 그래프 JS/CSS만, 검수 담당은 독립 시험 파일만 수정했다. root는 워크플로·기존 회귀 호환·서비스워커 시험·패키징/기록을 통합했다. 구현 작성자와 코드/화면 검수 담당을 구분했다. 서버 2 CPU 환경에서 무거운 로컬 빌드·브라우저를 병렬로 과다 실행하지 않았고, 실제 브라우저는 기존 GitHub 격리 Chrome을 사용했다. 운영 flow의 최대 두 실행 설정은 변경하지 않았다.

## 실제 검증과 모의 사실의 경계

브라우저는 sandbox를 켠 Chrome에서 실제 화면을 렌더링하고 클릭·드래그·키보드·뒤로가기·갱신을 수행한다. `run_journey_workspace.py`와 `run_journey_writes.py`는 매번 새 임시 SQLite와 실제 `ManagementHTTPServer`를 사용한다. PM 응답·번역·검수/후보 사실은 명시적인 시험 자료이며, 제품 worker나 모델을 호출하지 않는다. 계획 확정·후보 결정은 임시 가상 사용자의 실제 API 검사이고 마스터의 운영 승인이나 PR #10 결정이 아니다.

운영 DB/개인 대화/승인 원문을 미리보기에 복사하지 않는다. `docs/previews/journey/fixture.json`은 기존 시험 seed에서 생성한 공개용 읽기 투영이다. 평상시 패키징은 이 고정 파일만 읽는다. self-contained HTML은 실제 제품의 9개 JS 모듈을 유지하며 GET 응답만 고정 자료로 대체한다. 저장·모델·실제 승인·외부 HTTP·서비스워커는 미리보기에서 실행하지 않는다. 별도 그림과 외부 문서는 링크를 비활성화한다.

원본 그래프 fixture SHA-256은 `ac970aba4ccf4abe47bd862da581cdb2b412a0cd7ca6fb98b5d9e13b798b25ba`로 유지한다. 확장된 오프라인 fixture SHA-256은 `e84cad2748e99067635252184e8e04b3615b5f5eec175de87ad00bde95810d8c`다.

## 그래프 전후와 통합 수용 기준

동일 최근 실행 fixture와 PC/모바일 기본 배치의 맨해튼 길이·꺾임 수다. 브라우저 화살표의 픽셀 곡선 길이와 구별한다.

| 관계 | PC 이전→후보 | 모바일 이전→후보 | 후보 꺾임 |
| --- | --- | --- | --- |
| 검사 자기 이관 | 985→128 | 749→128 | 2 |
| 개발 자기 이관 | 581→115 | 581→115 | 2 |
| 독립 검수→개발 반환 | 826→706 | 767→647 | 2 |

경로 `issue`는 null이다. 38개 저장 관계와 새 관계 경계, 480개 고정 seed 배치에서 관계·라벨·입력 불변을 확인한다. 모든 교차가 없어졌다는 의미는 아니다. 기존 +8↔+9 px 검사는 같은 실제 포인터 제스처 안에서 시작 문턱을 넘은 뒤 왕복·release·실제 장애물 회피를 구분한다.

C 후보 `9954518`의 [Journey UI](https://github.com/HyungwonPark/ai-company/actions/runs/35902041885)는 읽기·그래프·서비스워커 모두 통과했다. 주요 노드 중심 오차 0 CSS px, 표시 글자 14/17px, Light·Black × 320/390/1440px를 실제 측정했다. 이전 C 후보의 통과를 최종 D/E 후보 검증으로 대신하지 않는다.

## 실패·수정 이력

| 후보 | 관측된 실패 | 처리 |
| --- | --- | --- |
| `787c735` | 새 Journey workflow가 runner context를 job env에서 사용하여 실행 전 거부 | `c5523d4`에서 setup의 GITHUB_ENV로 이동. 검사 전 실패이며 테스트 PASS로 세지 않음 |
| `787c735` | 넓은 화면→모바일에서 이전 카메라 폭이 남음 | 사용자 조작 여부와 실제 측정 폭을 실행별로 보존하고 기본 카메라만 재중앙화 |
| `787c735` | 제거된 refresh CSS 선택자, legacy를 새 5단계로 잘못 기대, 탐색 시 run이 지워지길 기대 | 실제 새 선택자·명시 run 보존·기존 legacy 계약에 맞춰 회귀 보정 |
| `c5523d4` | 빈 management_diagrams 스키마가 before 캡처 뒤 초기화되어 DB 변경으로 오판 | 서버와 같은 초기화를 캡처 전에 수행. 테이블 자체는 계속 불변 검사 |
| `c5523d4` | online 자동 조회가 먼저 401 처리해 클릭할 새로고침 버튼이 사라짐 | 실제 401 응답을 기다리고 로그인·사적 상태 제거를 검증 |
| `c5523d4` | async waitForFunction의 Promise가 먼저 truthy가 되어 SW 상태 대기를 끝냄 | 해결된 값을 기다리는 제한시간 내 polling. 정책·시간 상한 유지 |
| `9954518` | legacy 탐색에 순서 번호가 섞임, 즉시 화면+프로젝트 변경 시 이전 view 사용 | legacy 번호 제외, hash에서 요청된 view를 즉시 읽어 전환 |
| `9954518` | 기존 offline 시험이 삭제된 명시 snapshot을 새 snapshot으로 자동 대체하길 기대 | 없는 대상 안내와 원래 run 보존, 사용자의 새 대상 선택 후 복구 검사로 연결 |

`a31c8cf`에서는 읽기·그래프·미리보기·SW가 통과했지만, 인라인으로 옮긴 PM 요청 시험이 이미 있는 폼의 존재만 기다려 저장 직전 건수를 읽었다. 실제 POST 완료·저장 메시지 ID까지 기다리도록 보정했다. 기존 smoke도 URL 변경 후 hashchange의 DOM 반영을 기다리도록 보정했다.

독립 Astra Ultra 검수는 같은 화면에서 다른 실행을 고른 뒤 도착한 쓰기 응답의 자동 이동, hashchange 전에 render가 대기 URL을 덮는 P2 두 건을 발견했다. `812469b`는 정확한 제출 URL과 탐색 순번을 함께 고정하고 render의 원래 대상 일치 조건을 복원한다. 다른 곳으로 이동했다가 같은 URL로 돌아온 경우도 사용자의 선택을 보존한다. 독립 재현의 네 경계와 실제 보류 API 두 경로를 회귀로 연결했다.

`812469b`의 실제 보류 PM 요청은 선택을 보존했지만 다음 계획 검토의 16px 본문 기준에서 실패했다. `6f10570`은 요약·역할 설명·완료 조건을 16px로 표시하며 권한 데이터에는 영향을 주지 않는다.

`6f10570`의 다음 D 검사에서 후보 버튼 선택자가 숨겨진 form도 함께 골랐다. `d46136d`는 버튼을 명시한 검사로 수정하고, 별도 시각 검수가 발견한 닫기 버튼 36px 덮어쓰기를 44px로 복원했다. 최종 D에서 원문/한국어 전환·직접 확정·후보 결정·응답 유실·두 지연 성공 응답·권한 거부까지 모두 통과했다. 이미지 초기 시험의 404는 harness가 실제 CLI의 web root를 전달하지 않아 발생했고, 설치된 웹 경로로 시험을 보정한 뒤 파일·HTTP 검사를 통과했다.

동일 코드의 무근거 재실행으로 실패를 지우지 않는다. 각 원인·수정과 최종 후보의 CI를 연결한다. Automation evidence의 조건부 skipped는 실제 개발 루프·원격 CI 출처 통과로 세지 않는다.

## 적용한 지침

| 지침 | 이번 적용 | 증거 수준 |
| --- | --- | --- |
| ai-company-frontend | 확정한 순서형 재사용, 한국어·Light/Black·폭·핵심 조작 및 독립 검수 | 이번 개발 세션의 작업 지침. 운영 worker 탑재/준수 증거 아님 |
| ponytail | 기존 모듈·API·상태 계약 재사용, 새 프레임워크/엔진 불필요 | 변경 파일·의존성 대조 |
| web-design-guidelines | 키보드·초점·터치·한국어·줄바꿈·오류/대기 동선 | 렌더링/조작 결과. 접근성 인증 아님 |
| property-based-testing | 고정 seed의 URL·식별자 결속, 배치·이벤트 불변 조건 | 실제 JS 입력 생성 검사. 라이브러리 추가 없음 |
| variant-analysis | scope 전파·늦은 응답·과거 집계·async 대기 오류의 관련 경로 대조 | 발견 원인의 영향 범위 조사 |
| gh-fix-ci | 커밋·run·실패 로그 구분 후 수정 | 새 후보 CI. 운영 권한 확대 없음 |

## 후속 경계

[전체 목표 대응표](journey-overall-goal-map-2026-09-24.md)에서 과거 실제 모델·도메인·APK·복구 근거를 보존하고 현재 미확인을 구분한다. [적용·복구안](journey-integration-rollout-2026-09-24.md)의 후속 범위는 웹 1개다. worker/DB 새 계약, 새 모델·ECC·서명키는 이번 후보에 없다.

서버 Codex 세션의 자동 재개는 확인되지 않았다. 제품 내부 큐의 예약 재개와 혼동하지 않는다. 운영 타이머를 바꾸지 않고 단계·후보·실패 근거를 체크포인트로 기록한다. 사용자 직접 APK 확정·서명키 외부 복원은 별도 미실시이며 이번 비운영 개발의 대기 조건이 아니다.

최종 검수의 비차단 UX 한계는 모바일의 중복 진행 제목/대상 선택기, 초기 100% 그래프의 외곽 관계 일부가 첫 화면 밖인 점, 긴 결과 보고다. 전체 보기·이동·목록으로 접근할 수 있으며 새 차단 결함은 남아 있지 않다. 초보 사용자의 이해 속도·실수율은 실제 사용자 확인 전 미측정이다.

## 종료와 정확한 후속 기준

이번 명세의 비운영 완료 기준을 충족해 종료한다. 자동 운영 적용이나 추가 개발을 예약하지 않았다. 후속 검토 시 `feat/journey-product-integration`/PR #25의 제품 `d46136d`, 검사 `6a366e6`, 이 문서를 묶은 기록 커밋을 사용한다. `git diff d46136d 6a366e6 -- src android deploy pyproject.toml uv.lock`는 비어 있어야 하며, 후속 기록 커밋도 같은 제품 tree를 가져야 한다. ZIP은 위 해시 그대로 사용한다.

새 코드 수정이 없으면 통과한 CI를 이유 없이 다시 실행하지 않는다. 후속 운영 적용은 [웹 1개 적용·복구안](journey-integration-rollout-2026-09-24.md)의 고정 후보에 대한 별도 승인 후, 당시 실제 가동 버전과의 차이를 확인한 뒤 진행할 항목이다. APK 직접 목표 입력·확정과 서명키 외부 복원은 사용자 확인으로 남겨 둔다.
