# 위임받은 검증 클라이언트의 자동 흐름 검증

2026-09-14. **승인된 계획의 새 자동 검증 1건이 병렬 개발 → 검사 → 원격 CI → 독립 검수 →
Astra Ultra 최종 검수 → 후보 승인 요청까지 통과했다.** 실제 마스터가 브라우저에서 계획을
확정한 검증은 아니다. 결과 승인, 배포, 병합, 운영 타이머 변경은 수행하지 않았다.

구현은 [초안 PR #8](https://github.com/HyungwonPark/ai-company/pull/8), 실제 새 후보는
[초안 PR #10](https://github.com/HyungwonPark/ai-company/pull/10)이다.
이전 [PR #9](https://github.com/HyungwonPark/ai-company/pull/9)의 BLOCK은 그대로 보존했다.
기준 커밋 `352a31a79223fb77f377dbec578651ac665001f2`와 기존 작업 공간도 변경하지 않았다.

## 위임과 단일 실행

사용자가 정확한 기존 계획에 대해 검증 클라이언트의 계획 확정과 새 실행 하나를 위임했다.
원문, 수신 기록 시각, 계획 해시를 비공개 영속 원장에 저장하고 로컬 운영자 CLI로 접수했다.
동일 접수를 반복해도 같은 실행을 반환하며 HTTP 또는 모델 응답으로 위임을 생성하지 않는다.

| 항목 | 기록 |
|---|---|
| 계획 해시 | `4062ae815eb9becc85139ab54063e6551293b85068809287eab46e461f9bc6eb` |
| 수신 기록 시각 | `2026-09-14T13:03:14Z`; 사용자 응답 처리 후 처음 기록한 UTC 시계 기준 |
| 승인 원문 포함 receipt digest | `88b26877be1f30fae51205bfadf8ab19945364b56ea812893efcdf6876bc4d23` |
| 위임 digest | `363519520ccec00df757e7726d692adf80e6fbee60b2cb238440af309274a35a` |
| 새 실행 | `42eb1c2d3f3d4b44903a83a6ecd1ccf4`, `2026-09-14T13:18:07.017461Z` 생성 |
| 이전 실행 | `8f6d0475a8f34818aff5991d720074d9`, BLOCK 보존 |
| 실행 설정 digest | `e65c1459c90ba0fa313242bd908a02431e6f0bb9255f0e849053beab4edaa972` |

허용 경로는 `src/ai_company/pilot_status.py`, `tests/test_pilot_status.py`,
`.ai-company-ci/request.json` 세 개다. 기존 PM 제안과 설정을 그대로 사용하고 PM을 다시
호출해 계획을 바꾸지 않았다. 새 역할 작업·독립 clone·커밋·검수는 모두 승인 이후 시작했다.
계획·설정·목표·하네스·역할·경로가 달라지면 위임을 사용할 수 없다.

## 실제 후보와 검수

기준 `e4eb29dd62ded0e587b35a3eab6d35f4c2b44529`는
`validation/auto-pm-e4eb29dd`에 고정했다. 새 후보 SHA는
`87d4e296d0228663dadf02259d9db0641c9b8597`이다.

| 단계 | 실제 결과 |
|---|---|
| PM 제안 | 앞서 실제 Astra Ultra가 생성한 동일 해시의 계획을 승인; 새 제안으로 교체하지 않음 |
| 병렬 개발 | 독립 Git clone 두 개, High 개발 세션 두 개, 실제 실행 겹침 49.363초 |
| 통합 | 실행기가 허용 변경만 커밋하고 후보 PR #10 자동 생성; 수동 코드 수정 없음 |
| 격리 검사 | 네트워크 차단 Codex sandbox의 `pilot-unit` unittest 8개 통과 |
| 원격 CI | 승인 workflow의 성공 run·정의·checkout·check·artifact 검증 통과 |
| 독립 검수 | 별도 High 세션, 같은 후보와 verification digest에 PASS, 지적 없음 |
| Astra 최종 검수 | 별도 Ultra 세션 PASS; 실행기가 CLI 설정·종료 및 최종 gate의 원격 증적 재확인 |
| 보고·승인 | 인증된 로컬 API overview에 실제 보고서와 `awaiting_approval` 표시, 승인 요청 하나 생성 |
| 종료 | 새 개발 2개·독립 검수·최종 검수 CLI cgroup 4개 모두 소멸 확인 |

두 검수의 verification digest는
`068861506f25d1fcf3c229f39db1125413ee8926782ca03f8607e4bfa6dabaa9`다.
모델은 저장된 원격 증적을 검토했으며 직접 GitHub를 재조회했다고 주장하지 않았다.
실행기의 Verifier와 별도 읽기 감사가 GitHub 및 실제 artifact 출처를 확인했다.

승인 workflow [run 34848798486](https://github.com/HyungwonPark/ai-company/actions/runs/34848798486)의
workflow ID는 `357814570`, 경로는 `.github/workflows/automation-evidence.yml`이다.
승인 정의 digest `b8cf72b47b143ea7e84227c122e5ba2a9a2b3406ebdcdd881681ed3a3ca09b14`를
후보와 실제 실행 정의 SHA `799608c48a51a6063848a8862e77d150ced9c0ff`에서 모두 확인했다.
run attempt `1`, 필수 check `103991200063`, artifact `10348479607`가 같은 실행에 속하며,
checkout/tested SHA는 모두 새 후보다.

후보 승인 요청 `849f6de73d722f1ba2f4c5537111f6a0`은 **pending**이다.
내부 `MERGE_READY`는 같은 후보의 검사와 검수가 일치한다는 상태이며 병합 실행이 아니다.
이번 위임을 후보 수락·배포·병합 승인으로 확장하지 않았다.

## 구현·회귀와 증거 범위

위임 구현 커밋은 `2855444aa29c650b77c3f41a63f219fe361cf21c`다.
[원격 core CI](https://github.com/HyungwonPark/ai-company/actions/runs/34848394214)는
Python 3.11·3.12 각각 239개와 Android 준비 4개, 모의 수정 루프를 통과했다.
신규 위임 10개 회귀는 동시 접수·재시작·원자적 중단·불변 기록·시각·범위·변경 계획 거부를 포함한다.
기존 WAITING_QUOTA/WAITING_RETRY 복구·공유 한도·이관·미승인 CI 출처 거부도 유지했다.

[sandboxed Chrome UI CI](https://github.com/HyungwonPark/ai-company/actions/runs/34848394230)는
desktop/mobile 계획·보고·승인, 위임 원문 읽기 표시·HTML 이스케이프·이전 BLOCK 보존,
PM 대기 중 화면 접근과 제한/예약/이관 표시 회귀를 통과했다.
브라우저 회귀는 fixture이며 이번 실제 실행은 인증된 로컬 API 클라이언트로 관찰했다.
이 실행에서 실제 사용량 제한을 주입하거나 자동 수정 요청을 강제로 만들지는 않았다.
그 분기들은 기존 조정기 및 UI 모의 회귀에서 검증했다.

공개 요약은 [delegated evidence](evidence/automatic-pm-20260914-delegated.json)이다.
위임 원문·전체 실행 payload·검사 로그 경로는 생략하고 검수 보고서는 포함했다.
verification digest는 원본 기준이다.
원문 위임·원본 실행 사실·인증 API 이벤트·독립 감사는 비공개 실행 디렉터리에 보존했다.

## 운영 보존과 남은 실사용 조건

종료 후 운영 큐는 0건, 기존 timer는 active/enabled였다. 서비스 설정 hash,
컨테이너 ID·시작 시각·PID·재시작 수·health는 변경 전 기준과 같다.
app/caddy/db healthy, backup running이며 재시작은 없다. 두 userns 제한값 모두 `1`,
단일 bwrap AppArmor 프로필 hash도 그대로다.

새 세션의 Codex 0.154.0 rollout에서 모델 `gpt-6-astra`와 High/High/High/Ultra의
실제 CLI turn 설정을 확인했다. `backend_model_verified=false`를 유지한다.
Claude 실효 effort·Ultracode·동적 Workflow, 실제 마스터의 브라우저 계획 확정,
공개 도메인·TLS·TWA 서명 및 Android 실기기 연결은 별도 검증이 남았다.
운영용 자동 PM 타이머와 공개 서비스도 배포하지 않았다.
자세한 설정 증거와 공개 연결 준비는 [readiness 기록](automation-readiness-2026-09-14.md)을 따른다.
