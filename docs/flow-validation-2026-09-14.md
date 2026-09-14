# 2026-09-14 후속 구현 검증 기록

## 작업 기준과 서버

- PR #3 `e7ca36af3acfd9e92aa15cfde6201e2a0078cd28`, PR #4 `62bdc5a1191fee94914b8465207f2f2654365cd5`를 원격과 대조했다. PR #4는 열려 있다.
- 새 worktree: `/home/edward/ai-company/workspaces/agent-failover-loop`, 브랜치 `feat/agent-failover-loop`. PR #4 head에서 분기했다.
- 운영 worktree `workspaces/harness-loop-foundation`의 커밋·미커밋 상태와 기존 timer의 실행 경로를 보존했다. `/home/edward/ai-company` 자체는 Git worktree가 아니다.
- Ubuntu 24.04.4 LTS, Linux aarch64, CPU 2개, 메모리 11 GiB(확인 시 가용 약 8.5 GiB), Python 3.12.3 `/usr/bin/python3`, uv 0.12.3 `/home/edward/.local/bin/uv`.
- Codex 0.154.0: `/home/edward/.nvm/versions/node/v22.13.0/bin/codex`.
- Claude Code 2.1.270: `/home/edward/.local/bin/claude` → `/home/edward/.local/share/claude/versions/2.1.270`.
  기존 worker PATH와 이번 실행에서 같은 경로를 확인했다.
- 인증 파일·전체 환경변수는 출력하거나 저장소에 추가하지 않았다. 실행 증적 원문은 `.ai-company/model-probes/`의 비공개 로컬 파일로만 보관했다.

## 모델 설정: 확인된 사실과 미확인 범위

[공식 모델 설정](https://code.claude.com/docs/en/model-config#adjust-effort-level)에 따르면
`--effort ultracode`는 모델 추론 수준과 별개의 CLI 설정이며 xhigh 추론과 workflow 구성을 함께 요청한다.
2.1.203 이후 지원하므로 2.1.270 도움말에 없다는 사실만으로 미지원으로 판정하지 않았다.
`disableWorkflows`, xhigh보다 낮은 effort cap, 충돌하는 `CLAUDE_CODE_EFFORT_LEVEL`은 적용을 막을 수 있다.

기존 계정에서 다음 단일 실행 옵션으로 호출했다. 전역 설정을 변경하지 않았다.

```bash
/home/edward/.local/bin/claude -p --model claude-opus-5 --effort ultracode \
  --output-format stream-json --verbose '도구를 사용하지 말고 OK만 답하세요.'
```

exit 0, `result.success`, session `1e0736ef-35ba-4685-b56d-057d97e58f41`를 확인했다.
`system.init.model`/assistant metadata의 주 모델은 `claude-opus-5`다.
modelUsage에 나타난 Haiku 보조 사용량은 주 모델 대체로 분류하지 않는다.
stderr 경고는 없었다. 읽은 사용자/프로젝트/관리 설정 파일의 관련 키에는 충돌 값이 없었고
두 관련 환경변수도 설정되지 않았다. 원격 관리 정책/모델별 조직 cap의 실효값까지 확인한 것은 아니다.
응답에는 적용된 effort 또는 Ultracode boolean이 없었다. 옵션 수용 성공만으로 그 적용을 확정하지 않았다.

동일 Claude 세션을 `--resume`과 동일 모델·`--effort ultracode`로 재개해 성공했다.
다른 작은 읽기 작업(session `ef09f10a-5cc9-420e-9353-cdc8998cb615`)에서는 `Agent` 및 `Read` 도구 호출과
전용 cgroup 종료를 관찰했다. `Workflow` 도구는 init 목록에 없었고 호출도 관찰되지 않았다.
[공식 workflow 문서](https://code.claude.com/docs/en/workflows)에 따른 동적 workflow 활성화는 아직 미검증이다.
모델의 자기 설명을 설정/도구 실행의 근거로 사용하지 않았다.

Astra High/Ultra의 작은 호출은 `--model gpt-6-astra`와 각각 `model_reasoning_effort="high"`/`"ultra"`로 성공했다.
설치된 모델 목록에도 해당 수준이 있다. High session `01a09d0a-1331-71a1-aa32-45681639a2ac`를
명시 ID로 재개해 성공했다. 다만 exec JSONL에는 실제 model/effort 메타데이터가 없어 엄격한 결과 승인 조건은 충족하지 못했다.

임시 Git 저장소에서 실제 Astra High 개발 작업을 시도했다. CLI 세션은 종료했지만 구조화 결과는
`bwrap: loopback: Failed RTM_NEWADDR: Operation not permitted` 때문에 BLOCKED였다.
파일 변경/커밋은 없고 로컬 검사도 실패했다. 코드 변경, 실제 독립 검수, Astra 최종 승인까지의 실사용 루프는
이 환경에서 검증되지 않았다. 샌드박스 오류를 quota로 재시도하거나 권한 정책을 해제하지 않았다.
실제 quota를 소진하지 않았고 추가 유료 API 경로를 연결하지 않았다.

## 모의 테스트와 실제 호스트 타이머

기존 79개 회귀 테스트와 새 배정/증적/CLI 설정 테스트 37개, 총 116개가 로컬에서 통과했다.
`uv sync --frozen`과 기존 demo도 성공했으며 demo 결과는 `DEMO_READY`다.
`tests/test_dispatcher.py`의 PR #4 시나리오 대응은 다음과 같다.

| 설계 시나리오 | 검증 |
|---|---|
| 1/2 양방향 전환 | `test_01`, `test_02` |
| 3 공유 계정 한도 | `test_03`, 계정 alias 거부 |
| 4 모두 제한 | `test_04`, 반복 제한 재대기 |
| 5/6 PM·최종 검수 대기 | `test_05`, `test_06`: 독립 작업 계속 진행 |
| 7 원래 제공자 회복 | `test_07`: 소유권 유지, 일반 worker 자동 재개 차단 |
| 8 이관/실행/결과 도중 중단 | `test_08`, `08b`, `08c`, 검사 중단 guard |
| 9 살아 있는 이전 실행 | `test_09`, CLI 자식 프로세스 및 cgroup 검증 |
| 10 늦은 이전 결과 | `test_10` |
| 11 dirty/new files | `test_11`, 커밋되지 않은 변경의 승인 거부 |
| 12 session ID 유실 | `test_12`: 전체 체크포인트와 종료 확인 후 대체자 배정 |
| 13 오류 오인 금지 | `test_13`, 기존 CLI 오류 envelope/모델 텍스트 테스트 |
| 14 컨텍스트 부족 | `test_14`: 새 세션, quota 공유 상태 변경 없음 |
| 15 개발·검수·수정·최종 gate | `test_15`, 누락/변경 CI·자기 승인·커밋 변경 차단 |

생성한 subprocess CLI로 quota → DB 재개방 → 다른 제공자 새 세션 → 검사·독립 검수·최종 검수의
전체 모의 흐름도 실행했다. 이 결과는 `DEMO_READY`다.

실제 user systemd에서 별도 `ai-company-flow-proof-20260914.timer`를 예약했다.
검증 전용 큐 `/home/edward/ai-company/state/validation-flow-20260914`에 제한을 기록한 첫 프로세스가 종료했고,
08:39:01 KST에 timer가 새 프로세스를 시작했다. `codex exec resume timer-saved-session` 형태의 모의 CLI가
한 번 재개되어 검사 단계에 도달했다. 총 2회 실행, 누적 실행시간 약 0.19초, service exit 0/MainPID 0이었다.
예약 대기시간은 300초 실행 한도에 포함되지 않았다. 재현 도구는 `scripts/verify_flow_timer.py`다.

추가 호스트 검증에서 모의 CLI가 별도 process group의 `sleep` 자식을 남기고 quota로 종료하도록 했다.
호출별 systemd cgroup이 그 자식까지 종료했으며 `cgroup_stopped=true`, 살아 있는 자식 없음이 확인됐다.
이는 내부 workflow/자식 종료 관리의 모의 장애 검증이며 실제 계정 quota 이관 성공을 의미하지 않는다.

기존 `ai-company-quota.timer`는 enabled/active, service는 정상 종료/MainPID 0이다.
ExecStart는 계속 `workspaces/harness-loop-foundation/.venv/bin/ai-company session worker`이며 운영 큐는 `[]`다.
새 운영 flow timer는 템플릿만 추가했다. 이번 작업은 배포하지 않았고 검증용 일회성 timer만 실행했다.

## 재현 및 결과

```bash
uv sync --frozen
uv run --frozen python -m unittest discover -s tests -v
uv run --frozen ai-company demo --task examples/demo-task.json
```

최종 로컬 테스트 수와 원격 CI 실행 주소는 후속 초안 PR 본문에 기록한다.
실제 후보 활성화에는 모델/effort/Ultracode의 권위 있는 실행 메타데이터 연결,
현재 서버의 허용된 샌드박스 실행 경로, 해당 실제 작업의 원격 checkout attestation이 추가로 필요하다.
이 근거가 없는 후보를 다른 모델로 대체하거나 최종 승인을 생략하지 않는다.

추가 검수에서 재현한 두 결함의 수정과 후속 실사용 점검은 [PR #5 검수 수정](pr5-review-fixes.md)에 기록했다.
