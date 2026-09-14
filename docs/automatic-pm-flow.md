# 자동 PM 계획·역할 실행

이 변경은 `352a31a79223fb77f377dbec578651ac665001f2`를 보존한
`feat/auto-pm-flow` 초안이다. 운영 timer·queue·서비스 배포·자동 병합을 포함하지 않는다.

## 실행 경계

1. 관리 화면의 메시지 POST가 목표·대화 revision·활성 하네스 digest와 PM 요청을 함께 저장한다.
2. 별도 foreground `automate worker`가 서버 설정 digest를 먼저 고정하고, 기존 Dispatcher의
   planning 작업으로 Astra Ultra를 호출한다. HTTP는 모델을 기다리지 않는다.
3. PM의 구조화된 제안에는 역할별 목표·완료 조건·서로 겹치지 않는 출력 경로·의존 관계가 있다.
   서버 허용 경로를 벗어난 제안은 차단한다. 두 역할 이상은 독립적으로 시작할 수 있어야 한다.
4. 마스터가 화면에서 정확한 plan digest·하네스 버전을 확인해 확정하면 역할·활성 하네스·
   pending run을 원자적으로 저장한다. 오래된 제안과 다른 내용의 요청 키 재사용은 거부한다.
5. 조정기는 역할별 독립 clone과 immutable contribution 작업을 만든다. 필요한 선행 역할만
   기다린다. 기존 Dispatcher의 공통 두 실행 슬롯·계정별 공유 한도·중단 복구를 사용한다.
6. 개발자는 sandbox 안에서 허용 파일만 수정하고 커밋을 요청한다. `.git` 쓰기 권한을 주지 않는다.
   종료가 확인된 뒤 실행기가 경로·파일·원본 보고서·입력 HEAD를 확인하고 private index로 커밋한다.
   원본 보고서는 입력 SHA를 유지하고, 별도 `runner_commit` 증거가 실제 parent/tree/출력 SHA를 묶는다.
7. 모든 역할 결과를 journal로 복구 가능한 통합 clone에 모으고 program-owned CI 요청을 커밋한다.
   새로운 `automation/*` 브랜치의 draft PR만 게시한다. 기존 브랜치를 덮어쓰거나 force push하지 않는다.
8. 통합 후보의 로컬 검사·승인 workflow 원격 CI·독립 리뷰·Astra Ultra 최종 리뷰를 기존 Verifier로
   대조한다. 같은 후보의 check/artifact와 실행한 workflow 정의·run/attempt가 모두 일치해야 한다.
9. 수정 요청은 관련 역할로 돌아간다. 경로를 특정할 수 없는 공통 검사 실패는 모든 역할에 전달한다.
   수정할 역할의 새 작업은 최신 의존 결과를 포함하고, 이전 검수·후보는 이력으로 남긴다.
10. 실제 합격은 별도 승인 화면에 정확한 후보와 검증 digest를 표시한다. 이 승인은 개발 결과의
    수용 기록이며 배포·병합 권한으로 해석하지 않는다. fixture 완료는 승인 요청을 만들지 않는다.

## worker 사용

관리 HTTP와 worker는 같은 **전용 검증 state 디렉터리**를 사용한다. 운영 세션 큐를 복사하거나
timer를 바꾸지 않는다. HTTP 로그인 token은 별도 0600 파일로 제공한다.

```bash
uv run --frozen ai-company manage serve --state-dir "$VALIDATION_STATE" \
  --token-file "$CONSOLE_TOKEN_FILE" --host 127.0.0.1 --port 8765
uv run --frozen ai-company automate worker --state-dir "$VALIDATION_STATE" \
  --config "$AUTOMATION_CONFIG" --max-seconds 1800
uv run --frozen ai-company automate status --state-dir "$VALIDATION_STATE" \
  --config "$AUTOMATION_CONFIG"
```

`AutomationConfig`는 trusted local JSON이다. HTTP/PM으로 명령·저장소 경로·모델 정책을 받지 않는다.
필드는 `repository`, `source_clone`, `base_sha`, `base_branch`, `allowed_paths`, `checks`,
`agents`, `policy`, `ci`, `mode`, `max_parallel`, `pm_timeout_seconds`, `poll_seconds`다.
`checks`는 이름별 `argv`/`timeout_seconds`이며 실제 실행 전에 운영자가 승인한 명령을 고정한다.
`ci`에는 `workflow_path`, 파일 바이트의 SHA-256 `workflow_digest`, `required_checks`,
`artifact_name`을 고정한다. 게시 대상 base 브랜치의 실제 SHA는 `base_sha`와 같아야 한다.
새 workflow가 포함된 소스 커밋을 사용해야 한다.

조정기는 해당 설정과 확정된 run/현재 PM 요청의 작업만 실행한다. 기존·다른 설정의 작업을
동시에 시작하지 않는다. 전역 저장 결과 복구는 기존 공유 cooldown 일관성을 위해 유지한다.
worker 종료 뒤 예약은 SQLite에 남는다. 다시 같은 명령을 실행하면 같은 명세로 재개한다.
확인되지 않은 실행·외부 Git 변경·인증 실패는 무조건 재생하지 않고 차단 사유를 표시한다.

## 설정 근거와 보존

기존 `FlowPolicy`의 기본값은 `runtime_metadata`이며 strict 모델/effort/Ultracode 검증을 유지한다.
새 선택지 `configuration_evidence: cli_configuration`은 Codex 전용이다. Codex 0.154.0의
해당 세션·시도 시간 구간에 묶인 실제 rollout turn context가 요청한 모델/effort와 일치해야 한다.
`backend_model_verified=false`를 그대로 보존한다. 지원 모델 목록·모델 자기 설명은 근거가 아니다.
Claude의 지원 목록을 applied effort/Ultracode로 승격하지 않으며 이 정책의 후보로도 사용하지 않는다.

새 scope·정책 필드의 기본값은 직렬화에서 생략해 기존 task/spec/policy digest를 보존한다.
기존 full 흐름은 개발자가 커밋해야 하며 새 runner commit 프로토콜은 contribution에만 적용된다.
planning/contribution 완료는 각각 `PLAN_READY`/`CONTRIBUTION_READY`로 표시한다.
검사·검수를 생략한 이 상태를 `MERGE_READY`로 표시하지 않는다.

## 검증 기록

새 회귀는 실제 SQLite·독립 Git clone을 사용하되 모델/CI가 fixture임을 명시한다.
계획 확정 전 실행 금지, 병렬 두 역할, 후보 SHA 일치, 수정 역할 반환, 의존 결과 갱신,
사용량 이관, PM/역할 저장 경계 중단, fixture/live 설정 혼동 방지, 승인 요청 재시도를 검사한다.
브라우저 회귀는 PM 대기 중 보고·승인 접근, 오래된 계획, 프로젝트 전환, 응답 손실과 모바일 화면을 포함한다.

실제 모델·후보 PR·원격 CI·최종 검수 결과는 별도 실행 기록에만 기록한다.
이 설계 문서나 fixture PASS는 실제 완주·Ultracode·공개 도메인·Android 기기 검증을 뜻하지 않는다.
