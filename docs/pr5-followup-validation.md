# PR #5 공유 한도 재시도와 서버 재검증

검증일: 2026-09-14. 작업 시작 시 로컬과 원격 PR #5의 HEAD는 모두
`0f0204dee6b94706f1180d72a00639ab1aa41dd0`이었다.
사용량 제한 결과 복구와 CI 출처 수정은 이 커밋에 이미 포함되어 있었다.

## 원래 두 결함 재현

최초 PR 커밋 `79af827`의 Dispatcher/Verifier를 별도 Python 모듈로 읽어,
현재 테스트의 같은 입력과 임시 Git 저장소·큐에서 비교했다. 기존 checkout을 되돌리지 않았다.

| 시나리오 | 최초 코드 | 현재 코드 |
|---|---|---|
| WAITING_QUOTA 저장 직후 중단, 부분 파일·사용량·작성자·cooldown 복구 | 실패 | 통과 |
| WAITING_RETRY 저장 직후 중단, 동일 세션 재개 전 한 번 집계 | 실패 | 통과 |
| 저장된 결과 이후 외부 파일 변경, 실제 사용량 보존 | 실패 | 통과 |
| 다른 논리 작업 배정 전에 미반영 공유 quota 복구 | 실패 | 통과 |
| 같은 검사 이름·증적 내용을 가진 미승인 workflow 거부 | 실패 | 통과 |

복구의 observation marker, 사용량·작성 이력, 공유 cooldown, 저장소 snapshot과
세션 체크포인트는 같은 SQLite 트랜잭션에 있다. 종료·guard를 확인하고
저장된 실행 snapshot을 실제 저장소와 대조한다. 외부 변경은 재조정 상태로 남긴다.
세부 구현은 [기존 수정 기록](pr5-review-fixes.md)을 참고한다.

## 추가로 재현하고 수정한 WAITING_RETRY 경로

일시 통신 오류로 대기한 A와 같은 계정을 쓰는 B가 quota 결과를 저장하고 중단되면,
재시작 시 B의 cooldown은 복구되지만 A의 `WAITING_RETRY` 재개 경로가 그 한도를
확인하지 않았다. A가 동일 세션을 재개해 공유 한도를 우회했다.
계정을 `DISABLED` 또는 `UNKNOWN`으로 기록한 경우에도 같은 실행 경로가 열려 있었다.

두 신규 재개 테스트에서 수정 전 실패를 확인했다. 이제 동일 세션을 실행하기 전에
현재 소유자의 후보 적격성과 공유 계정 상태를 확인한다.
사용할 수 없으면 기존 소유권·session ID·누적 사용량을 유지하고 영속 대기한다.
cooldown 해제 시각을 사용하며, 그 시각을 알 수 없으면 제한된 예약 간격을 적용한다.
일시 통신 오류 때문에 다른 제공자로 변경하지 않는다.

신규 Dispatcher 테스트 5개는 다음을 검증한다.

- 다른 작업의 미반영 quota를 복구한 뒤 재시도 세션도 공유 한도를 지킨다.
- DISABLED/UNKNOWN/COOLDOWN 동안 실행하지 않고, 회복 후 원래 세션을 재개한다.
- WAITING_RETRY 반영 트랜잭션 중 재중단되면 checkpoint·marker·집계가 함께 rollback된다.
- 반영 commit 직후 다시 중단돼도 기존 시도의 시간·비용·작성자를 재집계하지 않는다.
- 결과 저장 후 외부 파일이 바뀌면 사용량을 보존하고 재조정을 요구한다.

CI 테스트 3개도 추가했다. 승인 ID와 다른 workflow 경로·실행 저장소·head·run ID,
다른 check suite나 job의 run/head/name/상태, 조회 중 변경된 PR head/run attempt를 거부한다.
기존의 미승인 workflow·실행 정의 digest·artifact 출처 검증도 유지한다.

## 같은 worker 환경의 bwrap 재현

운영 service에서 PATH만 읽어 다음 transient 설정에 적용했다.
`Type=exec`, `KillMode=control-group`, `TimeoutStopSec=5`, `UMask=0077`,
검증 worktree의 WorkingDirectory이며 각 호출에 별도 `ai-company-run-*.service`를 사용했다.

현재 worker PATH는 `~/.local/bin`을 먼저 찾으므로 Codex는 이전 연결 설정 단계에 설치된
standalone `0.154.0`이다. 실제 경로는
`~/.codex/packages/standalone/releases/0.154.0-aarch64-unknown-linux-musl/bin/codex`다.
기존 npm 설치본도 `0.154.0`, Claude Code는 `2.1.270`이다.
이번 검증에서는 설치본이나 운영 PATH를 변경하지 않았다.

| 최소 호출 | 결과 |
|---|---|
| npm bundled bwrap + user/pid/net namespace + /usr/bin/true | exit 1, RTM_NEWADDR 권한 오류 |
| standalone bundled bwrap + 같은 옵션 | exit 1, 같은 오류 |
| worker에서 선택된 codex sandbox linux -- /usr/bin/true | exit 1, 같은 오류 |

bwrap 옵션은 다음과 같다.

```bash
/path/to/bwrap --unshare-user --unshare-pid --unshare-net \
  --ro-bind / / --proc /proc --dev /dev /usr/bin/true
```

15:23 KST의 새 kernel audit에 `profile="unprivileged_userns"`의
`net_admin`·`setpcap` 거부가 기록됐다.
`kernel.unprivileged_userns_clone=1`,
`kernel.apparmor_restrict_unprivileged_userns=1`이며,
배포판 `/usr/bin/bwrap`과 bwrap 전용 프로필은 미설치 상태다.
세 호출의 cgroup 종료를 모두 확인했다.

[공식 Codex 문서](https://learn.chatgpt.com/docs/sandboxing)에 따른 조치를
패키지 설치 전까지 구체적으로 검토했다.
Ubuntu 저장소의 `bubblewrap 0.9.0-1ubuntu0.1`과
`apparmor-profiles 4.0.1really4.0.1-0ubuntu0.24.04.7`을 검증 폴더에 다운로드·해제했다.
패키지의 `bwrap-userns-restrict`는 root가 소유하는 `/usr/bin/bwrap`에 적용되며
자식 실행에는 `unpriv_bwrap`을 중첩하여 capability를 거부하는 정의를 포함한다.
이는 Codex의 파일·네트워크 제한을 대신하지 않는다.

```bash
apparmor_parser --skip-kernel-load --skip-cache \
  /private/extracted/usr/share/apparmor/extra-profiles/bwrap-userns-restrict
```

문법 검사 exit 0, stderr 없음.
프로필 SHA-256은 `11d39094f044f0cda0febb3ad517b830301da6b2ce929664af09ee9e4dd264f9`다.
패키지 설치·커널 프로필 로드는 수행하지 않았다. 전역 userns 제한이나 AppArmor를 끄지 않았다.

호스트에 조치를 적용하는 유지보수 단계에서는 배포판 bwrap 설치, 위 전용 프로필의 설치·로드,
worker가 배포판 bwrap을 실제 선택하는지 확인이 필요하다.
그다음 최소 명령 성공과 함께 작업 경로 밖 쓰기·허용되지 않은 네트워크 차단,
global userns 제한값 1 및 cgroup 종료가 모두 유지되는지 검증해야 한다.
현재 문법 검사 성공을 실제 샌드박스 복구 성공으로 간주하지 않는다.

## 실제 설정 근거와 작은 개발 호출

모든 호출은 기존 계정, worker PATH, 호출별 cgroup 및 역할별 기존 권한 옵션을 사용했다.
인증 파일·전체 환경변수를 읽거나 공개하지 않았다.

| 확인 계층 | Astra High | Claude Opus 5 / Ultracode |
|---|---|---|
| 요청 옵션 수용 | CLI exit 0 | CLI exit 0 |
| CLI가 남긴 해당 실행 설정 | 해당 turn_context의 gpt-6-astra/high | initialize에 Opus 5와 xhigh 지원 목록 |
| 응답의 실제 주 모델 | JSONL metadata 없음 | assistant/system에서 claude-opus-5 확인 |
| 실제 적용 effort/Ultracode | backend effort metadata 없음 | 적용 effort/Ultracode metadata 없음 |
| 동적 Workflow | 해당 없음 | init 도구 목록에 없음, 실행 관찰 없음 |

`configuration_evidence.py`가 수집한 Codex turn 설정을 backend 실행 증거로 승격하지 않았다.
Claude의 지원 목록도 적용 설정으로 간주하지 않는다.
[공식 모델 설정 문서](https://code.claude.com/docs/en/model-config#adjust-effort-level)는
Ultracode가 xhigh와 workflow 구성을 함께 요청하며, 환경변수·모델 지원·조직 cap 때문에
적용이 제한될 수 있음을 명시한다.
[공식 Workflow 문서](https://code.claude.com/docs/en/workflows)는 `-p`의 도구 실행에도
권한 평가가 적용됨을 설명한다. 프롬프트의 키워드나 모델 자기 설명으로 동작을 판정하지 않았다.

별도 임시 저장소에서 정수 목록 합산 함수 구현·테스트·커밋을 Astra High에 요청했다.
세션 `01a09ea9-8de0-7153-91c5-b1401975c947`의 첫 sandbox 명령이 실패했다.
구조화 결과는 `BLOCK`이며 HEAD
`e21023fbfc824f1b4108cafb66fcbbc047ea57f2`와 작업 파일이 그대로였다.
개발 검사·커밋은 실행되지 않았다. CLI 종료 category `success`를 개발 성공으로 바꾸지 않았다.

별도 Claude 읽기 진단 세션 `dcb30a4b-957a-45a7-876a-fc9afcb9762c`은
Opus 5로 두 번의 `Read` 호출을 수행했다.
`--effort ultracode` 옵션은 수용했지만 `Workflow` 호출과 실효 Ultracode 값은 확인되지 않았다.
두 실제 호출 모두 cgroup 종료를 확인했다.

따라서 실제 개발→검사→독립 검수→Astra 최종 검수의 완주는 여전히 차단된다.
개발 결과가 없는 상태에서 후속 검수 단계를 실행하거나, 이 읽기 진단을 독립 승인으로 세지 않았다.
샌드박스 호환성, 실행 설정 증거, 실제 후보 커밋의 원격 attestation이 남은 조건이다.

## 최종 검증과 운영 보존

- `uv sync --frozen`: 성공.
- 전체 148개 테스트: 34.004초, 모두 통과(기존 140 + 신규 8).
- 새로운 private 상태 디렉터리의 기존 데모: `DEMO_READY`.
- 명시 승인된 호스트에서 가상환경 회귀 검증을 실행했다. 실제 CLI의 sandbox 성공을 뜻하지 않는다.
- 기존 quota timer는 enabled/active, timer/service 파일 digest와 운영 checkout 상태가 검증 전후 동일했다.
- 운영 session 큐는 읽기 전용 조회로 0건이며 내용 digest도 동일했다.
- 새 운영 flow timer 배포·기존 큐 변경·자동 병합은 수행하지 않았다.

비공개 원문은 `.ai-company/pr5-followup-2c04c489/`에 보관했다.
새 커밋의 원격 CI 실행 주소와 상태는 PR #5 본문에 기록한다.
