# Claude 적용 설정과 Workflow · 2026-09-16

**Opus 5의 실제 요청 effort, Ultracode 적용값, 두 에이전트의 동적 Workflow 실행을 확인했다. 운영 개발 대체자로 등록하거나 기존 정책을 변경하지는 않았다.** 앞서 [지원 목록만 확인한 기록](evidence/claude-eligibility-2026-09-16.json)은 그대로 보존한다.

## 달라진 근거

CLI `2.1.270`의 `get_settings` control 응답에는 `applied.model`, `applied.effort`, `applied.ultracode`가 있다. 설치 바이너리의 해당 응답 스키마와 실제 응답을 대조했다. initialize의 모델 지원 목록이나 모델이 작성한 설명과 다른, CLI가 계산한 실행 시점 설정이다. 실행 전·후에 각각 고유 request ID로 조회하고 원문 settings·hooks·환경 값은 출력에서 제거했다.

공식 OpenTelemetry의 `api_request`에는 요청의 effort와 세션·prompt·request ID가 포함된다. 각 프로브의 `127.0.0.1` 임시 수집기로 필요한 필드만 받아 native 세션과 대조했다. 원문 프롬프트·인증 정보·모델 사고 내용·전체 요청/응답 body는 계측에 포함하지 않았다. 수집기는 종료 후 닫았고 외부 수집 서버나 전역 환경 설정을 추가하지 않았다. [공식 계측 필드](https://code.claude.com/docs/en/monitoring-usage)

| 실행 | 실제 결과 |
|---|---|
| xhigh, Workflow 비활성 | Opus 5 주 요청의 `effort=xhigh` 확인. stream-json의 effort 배열은 여전히 비어 있음 |
| Ultracode 요청, Workflow 기본값 유지 | 실행 전 `applied.ultracode=false`. 원하는 설정이 아니므로 모델 요청 전 중단. 모델 API 호출 0회 |
| 해당 실행에 `enableWorkflows=true` 추가 | 실행 전후 `model=claude-opus-5`, `effort=xhigh`, `ultracode=true`. 주 요청 계측도 xhigh |
| 같은 설정의 작은 Workflow | 실제 Workflow 호출 → 두 에이전트 병렬 실행 → 결과 취합 → 완료 알림 → 최종 응답. 전후 적용값 유지 |

현재 계정은 Pro다. 공식 문서는 Pro에서 Dynamic workflows를 별도로 켜도록 안내한다. 설치 CLI의 설정 스키마가 제공하는 `enableWorkflows`를 해당 실행의 `--settings`에만 지정했다. `disableWorkflows=false`만으로 기본값이 활성화되지는 않았다. `--restricted`, `--safe-mode`, `dontAsk`는 네 실행 모두 유지했다. [공식 Workflow 안내](https://code.claude.com/docs/en/workflows)

## 실제 Workflow

세션 `927b9644-01c7-4e33-a460-1c8bc92e36dd`, Workflow `wf_a9738395-7f3`, native task `w2cmsjc2m`이다. Workflow 도구 호출과 완료 알림의 tool-use ID가 같고, TaskOutput도 같은 task ID의 `completed` 결과를 반환했다.

- 두 자식 ID는 `ae6211625f95a24f3`, `a27370138c1529206`이다. 같은 단계에서 각각 native 시작 시각·기간·`done` 상태가 기록됐다. 실행 구간은 **3,271ms 겹친다**.
- 각 자식의 API 계측에 같은 Workflow ID와 `claude-opus-5`, `effort=xhigh`가 기록됐다. 주 요청도 같은 모델·effort다. CLI의 제목 생성용 Haiku 요청은 보조 작업으로 별도 집계했다.
- 부모에게 제공한 도구는 Workflow·TaskOutput·TaskStop뿐이다. 자식의 실제 도구 호출은 결과 반환용 StructuredOutput 각각 1회였다. 파일 읽기·쓰기·셸·웹 조회 도구는 제공하지 않았다.
- 인라인 테스트 데이터 두 묶음의 결과가 합쳐졌다. 이 과제는 Workflow의 배정·병렬 실행·전달·완료를 확인하는 용도다. `pilot_status.py` 구현, 해당 함수의 독립 회귀 검사, 마스터 계획 확정이나 코드 독립 검수로 세지 않는다.

Workflow 실행은 24.2초, CLI 비용 추정은 USD `0.28459375`였다. 네 번의 CLI 실행 전체는 주 요청 5회·Workflow 자식 요청 2회·제목 생성 보조 요청 3회, 추정 비용 합계 USD `0.34472375`다. 원하는 설정이 아니었던 실행에는 모델 호출이 없었다. 계정 제한을 고의로 소진하거나 quota 재시도를 수행하지 않았다. 모든 실행의 cgroup 종료와 임시 수집기 종료를 확인했다.

[비밀 없는 native 설정·계측·자식 작업 증적](evidence/claude-runtime-settings-2026-09-16.json)에 설치 CLI SHA-256, 각 실행의 stdout/선택 계측 SHA-256, 전후 적용값과 요청 ID, 실제 자식 상태를 보관한다. 비공개 원자료와 실행기는 서버 `.ai-company/claude-telemetry-20260916/`에 있다. provider 내부 모델의 암호학적 보증으로 해석하지 않으며 `backend_model_verified=false`를 유지한다.

## 운영 연결의 남은 일

1. 현재 실행기는 initialize만 수집하고, 기존 `cli_configuration` 정책은 Codex turn 근거만 허용한다. Claude의 전후 적용 설정과 요청별 계측을 실행 결과에 연결하는 새 증거 계약이 필요하다. 기존 정책의 의미를 바꾸어 과거 작업을 적격 처리하지 않는다.
2. 실제 저장소를 수정하는 Claude 역할에 대해 허용 경로·비허용 경로·도구 샌드박스와 Workflow 자식의 제한을 검증해야 한다. 이번 도구 없는 데이터 판정을 쓰기 격리 증거로 대체하지 않는다.
3. 위 조건을 충족한 후보를 새 작업 정책에 명시한 뒤, 별도 큐에서 모의 한도 주입과 실제 Claude 이관·동일 세션 재개를 대조해야 한다. 운영 계정의 한도를 소진하지 않는다.

공개 화면과 두 worker는 계속 `51b911a`다. [준비된 작업실 적용안](translation-repair-2026-09-16.md)의 코드 `c2b3fec`·이미지·허용 범위는 바꾸지 않았으며 적용 승인을 기다린다. 이번 관찰로 운영 큐·기존 서비스·PR #10 `pending`·APK/서명키를 변경하지 않았다.
