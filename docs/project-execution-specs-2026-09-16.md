# 프로젝트 실행 명세 · 2026-09-16

후속 사용자 승인으로 웹·PM/조정기·읽기 전용 pilot 카탈로그를 [운영에 적용했다](project-execution-activation-2026-09-16.md). 번역 worker와 기록은 유지했다. 아래 미적용 표시는 개발·패키지 준비 당시의 기록이며, 마스터의 APK 직접 확정 실행은 계속 확인 대기다.

**이번 개발 단계는 완료했다. 현재 운영에는 적용하지 않았다.** 이름과 자연어 목표로 PM과 대화를 시작하고, PM이 제안한 실행 범위를 검토한 다음 그 범위에 맞는 계획을 직접 확정하는 흐름을 구현했다. 기술 입력란을 새 프로젝트의 필수 입력으로 추가하지 않는다.

## 고정 후보와 검증 결과

[초안 PR #13](https://github.com/HyungwonPark/ai-company/pull/13)은 PR #8의 `69471e8`에서 분리했다. 제품 코드 후보는 `23ac8a723f6bfc4d67fe12949c9253e9f8acf04c`이며 이후 검증 기록만 별도로 추가한다. 이전 목표의 완료 기록은 보존했고 미완료 항목을 완료로 바꾸지 않았다.

| 확인 | 실제 결과 |
|---|---|
| 로컬 회귀 | Python 417개 PASS. 기존 호스트 bwrap 격리 검사 포함, skip 0. Android 준비 4개와 모의 개발 루프 `DEMO_READY` |
| 프로젝트 검증 | 임시 Git 저장소·독립 clone·별도 SQLite 큐에서 두 프로젝트의 개발·검수 모의 흐름 완주. 독립 경계 시험 16개, 자동화 시나리오 4개 |
| 중단·예산 | `WAITING_QUOTA`·`WAITING_RETRY` 저장 직후 중단, 호출 시작 전 예약 중단, 공유 계정 cooldown, 과거 사용량·수정 작업·동시 예약·검사 시간 상한 확인 |
| 독립 검수 | core와 저장 번역 원문 대조, UI 별도 검수 PASS. 과대 입력 고착·저장 요청 표시 불일치·구버전 확정 안내·PM 요청 후 화면 전환을 수정하고 회귀로 확인 |
| 원격 Python | [CI 35093180274](https://github.com/HyungwonPark/ai-company/actions/runs/35093180274) PASS. Python 3.11/3.12 각각 417개 중 414 PASS·서버 전용 3개 skip, Android 준비 각각 4 PASS |
| 원격 화면 | [Console UI 35093180287](https://github.com/HyungwonPark/ai-company/actions/runs/35093180287) PASS. sandboxed Chrome에서 Light·Black, 320/390/1440px, 저장 응답 유실·확정 digest·기존 PR12 회귀 확인 |
| 설치된 이미지 | 네트워크 없음·읽기 전용 루트·비특권 UID·cap 제거·새 권한 금지·256MiB·CPU 0.25·PID 64의 임시 컨테이너에서 33개 PASS. 비밀번호 변경·Host/Origin/CSRF·명세 저장·불변 표와 정적 파일 해시 확인 |
| 번역 2건 | 저장 원문·출력 각 8문장을 대조. 복사본에서 1건만 새 읽기 자료로 통과, 승인 권한을 ‘인증’으로 옮긴 1건은 실패 유지. 추가 번역 호출·운영 반영 0 |
| 운영 보존 | 웹·커플 컨테이너 5개 동일, 두 worker PID·unit·재시작 횟수 0과 기존 설정 유지. quota timer active, PR #10 pending. 운영 프로젝트 1·PM 요청 1·실행 2 그대로 |

관련 코드 CI와 브라우저 CI는 위 코드 후보에 연결했다. 제품 실행용 `Automation evidence` workflow는 이 개발 브랜치에서 **skip**이며 PASS로 세지 않는다. 임시 프로젝트의 모델·원격 검사·최종 검수는 fixture를 사용했다. 제품 Astra CLI의 새 최종 검수, 일반 프로젝트의 운영 자동개발, 마스터의 APK 직접 조작을 수행했다는 뜻이 아니다.

로컬 Chrome은 기존 AppArmor/userns 제약으로 실행하지 못했으며 격리를 해제하지 않았다. 화면 검증은 원격 CI의 sandboxed Chrome에서 완료했다. 상세 해시·검사 목록·운영 대조는 [검증 원장](evidence/project-execution-validation-2026-09-16.json), 번역 의미 판정은 [별도 기록](translation-saved-recheck-2026-09-16.md)에 보존했다.

문서 커밋 `da55b02`의 [화면 CI 35092106928](https://github.com/HyungwonPark/ai-company/actions/runs/35092106928)은 협업 fixture의 후반 화면을 저장한 뒤 `Route is already handled` 오류로 실패했다. 기존 정리가 진행 중인 `fetch`·`fulfill` 완료를 기다리지 않는 누락을 독립 검수로 확인해, `collaboration.cjs`에서 `unrouteAll({behavior:'wait'})` 후에 새로고침하도록 보완했다. [Playwright 1.63의 정리 대기 구현](https://github.com/microsoft/playwright/blob/v1.63.0/packages/playwright-core/src/client/network.ts#L827)에 따른 변경이며 오류를 삼키거나 화면 검사를 제거하지 않는다. 로그만으로 내부 중복 처리 경로 전체를 확정하지 않는다. 해당 시험 정리는 제품 코드를 바꾸지 않는다. 후속 `d1701c6`에서는 이 시나리오가 통과했고, 별도 키보드 회귀가 실제 제품의 포커스 손실을 발견했다.

## 기존 상태

| 구분 | 보존한 상태 |
|---|---|
| 실제 적용 완료 | [공개 웹 `28404b2`](pr12-web-activation-2026-09-16.md), [PM·조정기와 번역 worker `5594744`, Claude 개발 대체 후보](production-activation-2026-09-16.md) |
| 현재 실행 범위 | `src/ai_company/pilot_status.py`, `tests/test_pilot_status.py`와 기존 CI 요청 메타데이터. 임의 저장소·명령·계정의 자유 실행은 허용되지 않음 |
| 이번 개발 | 프로젝트별 실행 명세의 불변 버전, PM 제안과 등록 분리, 계획·실행 버전 연결, 로컬 카탈로그와 UI 연결. 운영 미적용 |
| 마스터 확인 필요 | APK에서 새 목표 입력·계획 직접 확정, 같은 실행의 검수·보고·승인 요청 대조. 서명키의 PC 외부 보관·별도 비밀번호 관리자 저장·외부 복원 확인 |
| 계속 보존 | 커플 서비스, 기존 타이머·큐·원문·사용량·공유 한도·승인 기록, PR #10 `pending`. 병합하지 않음 |

위 마스터 확인은 서버가 대신 수행하지 않는다. 프로젝트나 명세의 예시 이름을 실제 생성된 항목으로 안내하지 않는다. 서명키 인수인계는 [기존 외부 확인 절차](live-use-readiness-2026-09-16.md#서명키인수인계)를 따른다.

## 사용 흐름

다음은 **이번 후보를 적용한 뒤** 사용할 흐름이다. 현재 공개 화면에서 새 실행 명세 버튼을 사용할 수 있다는 뜻은 아니다.

1. **프로젝트 → 새 프로젝트**에서 이름과 목표를 입력하고 **PM과 시작**을 누른다. 프로젝트와 PM 요청을 저장하며, 개발을 시작하지 않는다.
2. PM은 서버에 등록한 카탈로그를 읽고 저장소·기준 커밋·허용 경로·검사·역할별 후보·예산을 제안한다. 질문이 있으면 대화로 좁힌다. 모델·추론은 요청 설정으로 표시하며 실제 적용·계정 적격성은 실행 근거로 따로 확인한다.
3. 프로젝트 설정의 **실행**에서 **PM 제안**을 읽고 **명세 저장**을 누른다. 서버가 허용 범위와 버전을 검사한다. 저장 자체는 계획 확정이나 실행 승인이 아니다.
4. **PM에게 새 계획 요청**을 누른다. 등록한 명세 버전으로 역할·담당 범위·완료 조건을 다시 제안받는다. 이전의 기술 제안을 자동으로 실행 계획으로 바꾸지 않는다.
5. **계획 검토·확정**에서 역할·완료 조건과 **실행 범위**의 버전·digest를 확인하고 **이 계획 확정**을 직접 누른다. 이 확인에 고정된 명세와 계획으로 실행 한 건을 생성한다.
6. 해당 실행 ID로 역할별 병렬 작업, 전달물, 검사, 승인된 원격 CI, 독립 검수, Astra 최종 검수, 보고와 후보 승인 요청을 대조한다. 계획 확정은 후보 수용·호스트 변경·배포·병합 승인이 아니다.

이번 단계에서는 최초 미등록 PM이 실행 명세를 제안한다. 등록된 명세를 바꾸려면 검토한 새 버전을 등록한 후 새 PM 계획을 요청한다. 등록 이후의 자유로운 명세 편집 화면이나 카탈로그 생성 UI는 포함하지 않는다.

## 신뢰 경계

카탈로그는 운영자가 로컬에서 고정한 `catalog_id → AutomationConfig`다. HTTP·PM 응답이 저장소 clone 경로, 검사 명령, 자격 증명, 계정 그룹, 모델의 검증 플래그나 격리 설정을 새로 정의하지 못한다. 카탈로그 항목의 존재는 그 저장소·계정·명령이 실제 실행에 적격하다는 증거가 아니며 worker가 실행 시 별도로 검사한다.

카탈로그 로딩 단계에서도 기존 `FlowSpec.validate_configuration`을 재사용해 지정된 Astra PM·최종 검수, 개발·독립 검수 모델/추론·Ultracode, 계정 하나의 보수적인 공유 한도 그룹, 필수 검사 연결을 확인한다. 이 검사는 메모리 내 계약 검증이며 저장소를 열거나 작업·모델 실행을 생성하지 않는다.

| 항목 | 명세에서 선택할 수 있는 범위 |
|---|---|
| 저장소·기준 | 하나의 로컬 카탈로그 ID와 정확한 digest를 참조. 저장소·브랜치·커밋 직접 교체 불가 |
| 허용 파일 | 카탈로그의 경로와 같거나 더 좁은 경로. 명시적 파일 또는 `/`로 끝나는 디렉터리 사용. 경로 탈출·glob·`.git`·`.github`·`.env` 등 거부 |
| 검사·원격 CI | 카탈로그의 필수 검사, argv·제한시간·CI 출처 규칙 전체 유지. 검사 삭제·명령 교체 불가 |
| 후보 | 개발·독립 검수 후보는 기존 순서를 지키는 비어 있지 않은 부분집합. PM·최종 검수 후보는 정확히 기존 풀 유지 |
| 역할별 담당 | `role_candidates`를 지정하면 실제 계획의 역할 key와 정확히 일치해야 함. 각 후보는 선택한 개발 풀의 순서를 지키는 부분집합 |
| 예산 | 비용·실행시간·실행 횟수·수정 횟수는 카탈로그 상한 이하. 계정 공유 한도와 기존 누적 사용량을 초기화하지 않음 |
| 요청·관측 | 카탈로그의 모델·추론·Ultracode는 요청 값. 실제 적용·동적 Workflow·계정 적격성의 관측 근거를 대신하지 않음 |

비용 미상이나 Codex 호출은 호출 전 정확한 금액을 보장할 수 없다. 보고된 비용과 실행 횟수·시간·동시 실행 예약을 함께 관리하며, 비용 미상을 0원으로 간주해 새 예산을 만드는 방식으로 우회하지 않는다. 명세 버전을 바꿔 같은 프로젝트의 이전 사용량을 지우거나 같은 계정의 cooldown을 새로 만들지 않는다.

**현재 실행기는 유한한 `max_cost_usd`가 있으면 Codex 후보를 제외하므로 지정된 Codex PM·최종 검수가 적격 후보 없음으로 차단된다.** 카탈로그/명세 화면에 이 제한을 표시한다. 금액 상한 설정을 지원한다는 사실과 이 구성으로 전체 PM 흐름을 실행할 수 있다는 주장은 다르다. 기존 정책을 완화하거나 PM·최종 검수자를 바꾸어 통과시키지 않는다.

## 불변 버전

명세는 `management_execution_specs`에 추가만 한다. 갱신·삭제는 SQLite 트리거로 거부하며 등록 요청의 재시도 영수증은 별도 표에 보관한다. 프로젝트의 현재 명세 참조만 새 버전을 가리킨다.

PM 요청은 생성 시점의 명세 참조를 저장한다. 계획 digest와 확정한 실행은 같은 `{version, digest, catalog_id, catalog_digest}`에 결합한다. 명세 원문에는 프로젝트 ID가 포함되므로 다른 프로젝트의 같은 버전 번호나 참조를 바꿔 끼울 수 없다. 명세가 없는 기존 요청·계획·실행에는 새 선택 필드를 끼워 넣지 않아 기존 digest와 전역 설정 경로를 보존한다.

- v1 계획을 아직 확정하지 않은 상태에서 v2를 등록하면 v1의 새 확정은 거부한다. 기존 PM 결과·원문을 수정하지 않는다.
- 이미 확정한 v1의 같은 확정 요청 재전송은 원래 실행 영수증을 반환한다. v2로 실행을 다시 만들지 않는다.
- PM 완료와 새 확정에서는 카탈로그·명세를 다시 확인한다. 카탈로그가 철회되면 새 실행으로 이어가지 않는다.
- 이미 설정 digest를 고정한 PM 요청은 카탈로그가 철회되어도 그 참조를 유지하며 차단 결과를 기록할 수 있다. 한 프로젝트의 차단 결과 저장 실패로 다른 프로젝트 조정까지 중단시키지 않는다.
- 늦게 연결되는 작업은 역할 → 원래 계획 → 원래 실행의 하네스와 명세에 연결한다. 프로젝트의 최신 하네스로 지난 작업을 다시 표시하지 않는다.

## HTTP 계약

기존 비밀번호 로그인, Host·Origin·CSRF 검사, 임시 비밀번호 변경 조건을 유지한다. 카탈로그 조회도 인증이 필요하다.

| 요청 | 결과 |
|---|---|
| `GET /api/execution-catalog` | `entries` 배열. 공개 요약과 카탈로그 digest. clone·자격 증명·계정 그룹 제외 |
| `GET /api/projects/{project_id}/execution-specs` | 해당 프로젝트의 `execution_specs` 이력 |
| `POST /api/projects/{project_id}/execution-specs` | `201`과 `execution_spec` 한 건. 프로젝트·PM·실행을 추가 생성하지 않음 |
| `POST /api/projects/{project_id}/plans/{plan_id}/confirm` | 기존 확정 payload에 해당 명세의 `execution_spec_digest` 추가. 명세 없는 기존 계획은 생략 |

등록 본문의 구조 예시다. 아래 ID·digest·파일·예산은 설명용이며 실제 등록되거나 승인된 값이 아니다. 실제 값은 해당 서버의 카탈로그와 PM 제안에서 읽는다.

```json
{
  "base_version": 0,
  "idempotency_key": "registration-example-001",
  "selection": {
    "catalog_id": "reviewed-project",
    "catalog_digest": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "allowed_paths": ["src/example.py", "tests/test_example.py"],
    "candidates": {"developer": ["approved-dev"], "reviewer": ["approved-reviewer"]},
    "role_candidates": {"implementation": ["approved-dev"], "tests": ["approved-dev"]},
    "budget": {"max_cost_usd": 3.0, "max_runtime_seconds": 600.0, "max_executions": 8, "max_repairs": 1}
  }
}
```

`base_version`은 처음에 0이며 이후 실제 최신 버전과 같아야 한다. 같은 인증 사용자·프로젝트·idempotency key·정규화 본문 재전송은 원래 결과를 반환한다. 같은 key의 다른 본문이나 동시에 달라진 기준 버전은 충돌로 거부한다. `allowed_paths`, `candidates`, `role_candidates`, `budget`은 생략할 수 있으며 카탈로그보다 넓어지지 않는다. 응답을 잃으면 같은 key와 본문으로 결과를 확인한다.

## 로컬 설치 입력

카탈로그 로더는 최대 2 MiB의 로컬 JSON 객체를 받으며 중복 key를 거부한다. 각 값은 검토한 전체 `AutomationConfig`다. 공개 요약이나 PM의 `selection`을 설정 파일 대신 넣을 수 없다. 파일 안의 clone 경로·계정 참조를 Git·공개 API·로그에 복사하지 않는다.

후속 적용을 준비할 때 운영자는 현재 승인된 실제 자동화 설정을 별도의 비공개 카탈로그 파일에서 하나의 ID에 매핑한다. 첫 운영 항목은 기존 pilot 저장소·기준 커밋·두 파일·필수 검사·후보·계정 그룹·예산을 그대로 보존해야 한다. 새 기능 구현이 다른 저장소나 더 넓은 실행 범위의 승인이 되지 않는다. 새 경로나 기존 파일 충돌, 기존 설정과의 차이는 적용안에 먼저 기록한다.

새 옵션은 다음 세 진입점에만 연결했다. 아래는 명령 형식이며 운영에서 실행한 기록이 아니다. 기존 인증·비공개 바인딩·공개 origin·상태 경로는 실제 고정안 그대로 전달해야 한다.

```text
ai-company manage serve [기존 관리 서버 인수] --execution-catalog <검토한 로컬 JSON>
ai-company automate tick [기존 자동화 인수] --execution-catalog <같은 내용의 로컬 JSON>
python -m ai_company.service_worker automation [기존 worker 인수] --execution-catalog <같은 내용의 로컬 JSON>
```

관리 API와 automation은 같은 카탈로그 digest를 읽어야 한다. 관리 컨테이너에는 파일을 읽기 전용으로 연결하며 API 포트를 인터넷에 직접 공개하지 않는다. 파일은 프로세스 시작 시 읽는 방어적 사본이며 실행 중 파일 변경을 자동 반영하지 않는다. 옵션을 생략하면 기존 전역 설정 경로를 유지한다. 번역 worker에는 이 옵션을 사용할 수 없다.

## 검증과 완료 조건

검증은 임시 SQLite·독립 로컬 Git 저장소·가짜 실행기를 사용한다. 운영 큐를 변경하거나 모델 계정의 한도를 고의로 소진하지 않는다. 실제 모델·원격 작업 CI의 전 과정 완료는 이 개발 회귀와 구분한다.

| 검증 축 | 관련 시험과 확인 경계 |
|---|---|
| 카탈로그·등록·API | [명세 회귀](../tests/test_execution_specs.py), [관리 HTTP 회귀](../tests/test_management_server.py): 범위 축소, 임의 설정 거부, 멱등 등록, 인증·CSRF, 불변 버전과 설정 고정 |
| 독립 검수 | [프로젝트 간 격리 시험](../tests/test_project_execution_isolation.py): v1/v2·프로젝트 간 참조·늦은 작업 연결·기존 영수증·공유 한도·누적 예산 |
| 자동 조정 | [프로젝트 자동화 회귀](../tests/test_project_automation.py): 실제 생성된 계획·실행·작업의 명세 연결, 역할별 후보, 다른 프로젝트 진행 |
| UI | [실행 명세 브라우저 시험](../tests/ui/execution_specs.cjs): 명세 제안·저장과 계획 확정 분리, 확정 payload, Light·Black·모바일 표시 |
| 기존 기능 | 기존 Python·UI·Android 준비 회귀. Android 준비 통과를 APK 실기기 조작 완료로 표시하지 않음 |

코드·PR·CI·독립 검수와 후보 패키지를 위 표와 검증 원장에 고정했다. 이 단계의 완료 조건인 **개발·격리 회귀·독립 검수·원격 CI·초안 PR·후속 적용/복구안**을 충족했다. 앱에서 마스터가 직접 완주한 실제 작업이나 일반 프로젝트의 운영 활성화를 완료 조건에 섞지 않는다.

## 후속 적용안

**이번 실행 명세 후보의 공개 웹·automation 교체와 카탈로그 연결은 아직 승인받거나 적용하지 않았다.** 기존 `5594744` 및 `28404b2` 고정 대상에 대한 승인을 다른 후보에 재사용하지 않는다. 새 후보·패키지·환경 차이를 구체적으로 고정한 뒤 달라진 대상만 검토한다.

다음 파일과 해시는 **준비만 완료한 후속 후보**다. release와 runtime 목적지에는 아직 설치하지 않았다.

| 고정 항목 | 값 |
|---|---|
| 코드 | `23ac8a723f6bfc4d67fe12949c9253e9f8acf04c` |
| 새 웹 이미지 | `sha256:0f44d2e0a3706567bafea34c640345264fcace697a5ce317405366380cbfa1f8` |
| 소스 archive SHA-256 | `34e11ba64cefaff9c9f68ff9040d74c0b157903e265196387e4e4dee2e545a89` |
| 비공개 적용안 SHA-256 | `d7797d7125a3717130f4a3dd57775eee153c708603d5437d2095e81eec66aae3` |
| 제안 Compose SHA-256 | `8b973c0ddd7bb140cf6997d77c61e5cdc440cfe128b2c75df4f31134d9080b1f` · `docker compose config --quiet` PASS |
| pilot 카탈로그 파일 SHA-256 | `cf5f200f62495262bbdffbc177a9eefddeb6581de48c87d7d0a554bbd4c13153` |
| 기존과 같은 유효 설정 digest | `2f925f653446f89b1b0d717f9a42e0d99a9df4660c90283a4cd2ba66a6d66415` |
| 설치 예정 release | `/home/edward/ai-company/releases/project-execution-23ac8a723f6b` |
| 설치 예정 카탈로그 | `/home/edward/ai-company/runtime/project-execution-23ac8a723f6b/catalog.json` |

비공개 패키지 위치는 이 작업트리의 `.ai-company/project-execution-package-20260916/23ac8a723f6bfc4d67fe12949c9253e9f8acf04c/`다. `release-source.tar`, `manifest.json`, `rollout-proposal.json`, `console.compose.proposed.json`, 두 `.service.proposed` 파일에 실제 변경을 담았다. 기존 파일이 있거나 직전 해시가 달라지면 덮어쓰지 않는다. 카탈로그는 UID/GID 1002 소유, 디렉터리 0700·파일 0600으로 준비해 기존 웹 실행 사용자 `1002:1002`를 유지한다.

실행 명세 적용의 변경 범위는 웹 이미지 1개, 카탈로그 읽기 전용 mount와 `--execution-catalog`, automation unit의 release·작업 디렉터리·카탈로그 인수다. 번역 표시 개선까지 적용할 때만 translation unit도 같은 release로 교체하고 저장 결과 재검사 2건을 실행한다. 전역 release symlink, 전용 환경변수와 원래 자동화·번역 설정 파일, 계정 그룹·예산, Caddy·외부 포트는 변경하지 않는다. 새 표는 `management_execution_specs`, `management_execution_spec_registrations`, `translation_saved_rechecks` 세 개이며 기존 DB를 교체하지 않는다.

위 후보는 앞서 승인한 코드와 다르므로 **실제 운영 교체는 새 후보에 대한 후속 승인 대상**이다. 이번 개발 단계의 완료를 이유로 실행하지 않는다.

1. 새 후보 커밋, 웹 이미지 digest, automation release, 비공개 카탈로그 내용 digest, Compose/unit의 변경 필드를 고정한다. 새 카탈로그에는 기존 승인 범위의 pilot 한 항목만 준비한다. 현재 web `28404b2`·workers `5594744`·Claude 설정과 다르면 차이와 영향을 먼저 기록한다.
2. 적용 직전에 현재 PM 요청·계획·확정 실행·역할 작업을 읽고, legacy 또는 고정 명세 참조·config digest·상태·예약 시각으로 분류한다. 새 버전에서 실제로 이어질 대상과 멈춰야 할 대상을 고정한다. 큐를 비우거나 이전 기록을 완료 처리하지 않는다.
3. 검토한 새 실행 접수 차단과 worker 정상 종료 절차로 조정 중·실행 중 작업과 자식 프로세스 상태를 확인한다. 단순히 화면을 닫은 것을 실행 정지로 취급하지 않는다. 종료 불명 작업을 새 프로세스로 중복 시작하지 않는다.
4. 별도 승인된 범위 안에서 새 automation release와 읽기 전용 카탈로그, 같은 카탈로그를 사용하는 관리 서버 이미지를 교체한다. 기존 Caddy 도메인·내부 연결·커플 앱·타이머·로그인·계정 그룹은 보존한다. translation 교체는 이 기능의 필수 변경이 아니다.
5. 실제 가동 코드·인수·catalog digest·fresh heartbeat·기존 사용량·cooldown·승인 보존을 대조한다. 공개 HTTPS 로그인 방식, Host·Origin·CSRF, 정적 파일, 인증된 카탈로그/명세 조회를 새 후보와 연결한다. 마스터의 목표 입력·계획 확정은 대행하지 않는다.

저장된 번역 결과의 별도 개선은 [저장 번역 재검사 적용·복구안](translation-saved-recheck-2026-09-16.md)을 따른다. 그 문서의 복사본 검증은 운영에 새 읽기 자료를 반영했다는 증거가 아니다. 새 번역 호출을 추가하지 않으며 실행 명세 기능의 적용과 자동으로 묶지 않는다.

## 복구 조건

**DB와 실행 이력을 과거 사본으로 덮어쓰지 않는다.** 새 명세 표·등록 영수증·PM 참조·계획·확정·실행·사용량·승인·이벤트를 보존한다. 이전 바이너리가 모르는 행이 있다고 삭제하지 않는다.

구버전 automation을 즉시 재시작하는 복구는 안전하다고 가정할 수 없다. 새 명세가 기존 pilot 설정을 그대로 선택하면 유효 config digest가 구버전 전역 설정과 같을 수 있다. 구버전은 새 명세 참조를 이해하지 못한 채 그 요청·실행을 소비할 위험이 있다. **명세에 묶인 새 요청의 소비를 막는 검증된 실행 접수 차단·대상 제한 또는 호환 guard가 없으면 worker 복구는 차단 상태**다. 새 참조를 지우거나 digest를 바꾸는 방식으로 우회하지 않는다.

고정 적용안의 실패 시 기본 조치는 automation을 정상 종료하여 새 자동 실행을 멈추고, 새 명세를 이해하는 관리 API·보고·승인 화면을 유지하는 것이다. 명세에 묶인 요청·계획·확정이 하나라도 생겼다면 구 웹과 구 automation을 단순 재시작하지 않는다. 구 웹 역시 새 명세의 확정 경계를 이해하지 못하기 때문이다. 기록이 없음을 확인한 초기 교체 실패에서만 보관한 기존 Compose/unit의 정확한 해시로 복원할 수 있다. 이미 생성된 명세·요청·실행·승인을 삭제하거나 DB snapshot으로 복구하지 않는다.

검토 가능한 최종 복구안에는 다음을 포함해야 한다.

- 현재 후보와 일치하는 이미지·release·unit·카탈로그 연결만 되돌릴 대상 목록.
- 새 작업을 받지 않는 상태의 증거, 실행·예약·종료 불명 작업 목록, 구버전이 이 작업을 소비하지 못하는 재현 검사.
- 새 UI만 되돌릴 경우 기존·신규 저장 기록의 읽기 호환성, 새 automation을 유지할 대상과 이유.
- 새 실행 접수·복구가 안전하다는 조건을 만족하지 못하면 현재 검토된 코드를 유지하고 해당 worker의 새 실행을 멈추는 절차. 기존 커플 서비스·번역·보고·승인까지 함께 중단하지 않는 범위.
- 번역 새 표시 선택까지 적용한 경우, 구버전 번역 worker 시작 **전에** 위 번역 문서의 새 코드로 표시 연결만 복원하는 순서.

후속 적용·복구 준비가 끝나더라도 APK 직접 조작과 서명키 외부 복원은 여전히 마스터 확인 항목이다. 최신 종합 상태는 [실사용 연결 현황](live-use-readiness-2026-09-16.md)에 이어 기록한다.

## 최종 화면 회귀 보완

`d1701c6`의 [화면 CI 35092681835](https://github.com/HyungwonPark/ai-company/actions/runs/35092681835)는 프로젝트 목록 갱신 뒤 생성 버튼의 키보드 표시 검사에서 실패했다. 목록이 DOM을 교체할 때 기존 포커스 복구가 ID가 있는 요소만 처리하는데 세 생성 버튼에는 ID가 없었다. `23ac8a7`에서 각각 고유 ID를 부여하고, 키보드 Tab 이동 뒤 실제 갱신으로 기존 DOM 분리를 강제한 다음 새 버튼의 포커스·표시 유지와 대화상자 종료 후 복귀를 확인하도록 회귀를 보완했다. 독립 검수와 [최종 화면 CI](https://github.com/HyungwonPark/ai-company/actions/runs/35093180287)가 통과했다. 제품 변경이 포함되어 위 패키지도 `23ac8a7`로 다시 만들고 격리 검사 33개와 Compose 문법 검사를 통과했다. 이전 `9e6c0c1` 패키지는 과거 검증 자료이며 적용 후보로 사용하지 않는다.
