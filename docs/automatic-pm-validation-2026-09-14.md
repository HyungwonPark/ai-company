# 자동 PM 실제 연결 검증

2026-09-14. 구현 초안은 [PR #8](https://github.com/HyungwonPark/ai-company/pull/8)이며
기존 `352a31a79223fb77f377dbec578651ac665001f2`를 보존했다. 운영 배포·자동 병합은 하지 않았다.

## 최신 후속 결과

이후 명시적 위임에 연결한 새 실행은 병렬 개발·검사·원격 CI·독립 검수·Astra 최종 검수를
통과하고 후보 승인 요청까지 도달했다. [위임 검증 기록](automatic-pm-delegated-validation-2026-09-14.md)을
참조한다. 이는 위임받은 검증 클라이언트의 실행이며 실제 마스터의 UI 조작 검증은 아니다.
아래의 기존 PR #9 BLOCK 기록은 소급 승인하지 않고 보존한다.

## 이전 실행 결과

실제 Astra Ultra PM → 인증 API의 계획 확정 → 두 독립 clone의 병렬 개발 → 실행기 커밋 →
통합 후보 draft PR → 격리 검사 → 승인 workflow 원격 CI → 독립 검수까지 자동 연결했다.
**Astra Ultra 최종 검수는 명시적 마스터 확정/위임 증거가 없다는 이유로 BLOCK했다.**
코드 결함은 지적하지 않았으나, 이 실행을 최종 합격이나 실제 완주로 표시하지 않는다.

검증 클라이언트는 목표를 보내고 PM이 제안한 두 출력 경로·역할 수·의존 관계를 확인해
인증 API로 계획을 확정했다. 이것은 브라우저의 실제 사용자 조작이나 마스터의 명시적 위임을
입증하지 않는다. 이전 BLOCK을 PASS로 바꾸지 않으며, 추가 실행 전에 구체적인 위임 확인을 요청했다.

이후 사용자가 계획 `4062ae815eb9becc85139ab54063e6551293b85068809287eab46e461f9bc6eb`에 한해
검증 클라이언트의 계획 확정과 새 자동 검증 1건을 명시적으로 위임했다. 첫 기록 시각은
`2026-09-14T13:03:14Z`이며, 원문과 수신 시각은 비공개 위임 원장에 보존한다.
허용 범위는 두 소스/테스트 파일과 `.ai-company-ci/request.json`뿐이다.
새 실행은 이전 BLOCK을 소급 승인하지 않고 별도 run으로 연결한다. 아래 기록은 이전 실행이다.

기계 판독 기록: [automatic-pm-20260914-v2.json](evidence/automatic-pm-20260914-v2.json).
검증 digest는 실제 저장된 원본 기록 기준이다. 공개 요약에서는 로컬 검사 로그 경로만 생략했다.
원본 큐·CLI 출력·계획·API 이벤트는 비공개 `.ai-company/automatic-pm-20260914-v2/`에 보존했다.

## 실제 후보와 단계

후보 [PR #9](https://github.com/HyungwonPark/ai-company/pull/9)는 자동 게시된 draft다.
기준 `e4eb29dd62ded0e587b35a3eab6d35f4c2b44529`는
`validation/auto-pm-e4eb29dd`에 고정해 후속 구현 문서 변경과 분리했다.
후보는 `1afe788cecfebf7c08246eb5120e459067ed491b`이다.

작업은 `summarize_role_states`의 전체·완료·대기·차단·진행 건수 집계와 독립 unittest다.
개발 역할은 `src/ai_company/pilot_status.py`, 검사 작성 역할은 `tests/test_pilot_status.py`만 소유했다.
각 역할의 `.git` 저장소는 독립적이며 실제 실행 시간 구간이 겹친다.

| 단계 | 결과 |
|---|---|
| Astra Ultra PM | 실제 세션의 구조화된 계획, 역할 2개·서로 다른 출력 경로·의존 없음 |
| 계획 확정 | 인증 API 검증 클라이언트가 수행; 명시적 마스터 위임 근거는 없음 |
| 병렬 개발 | 두 실제 Codex High 세션, 서로 다른 clone에서 동시에 수행 |
| 커밋 | 원본 모델 보고서 유지, 실행기가 허용 변경을 커밋하고 parent/tree/출력 SHA 증거 검증 |
| 격리 검사 | 네트워크 차단 Codex sandbox의 실제 unittest 8개 통과 |
| 원격 CI | 승인된 workflow run·실행 정의·후보·필수 check·artifact 출처 검증 통과 |
| 독립 검수 | 별도 Codex High 세션, 같은 후보 및 verification digest에 PASS |
| Astra Ultra 최종 검수 | 코드 결함 없음; 마스터 확정/위임 증거 누락으로 BLOCK |
| 프로세스 종료 | PM·개발 2개·검수·최종 검수의 CLI cgroup 5개 모두 소멸 확인 |

원격 증적은 [run 34845452000](https://github.com/HyungwonPark/ai-company/actions/runs/34845452000)이다.
workflow ID `357814570`, 경로 `.github/workflows/automation-evidence.yml`,
정의 digest `b8cf72b47b143ea7e84227c122e5ba2a9a2b3406ebdcdd881681ed3a3ca09b14`,
실행 정의 SHA `0e705dbcb48fc62358bf6469293e90e4497bf693`,
필수 check ID `103980169545`, artifact ID `10347029450`, run attempt `1`을 확인했다.
실제 checkout과 증적의 tested SHA는 모두 후보 `1afe788`이다.

## 추가 재현과 수정

- native `workspace-write` 개발은 파일을 수정했지만 `.git/index.lock` 쓰기는 EROFS로 거부됐다.
  샌드박스는 유지하고 contribution 전용 실행기 커밋을 추가했다. 허용 경로 밖 파일·symlink·
  원본 보고서 교체·부모/트리 불일치는 차단한다. 기존 full 작업의 커밋 규칙은 바꾸지 않았다.
- 첫 실제 PM 요청은 strict JSON schema에 `findings` 등 모든 선언 필드가 required로 지정되지 않아
  API가 거부했다. 원래 실패 실행은 그대로 두고, provider 전송 스키마만 정규화했다.
- 첫 원격 Chrome 회귀에서 화면 이동 직후 프로젝트 변경이 이전 화면으로 덮이는 경합을 재현했다.
  화면 상태를 동기적으로 갱신하고 같은 이벤트 순서의 회귀를 추가했다.
- 독립 코드 검수의 5개 결함을 수정했다: 승인 ID/경로 길이 불일치, PM 제출 중단 시 fixture→live
  출처 혼동, 외부 큐 작업 선택, 수정 역할에 최신 의존 결과 누락, 한 분기 차단 시 무관한 후속 작업 정지.
- 진행 단계가 바뀐 뒤 과거 CI 대기 사유를 계속 표시하던 메시지와 실행 readiness 표시도 보완했다.

## 회귀·UI 검증

[구현 CI](https://github.com/HyungwonPark/ai-company/actions/runs/34845498430)는
Python 3.11·3.12에서 각각 core 229개와 Android 준비 4개를 통과했다.
기존 WAITING_QUOTA/WAITING_RETRY 결과 반영·공유 한도·이관·원격 CI 출처 회귀가 포함된다.
새 조정기 회귀는 모의 모델/CI를 명시하고 병렬 개발, 재시작, 자동 수정 반환,
독립 분기 계속 진행, 대체자 이관, fixture/live 혼동 방지를 검사한다.

[sandboxed Chrome CI](https://github.com/HyungwonPark/ai-company/actions/runs/34845498486)는
desktop/mobile 계획 검토·확정, 오래된 계획, 응답 손실, PM 대기 중 보고·승인 접근,
사용량·재시도·예약·이관 표시, 모델 검수 의견의 후보 연결·HTML 이스케이프를 통과했다.
모바일 대화상자 하단의 확정 버튼 접근도 검사했다. 스크린샷은 fixture이고 실제 모델 실행 화면은 아니다.
로컬 Chromium은 AppArmor의 `No usable sandbox`로 미실행이며 sandbox를 해제하지 않았다.

## 운영 보존과 남은 조건

검증 중 재조회한 운영 queue는 0건, timer는 active/enabled였다. 서비스 설정 hash,
컨테이너 ID·시작 시각·PID·재시작 수·health는 이전 기준과 같았다.
app/caddy/db는 healthy, backup은 running이며 healthcheck가 없다.
두 userns 제한값은 모두 `1`, 단일 bwrap AppArmor 프로필 hash도 동일하다.

새 실행 정책은 실제 Codex 0.154.0 rollout의 해당 session/cwd/시도 시간에 묶인
`gpt-6-astra/high` 및 `ultra` CLI 설정을 확인한다. backend 검증값은 false로 유지한다.
Claude의 실효 effort·Ultracode·동적 Workflow, 공개 도메인·TWA 서명·실기기 연결은 미검증이다.
공식 CLI/문서 근거와 범위는 [설정·공개 연결 준비 기록](automation-readiness-2026-09-14.md)에 있다.
