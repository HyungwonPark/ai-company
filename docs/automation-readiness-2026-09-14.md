# 자동 PM 실행과 공개 연결의 증거 경계

2026-09-14, `feat/auto-pm-flow` 구현 중의 읽기 점검이다. 이번 점검은 모델을 호출하거나
설정·호스트·DNS·서비스·운영 큐를 변경하지 않았다. 새 자동 PM 실사용 실행 결과는
그 실행의 별도 기록으로 갱신해야 하며, 이 문서가 완주를 증명하지 않는다.

## 설치 CLI와 실행기가 증명하는 범위

`--version`과 `--help`를 실제 설치 경로에서 읽었다.

| 항목 | 확인한 사실 | 증명하지 못하는 사항 |
|---|---|---|
| Claude | `/home/edward/.local/bin/claude` → `.local/share/claude/versions/2.1.270`, `2.1.270 (Claude Code)` | 해당 계정의 모든 서버 관리 정책 |
| Codex | `/home/edward/.local/bin/codex` → `.codex/packages/standalone/releases/0.154.0-aarch64-unknown-linux-musl/bin/codex`, `codex-cli 0.154.0` | 실제 backend 모델·추론 적용값 |
| Claude 도움말 | `--effort`에 low/medium/high/xhigh/max, `--settings`, `--setting-sources` 제공 | 도움말 목록에 Ultracode가 없다는 이유만으로 지원을 부정할 수 없음 |
| Codex 도움말 | `--model`, `--config`, profile 및 strict-config 경로 제공 | 옵션 수용만으로 backend 적용 확인 불가 |

현재 실행기는 [session_cli.py](../src/ai_company/adapters/session_cli.py)에서 Codex 모델과
`model_reasoning_effort`, Claude `--effort ultracode` 또는 지정 effort를 요청한다.
[configuration_evidence.py](../src/ai_company/adapters/configuration_evidence.py)는 요청과
다른 범위의 관측을 보존한다. 모델이 답변에 쓴 자기 설명은 관측 metadata로 사용하지 않는다.

| 증거 수준 | 현재 획득 경로 | 판정 |
|---|---|---|
| supported | Claude initialize의 모델/effort 목록; Codex model catalog 스키마 | 실행 가능 설정의 광고이며 해당 turn의 적용 확인이 아님 |
| requested | 실행기의 argv와 고정 AgentProfile | 실행기가 요청한 설정 |
| CLI applied configuration | Codex rollout `turn_context`의 모델/effort. version·session·cwd·실행 시간에 결합 | CLI가 기록한 turn 설정이며 `backend_model_verified=false` 유지 |
| runtime observed | Claude 응답의 주 모델 metadata 등 실제 event 필드 | 해당 필드만 관측; initialize의 `applied_effort`, `applied_ultracode`는 null |
| backend verified | 별도 신뢰 가능한 실제 실행 metadata 필요 | 현재 자료로 모델·effort·Ultracode 전체를 검증했다고 표시 불가 |
| dynamic Workflow | 실제 Workflow 호출과 실행/자식 작업·권한·종료 증거 | 정적 검수에서 호출 0회; 기능 사용 검증은 남음 |

기존 [runtime-validation](runtime-validation-2026-09-14.md)의 실제 작은 작업은
`535d5c529d59dc59789d8216822c7773be79ecde`를 대상으로 개발·고정 검사 5개·Claude 독립
검수·Astra 최종 검수를 수행했다. 개발 high와 최종 ultra는 Codex CLI turn 설정으로
관측했으며 backend 검증값은 false였다. 해당 후보는 push하지 않아 원격 CI가 없다.
이전 감독 진단의 성공을 이번 자동 PM 전체 흐름 성공으로 재사용하지 않는다.

현재 [FlowPolicy](../src/ai_company/flow_contracts.py)에는 명시적인
`configuration_evidence="cli_configuration"` 선택이 추가되었다. 기본값
`runtime_metadata`와 `AgentProfile.verified()`의 엄격한 의미를 유지한다.
[Dispatcher](../src/ai_company/dispatcher.py)는 CLI 정책에서 Codex/non-Ultracode만
허용하고 세션·실행 시간·turn ID·model/effort를 확인하며 상충하는 runtime metadata를
거부한다. 이는 정책 digest에 결합되는 더 좁은 증거 계약이다. 기존 불변 정책을 바꿔
과거 실패를 합격시키거나 CLI 기록을 backend 검증으로 승격하는 근거가 아니다.
Claude Ultracode의 실효값 공백은 이 선택으로 해결되지 않는다.

## Ultracode와 동적 Workflow의 남은 확인

공식 문서는 CLI 2.1.203 이상에서 `--effort ultracode`가 xhigh와 Workflow 자동 조정을
요청한다고 설명한다. `CLAUDE_CODE_EFFORT_LEVEL`의 다른 값, Workflow 비활성화,
모델 지원과 조직 effort 상한 때문에 모드가 꺼지거나 낮은 effort가 적용될 수 있다.
따라서 옵션 수용과 실효 설정을 분리한다.
[Claude 모델 설정](https://code.claude.com/docs/en/model-config#adjust-effort-level)

비밀값을 제외한 allowlist만 읽었다. 현재 셸의 `CLAUDE_CODE_EFFORT_LEVEL`과
`CLAUDE_CODE_DISABLE_WORKFLOWS`는 미설정이었다. 사용자 settings에서 effortLevel,
ultracode, disableWorkflows, 두 env 키, Opus 5의 per-model effort는 지정되지 않았다.
이 worktree의 `.claude/settings.json`·`.claude/settings.local.json`과
`/etc/claude-code/managed-settings.json`은 없었다. 전체 설정이나 자격 증명은 출력하지
않았다. 이 관찰은 다른 실행의 환경·ancestor 설정·원격 조직 상한까지 없다는 증거가 아니다.

`-p` 프롬프트에 `ultracode` 단어만 넣는 방식은 사람 입력의 opt-in으로 취급되지 않는다.
현재 실행기의 명시적 CLI 옵션과 구분해야 한다. Workflow는 비대화형 실행에서도 도구
권한 평가를 거치며 자식 도구에 격리가 적용된다. 정적 검수는 Read/Glob/Grep만으로
수행하므로 Workflow 호출이 없다는 결과로 동적 실행 완료를 주장할 수 없다.
[Claude Workflow 공식 절차](https://code.claude.com/docs/en/workflows)

다음 별도 검증은 제한된 과제·예산·전용 workspace에서 공식 설정을 요청하고,
initialize의 supported 값과 실제 적용 event를 따로 기록해야 한다. Workflow가 실제
호출되면 호출 ID, 실행 단계, 자식 작업, 허용 도구, 수정 범위, 종료를 결합한다.
실효 metadata가 여전히 없으면 unknown을 유지한다. 권한 우회나 전역 설정 변경을
동적 동작의 증명 수단으로 사용하지 않는다.

Codex의 로컬 생성 스키마도 좁혀 읽었다. `ModelListResponse`는 지원 effort 목록과 기본값의
형식이며 현재 실행 응답이 아니다. `ModelVerificationNotification`의 verification enum은
`trustedAccessForCyber`만 있고, 이름과 달리 임의 모델/effort의 backend 인증서가 아니다.
`ModelReroutedNotification`에는 fromModel/toModel 및 사유가 있지만 스키마의 존재만으로
현재 run에서 재라우팅이 없었다고 단정할 수 없다. 검사한 스키마 위치는
`/tmp/codex-remote-pin-schema/v2/`이며 원시 세션은 공개 문서로 복사하지 않았다.
공식 configuration 표의 effort 열거에는 ultra가 없으므로 그 표로 Astra Ultra 적용을
대신 검증하지 않는다. 실제 CLI turn 관측은 위 수준으로 보존한다.
[Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)

## 자동 PM 완주 뒤 공개 연결에 필요한 산출물

우선 같은 candidate의 PM 계획, 역할별 기여·검사·수정, 독립 검수, Astra 최종 검수와
승인 workflow의 원격 run/check/artifact가 task·policy·base·head에 결합되어야 한다.
실제 quota 영속 대기와 독립 역할의 계속 실행, 재개 후 중복 기여/PR/승인 방지도
별도 실행 근거로 남긴다. 단위 테스트와 모의 DEMO_READY는 이 실행 증거의 대체물이 아니다.

[domain-twa-plan](domain-twa-plan.md)에 기록된 기존 운영 서비스 기준을 보존한다.
그 문서의 DNS·자원·서비스 상태는 앞선 관찰 시점의 기록이며 이번에 재조회한 상태가 아니다.
기존 `/home/edward/talenta-site`의 Caddy/Compose, 커플 DB·secret·release 경로와
운영 timer/queue를 자동 PM 공개 연결에 공유하거나 변경하지 않는다.

| 공개 연결 전 준비 | 남은 구체적 결과 |
|---|---|
| 신규 서버 release | 독립 image digest·state 경로·UID·CPU/memory/pids/log 상한, 비공개 기능 검사 및 rollback |
| 도메인/TLS | hyungwon.cloud 소유권·현재 용도·A/AAAA/CAA/TTL·대상 IP 확정, Caddy 추가 vhost/network diff, 별도 승인 후 TLS 검증 |
| 로그인/승인 | 신규 master identity와 secret 보관, production session/CSRF/권한·만료·head 변경·재전송 검사, API 비공개 경계 |
| 웹 설치 | 현재 manifest는 SVG icon만 선언. 192/512 PNG 및 실제 origin·scope·모바일 offline/재연결 검증 필요 |
| Android 소유권 | 새 application ID 확정(제안 cloud.hyungwon.aicompany), 신규 key 담당자·백업·배포 채널·실제 설치 APK signer |
| Digital Asset Links | 실제 signer로 생성한 JSON의 HTTPS200/no redirect와 정확한 package/origin 대조; unset/미승인 인증서 음성 검사 |
| 실제 TWA | 별도 테스트 단말 설치/update·로그인 유지/로그아웃·cold start/back·App Links와 Chrome TWA validation·승인 재전송 검사 |

현재 [manifest](../src/ai_company/web/manifest.webmanifest)와
[service worker](../src/ai_company/web/sw.js)는 정적 shell만 대상으로 하며 API/승인 쓰기를
캐시하지 않는다. 이것만으로 Android 설치·연결 성공을 표시하지 않는다.
기존 커플 앱 `life.talentaedward.app`과 key를 재사용하지 않는다. 참고 저장소의
`android/build.sh`는 서명 시 기존 release 게시까지 수행하므로 새 빌드는 artifact 생성과
게시를 분리해야 한다.

이번에 `uv run --frozen python -m unittest discover -s deploy/examples -p 'test_*.py' -v`의
4개 준비 테스트가 다시 통과했다. 빈/잘못된 지문, 기존 앱 ID 재사용, 기존 파일 덮어쓰기를
거부하고 정상 합성 지문을 정규화했다. 실제 키·assetlinks 게시·APK 설치 검증은 수행하지
않았다. 운영 공개 연결 승인은 위 release/config/artifact를 검토 가능하게 만든 뒤 별도로
받으며, 자동 병합이나 운영 배포는 이 문서 작성 범위에 포함되지 않는다.
