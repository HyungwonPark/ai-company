# 통합 목표 후속 기반 검증

2026-09-14. 기준 PR #5 `45ea969`를 보존한 별도 작업 트리에서 수행했다.
[통합 계획](plans/company-control-plane.md), [관리 API](management-api.md),
[격리 복구안](runtime-remediation-plan.md), [도메인·TWA 준비](domain-twa-plan.md)를 연결한다.

## 로컬 결과

| 검증 | 결과 |
|---|---|
| 고정 의존성 설치 | uv sync --frozen 성공, 별도 venv, Python3.13.15 |
| 전체 회귀 | **168개 통과**, 39.711초 (기존148 + 동시 실행8 + 관리 API12) |
| Android 연결 생성기 | **4개 통과**; 잘못된 서명·기존 앱 ID·덮어쓰기 거부 |
| 새 private demo | DEMO_READY, 실제 모델 호출 아님 |
| CLI | flow worker --parallel {1,2}, manage serve 도움말·구문 검사 통과 |
| UI 구문 | app.js·sw.js Node 구문 검사 통과 |
| 변경 공백 검사 | git diff --check 통과 |

동시 실행 테스트는 별도 Git clone과 실제 여러 thread/SQLite 연결을 사용한다.
pre-on_spawn의 process=None 경계, 같은 저장소 직렬화, 두 실행 상한, 로컬 검사 중
다른 역할 실행, worker 중단과 불확실한 결과 재실행 금지, 고아 실행 용량 예약,
일반 작업이 먼저 정렬되어도 중단 복구, 다른 worker 실행 뒤의 상태 재조회가 포함된다.

관리 API 테스트는 재시작·PM 대기 중 조회/승인, 큐의 실제 RUNNING 표시, 동시 커밋
사이 일관된 읽기 snapshot, 하네스 초안/활성 버전, 승인 대상·기한·비용·환경·SHA의
검증, 중복 요청과 변조 거부, 로그인/CSRF/Origin/Host/정적 경로를 포함한다.

## 독립 검수와 수정

관리 API 담당자가 실행 잠금 변경을 독립 검수하여 controller가 죽은 뒤 살아 있는 CLI의
용량 누락을 지적했다. 이후 별도 **gpt-6-astra / ultra** 검수 에이전트가 아래를 재현했다.

1. 다른 worker가 작업을 마친 뒤 오래된 task list 항목으로 다시 실행하여 같은 generation에
   두 retry job이 생기고 사용량이 덮이는 문제. 소유권 획득 후 상태·예약 시각을 다시 읽고,
   외부 실행에서 돌아올 때마다 저장된 대기 결과와 용량을 재확인하도록 수정했다.
2. 실제 sleep 자식을 spawn한 뒤 on_spawn 이전 중단되면 process ID가 비어 있어 용량에
   빠지는 문제. 살아 있거나 종료가 불확실한 영속 guard도 용량을 예약하도록 수정했다.
   검수용 자식은 종료하고 wait했으며 운영 프로세스를 사용하지 않았다.
3. 슬롯이 가득 찼을 때 먼저 정렬된 일반 작업 때문에 뒤의 중단 복구가 실행되지 않는 문제.
   새 실행만 건너뛰고 복구 후보는 계속 검사한 뒤 BUSY를 반환하도록 수정했다.

최종 독립 검수: **차단 지적 없음**, 집중 테스트20개 통과.
검수된 dispatcher.py SHA-256:
`e1fb9a5388493670a6a74efc8b0ed843a59f2254c77248b8d710245043d9a17e`.

이것은 개발한 코드의 독립 검수다. 제품의 실제 CLI에서 Astra Ultra가 같은 후보를
최종 검수했다는 증거나 실제 개발 루프 완주 증거로 사용하지 않는다.

## 원격 CI와 브라우저 검증 경계

초안 PR에는 기존 Python3.11/3.12 CI를 유지하며 Android 생성기 회귀도 추가한다.
후속 UI PR에는 별도 Console UI workflow를 준비했다. 실제 HTTP 서버와 private fixture를
띄우고 sandboxed Chrome으로 로그인·대화/승인 저장·재접속·5개 모바일 화면을 검사한다.
각 원격 run 결과와 대상 commit은 PR 본문에서 별도로 연결한다.

로컬 Chromium의 부족한 라이브러리4개는 /tmp에만 다운로드·해제했다. 시스템 패키지는
설치하지 않았다. `chromiumSandbox:true`에서 최종적으로 `No usable sandbox!`로 실패했다.
`--no-sandbox`나 전역 AppArmor 해제를 사용하지 않았다. 로컬 브라우저 PASS·화면 육안
검사·TWA 설치 성공을 주장하지 않는다. 원격 UI 결과는 실제 run으로 확인해야 한다.

## 실제 서버와 미완료 조건

- 같은 worker PATH/cgroup의 새 bwrap true 재현은 18:36:11 KST에 exit1.
  kernel audit의 unprivileged_userns net_admin/setpcap 거부를 확인했다. 일회성 unit은 수거됐다.
- root-owned bwrap 및 검토된 전용 프로필의 적용안·영향·음성 검사·되돌리기를 준비했다.
  패키지 설치·kernel profile 로드와 적용 후 실제 모델 실행은 이 기록 시점에 미실시다.
- 모델 요청 수용, CLI 구성, 실제 주 모델, 실효 effort/Ultracode, 동적 Workflow는 별도
  확인 항목이다. 이전 PR5의 미확인 항목을 이번 fixture 결과로 해소하지 않았다.
- PM 메시지는 저장되지만 실제 PM 소비/응답과 하네스에서 작업을 만드는 연결은 남아 있다.
  기존 flow 작업을 관리 역할에 연결할 수 있으며 자동 정책 변경·배포 endpoint는 없다.
- 기존 Caddy/Compose/timer 설정 digest와 서비스 health를 확인했다. 운영 timer·큐를
  이전하거나 설정을 바꾸지 않았다. hyungwon.cloud DNS는 다른 대상을 가리키므로 소유/
  현재 사용 확인과 새 환경 연결 검증이 필요하다.
- 실제 개발→검사→독립 검수→Astra Ultra 최종 검수, 공개 로그인, Android 서명/DAL/
  설치/로그인 유지 검증이 남아 있다. 운영 배포·자동 병합은 수행하지 않았다.
