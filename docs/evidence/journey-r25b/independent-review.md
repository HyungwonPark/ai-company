# R25-1b 독립 검수 기록

## 범위

사용자 지정 PR23 ca35e931a701cdae60483d2e1b36114b1f0e2754의 검수 문서와 재현 스크립트를 읽었다. 기준 게시 0bcf8d7, 제품 14114a7의 R25-1 초기 부팅/R25-2 통과를 보존하고 인증 뒤 혼합 graph/report 모듈 경계만 검토한다. 저장소 AGENTS 및 실제 읽은 ai-company-frontend/code-review-skill/variant-analysis 지침을 적용했다. 제품 작성과 분리해 독립 회귀 두 파일만 작성했다. 운영·실제 모델·키·PR10·승인 변경 없음.

## 독립 최초 재현

고정 공개 Git의 수정하지 않은 pr25-mixed-graph-0bcf8d7.mjs를 Node VM에서 재실행해 REPRODUCED를 확인했다. 새 app SHA f048a9d5f5826ccb8c40868e81256c3a652c0f3e62c3ac41196f1c7e563a0450 / 구 graph 8819bcb7bab374b71ad9037c8a8a0248f764f582ee4efecd432c24689d3643b5 조합에서 getScope 및 selectScope TypeError. 신 graph 대조는 두 함수 모두 PASS. v8 fetch 처리에서 graph 경로만 실패하고 새 app 및 인증 API는 정상이라는 전제가 성립했다. 이것은 실제 Chrome 성공 증거가 아니다.

## 독립 정상 기대 브라우저 검사

- tests/ui/run_journey_mixed_modules.py: 실제 임시 ManagementHTTPServer, password login 및 SQLite. 기존 Host/Origin/CSRF/쿠키 검증은 그대로이며 시험 전용 static handler만 baseline/current bytes와 선택적 socket failure를 주입한다. 업무 POST는 서버가 기록·거부하며 login/password만 허용한다.
- tests/ui/journey_mixed_modules.cjs: v8 제어 2탭과 새 worker waiting에서 old graph, old graph+report, old report, old app+new modules 네 조합. 실제 받은 세 파일 bytes, worker client 수, 정상 session/overview API, 정확한 선택 URL, 미전송 PM 초안, 명시적 갱신 후 동일 실행 탐색, 업무 write 0, Light320/Black390 안내 화면을 확인한다.
- 제어 endpoint는 loopback 임시 서버에만 존재하며 제품 코드/API에 추가하지 않는다. 원문 예시 상태와 업무 테이블 29개 불변을 대조하고 인증 예시 쓰기는 별도로 구분한다.
- 문법 검사 및 seed-only PASS: 임시 업무 테이블 29개, v8 public assets 43개, 초안용 live 예시 프로젝트. 실제 모델이나 worker는 실행하지 않는다. 최초 tests-only 2547e5c의 실제 Chrome 결과 대기 중.

## 수정 작업트리 코드 검토

- app 호환성 검사는 새 graph의 getScope/selectScope와 report.execution 세 메서드 존재를 검증한다. 구 모듈이면 selected/sync/capture/mount와 content를 안전하게 닫고 현재 계획이나 다른 실행으로 대체하지 않는다.
- 지원되지 않는 모듈에서 신규 업무 쓰기는 API 및 form/button 진입 모두 거부한다. 인증·읽기는 유지하고 기존 요청을 재전송하지 않는다. 명시적 reload는 busy form/open dialog에서 거부하며 저장소·초안·현재 URL을 지우지 않는다.
- 새 imports/named exports를 요구하지 않는다. 공개 shell 최초 부팅 해결과 인증 뒤 workspace 경계의 역할을 구분한다.
- 구 app+신 report의 분류기 기본 unknown은 보수적인 제한이다. 이전 app에 새 실행 선택 계약이나 신규 쓰기 guard가 소급 적용됐다고 주장하지 않는다.
- 현재 diff에서 추가 차단 결함을 발견하지 못했다. 실제 인증 Chrome 전후 재현 및 최종 고정 코드·캡처 확인 전 최종 합격으로 확대하지 않는다.

## 첫 브라우저 시도: 검사 준비 오류

검사 전용 2547e5c / Journey 36080247844의 mixed failure JSON은 인증된 선택적 모듈 실패 단계에서 waitForResponse 시간 초과이고 pageerror는 없었다. 코드 조사에서 같은 pathname의 hash만 바꾸는 page.goto는 새 문서를 부팅하지 않아 새 JS 요청 자체가 없었다. 이것은 R25-1b 원래 제품 결함의 Chrome 재현이 아니며 합격/제품실패로 계산하지 않는다.

검사만 수정해 같은 브라우저 task에서 의도한 URL을 history.replaceState로 설정하고 location.reload를 호출한다. 실제 새 문서 부팅과 세 모듈 response를 대조하는 원래 정상 기대는 유지한다. 제품 변경 없이 tests-only 후속 CI에서 최초 원래 TypeError가 발생하는지 다시 확인해야 한다.

## 고정 미리보기 날짜 경과에 따른 기대값 정정

2547e5c preview 실패 JSON은 만료된 고정 승인 예시에 대해 모든 결정 버튼이 비활성이라고 기대하여 2 != 0으로 실패했다. 실제 app approvalDocuments는 원문 pending이면서 기한이 지났을 때 approve만 비활성이고 반려/수정 요청은 남긴다. 운영 API overview는 만료를 expired로 투영하며 decide 자체도 expires_at을 검증해 거부한다. 읽기 전용 패키지는 원문 pending을 변경하지 않고 공통 adapter에서 모든 non-GET을 405로 거부한다.

따라서 만료 approve 비활성+모든 preview write405라는 실제 계약으로 검사 기대를 정정하는 것은 타당하다. 시계·fixture·승인 결과·제품 보호 규칙을 바꾸지 않으며 원래 실패를 보존해야 한다. 최초 R25-1b 실패와 관계없는 검사 기대 경계로 구분한다.

## 최초 실제 인증 Chrome 결함 재현 — c4d0a1c

검사 전용 c4d0a1c / Journey 36080550179의 실패 JSON과 PNG를 독립 대조했다. 이번에는 준비 단계가 아니라 실제 R25-1b 재현이다.

- 받은 app SHA f048a9d5f5826ccb8c40868e81256c3a652c0f3e62c3ac41196f1c7e563a0450, 구 graph SHA 8819bcb7bab374b71ad9037c8a8a0248f764f582ee4efecd432c24689d3643b5, 새 report SHA 1a7667777b931d2f30900bdb4ae8821ceaebdaa9da10f983083ac4bb0d5bdaaa.
- v8 active window clients 2, v10 waiting clients 0, authenticated=true 및 정상 실제 project API. HTTP cache 비활성 상태다.
- 실제 pageerror: workspaceGraph.selectScope is not a function. 정상 기대인 처리되지 않은 예외 없음에 FAIL했다.
- PNG에서는 인증 후 진행 화면이 불러오는 중 skeleton에 멈춘 모습을 직접 확인했다. 운영에서 발생했다고 주장하지 않는다.

## 고정 수정 ffb62c3 코드 재검수

실제 수정 diff는 app.js의 호환성 확인·읽기 안내·명시적 reload·신규 업무 write 보호이며 backend/DB/worker/권한/예산은 변경하지 않았다. 새 저장/PM 요청 함수의 초기 guard가 이미 남은 불확실 요청 intent와 멱등 키를 유지하는 것도 확인했다. 실제 compatibility 회귀(3개 혼합 조합·18개 업무 write 미전송·인증/read 허용·reload busy/dialog 보호), 명세 저장/PM 경합 및 남은 intent 보존 회귀를 독립 재실행해 PASS했다. 제품 코드 검토에서 추가 차단 결함은 발견하지 못했으나 최종 실제 Chrome 4조합 성공과 화면 검수 전 완료 판정은 보류한다.

# 최종 코드·실제 브라우저 독립 판정 — ffb62c3

**판정: R25-1b와 지정된 혼합 모듈 관련 경계 보완 통과. 검토한 비운영 코드·실제 인증 API 브라우저·화면 범위에서 추가 차단 결함을 발견하지 못했다.** Console 전체 CI가 별도로 진행 중인 상태이므로, 이 판정을 모든 CI 완료나 운영 적용 완료로 해석하지 않는다.

## 같은 고정 후보의 전후 근거

- 제품 ffb62c37e4822530f38f15f4b0f64781cdf40bc6, Journey 36080894484 성공 artifact를 읽었다. 테스트 전용 c4d0a1c에서 실제 selectScope TypeError가 난 동일 정상 기대 검사가 수정 뒤 성공했다. 앞선 hash-only 검사 준비 오류 2547e5c는 최초 제품 실패와 구분해 보존했다.
- 실제 격리 Chrome 153.0.8010.52, sandbox=true. 새 app+구 graph, 새 app+구 graph/report, 새 app+구 report, 구 app+새 modules 네 조합 모두 PASS.
- 각 조합에서 v8 제어 window client 2, v10 waiting client 0, authenticated=true, 실제 project API 성공, HTTP cache 비활성과 실제 app/graph/report SHA를 확인했다. 새 app SHA는 89874441e53980533f3bfc77556b9f0c5ce518f5b590299dc7c688d808589e4b다.
- 미지원 조합은 내부 메서드명 없이 화면 갱신 안내만 표시하고 다른 실행·후보를 표시하지 않는다. 선택 URL과 현재/다른 탭 PM 미전송 초안이 보존됐다. stale 문서 컨트롤로 신규 업무 행동/폼 제출을 시도해도 업무 쓰기 0. 명시적 재로드 뒤 선택한 기존 실행의 진행→결과→승인 탐색을 확인했다.
- 역방향 구 app+신 모듈은 처리되지 않은 오류 없이 기존 프로젝트별 읽기 계약을 유지했다. 구 app에 신 여정의 정확한 실행 URL 계약이나 신규 쓰기 guard가 소급됐다고 주장하지 않는다.
- 모든 case errors가 비어 있다. 업무 테이블 29개 전후 불변, browser_returncode=0, 기록된 쓰기는 테스트 로그인 4건·테스트 비밀번호 변경 1건뿐이다. 원문·승인·사용량·재시도 정책을 바꾸지 않았다.
- 기존 상태 분류 48조합과 최초 SW 부분/전체 오프라인 갱신·복구 3경계 PASS를 같은 후보의 JSON에서 확인했다. API/POST 비캐시·비재생과 공개 부팅 검사는 그대로 유지됐다.

## 직접 시각 검수

최종 PNG old-graph Light320/Black390, old-graph-report Light320, old-report Black390 네 장을 직접 열었다. 한국어 제목과 설명, 눈에 보이는 화면 다시 불러오기 버튼이 모바일 폭에서 읽히며, 서버 내부 함수명이 노출되지 않는다. 기존 실패의 불러오는 중 skeleton 정지 대신 사용자가 취할 행동이 분명하다. 검사에서 stale 버튼·폼 시도를 했으므로 화면 하단의 갱신 후 다시 확인 toast가 함께 보인다.

이것은 독립 작성 Playwright 검사의 실제 Chrome 조작·캡처 대조이며, 이 검수자가 APK 실기기를 수동 조작했다는 뜻은 아니다. 운영 서비스의 현재 상태도 확인하지 않았다.

## 패키지·이미지 동일성

- 미리보기 manifest의 원본 28파일을 고정 제품/패키지 소스와 SHA256 독립 대조해 모두 일치. HTML 직접 계산 해시 9a380f843e390ddff3233e2627ccada40d65eb92e4938634fa91d02c0b2a4209. manifest source_commit 1459523445d0826d4d7a8de45e15be81ca00d209는 CI 합성 checkout이며 제품 head와 구분한다.
- 이미지 sha256:b3353656c9717bc4c67395c86611df61370fc8a53786da2c22e47e9f5df83576, product ffb62c3, arm64 로컬 Docker ID. registry pull 주소가 아니다. source manifest 113파일을 고정 소스와 독립 대조해 일치; manifest SHA256 377374102c7f94a4ffec0d8fd5082fdb3678521afa458a80bbe8e0e1a41bb540.
- 작성자의 격리 이미지 PASS JSON에서 root 읽기 전용·uid65532·network none/loopback·임시 state·운영 mount 없음·익명 API401·다른 Host403을 확인했다. 이 독립 검수자는 이미지를 다시 실행하지 않았으며 JSON과 소스 bytes의 일치를 검토했다.

## 남는 제한

실제 모델·운영 도메인·실기기 APK·외부 서명키 복원은 미검증이다. 운영 배포·병합·PR10 승인 변경을 수행하거나 승인하지 않았다. 구버전 v8 인증 전 로그인 비밀번호의 offline 재렌더 초기화는 기존 제약으로 유지하며 업무 초안 보존과 구분한다. 고정 만료 미리보기의 반려/수정 버튼 표시를 서버 결정 가능성으로 해석하지 않으며 패키지의 모든 쓰기는 405다. 최종 Console CI 완료 여부는 주 작업자의 별도 확인 항목이다.

게시 준비 시 작성자 대조: 독립 코드·화면 판정 이후 Console36080894592도 같은 ffb62c3에서 success로 완료됐으며 candidate.json에 출처를 연결했다. 이 추가 CI 조회는 독립 검수자의 직접 실행으로 세지 않는다.
