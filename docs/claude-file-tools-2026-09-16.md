# Claude 파일 도구 · 2026-09-16

**Claude가 격리된 파일 도구로 담당 파일을 읽고 수정하는 데 성공했다. 비담당 파일 쓰기는 같은 실제 세션에서 거부됐다.** 기존 [내장 Bash 오류](claude-native-sandbox-2026-09-16.md)는 그대로이며, 운영 개발 대체자 등록·이관·재개까지 완료한 것은 아니다.

## 구현

[`workspace_files.py`](../src/ai_company/adapters/workspace_files.py)는 승인된 `/usr/bin/bwrap`과 단일 AppArmor 프로필을 사용한다. 모델에는 실행 명령·마운트·환경 변수·권한 옵션을 받지 않는 `read_file`, `write_file`만 제공한다. 실제 내용 처리는 고정된 Python 코드가 격리 안에서 수행한다. 검사 실행은 기존 검증기가 맡으며 새 셸 도구를 노출하지 않는다.

- 저장소는 읽기 전용이고 지정 파일의 고정된 inode만 쓰기 가능하다. 호스트 루트·홈·인증 경로·서비스 소켓은 마운트하지 않는다. PID·네트워크 등 namespace를 분리하고 capability를 모두 제거한다.
- 자식은 내용을 읽거나 쓰기 전에 `bwrap//&unpriv_bwrap (enforce)`, `CapEff=0`, `CapBnd=0`, `NoNewPrivs=1`을 확인한다. 바이너리·프로필 SHA-256과 기존 두 sysctl 값도 검사한다. 호환되지 않으면 중단한다.
- 경로 탈출·숨김/자격 증명 경로·symlink·hardlink·FIFO·다른 사용자 파일·변경된 작업 공간을 거부한다. 내용은 최대 64KiB의 UTF-8이며 코드로 평가하지 않는다.
- 새 파일은 존재하는 부모 아래에 독점적으로 빈 파일을 만든 뒤 격리 안에서 내용을 쓴다. 실패 후 생긴 부분 작업은 남기며 기존 파일을 삭제하거나 과거 내용으로 되돌리지 않는다. 상위 폴더 생성·삭제 도구는 제공하지 않는다.
- 실행 제한은 12초이고 출력·CPU·core dump를 제한한다. 정상 종료와 시간 초과 모두에서 분리된 자식 프로세스가 남지 않는지 실제 호스트에서 검사했다.

[`workspace_mcp.py`](../src/ai_company/adapters/workspace_mcp.py)는 로컬 stdio 통신만 제공한다. 역할에 쓰기 경로가 없으면 쓰기 도구도 목록에 나타나지 않는다. 감사 파일은 작업 공간 밖에 독점 생성하고 요청 시작·완료·실패를 fsync한다. 내용 대신 요청 인수와 결과 파일의 digest를 기록한다. 다른 명령·추가 인수·큰 메시지는 거부한다. [공식 Claude MCP 연결](https://code.claude.com/docs/en/mcp), [MCP stdio 규격](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)

이 모듈은 **아직 Dispatcher·운영 설정에 연결하지 않았다.** 기존 `cli_configuration` 정책을 바꾸거나 과거 Claude 후보를 소급 적격 처리하지 않는다. 공개 적용 승인 대기 중인 `c2b3fec` 이미지·worker에도 포함되지 않는다.

## 실제 검사

| 검사 | 결과 |
|---|---|
| 새 도구 검사 15개 | 모두 PASS. 실제 bwrap 검사 3개 안에 파일·네트워크·종료 시나리오 포함 |
| 전체 회귀 336개 | 서버에서 모두 PASS. 이후 비문자 도구명 거부 보완은 해당 MCP 검사 4개와 전체 파일 도구 검사 15개를 재실행해 PASS |
| 허용 파일·옆 파일·외부 파일 | 허용 파일만 쓰기 성공. 옆 파일 쓰기·외부 읽기/쓰기는 거부 |
| 네트워크 | 호스트의 loopback TCP 연결 성공을 대조군으로 두고, 격리 안 TCP·호스트 abstract Unix socket 연결은 거부 |
| 자식 종료 | 별도 세션으로 분리한 자식이 정상 반환·시간 초과 후 모두 없음 |
| 실제 Claude | 비담당 파일 쓰기 실패 → 담당 파일 읽기 → 쓰기 → 재읽기 성공. 도구 기록·감사 기록·파일 digest 일치 |

기본 원격 CI에서는 검토된 서버 바이너리·AppArmor를 요구하는 호스트 검사 3개를 명시적으로 skip한다. 이를 원격 격리 PASS로 세지 않는다. 호스트 검증 명령은 `AI_COMPANY_TEST_BWRAP=1 uv run --frozen python -m unittest discover -s tests -p 'test_workspace_*.py' -v`다.

최종 소스의 실제 Claude 세션은 `78cc064e-8c4a-4010-868f-a1a11173f9bf`다. native 도구 목록은 `mcp__company_files__read_file`, `mcp__company_files__write_file` 두 개뿐이며 `company_files` 서버가 connected였다. 비담당 요청은 native `tool_result.is_error=true`와 감사 실패로 연결되고 파일이 생기지 않았다. 마지막 담당 파일은 `VALUE = 11`과 줄바꿈이며 SHA-256은 `4d411a302a03aae11d89bbaf1e57eaf4713fc802752316eb0082c4c6016690f0`다.

실행 전후 공식 CLI `get_settings.applied`는 Opus 5·xhigh·Ultracode 활성값으로 일치했다. 주 API 요청마다 같은 세션·모델·effort를 계측했고 제목 생성 보조 요청은 분리했다. native 호출과 감사 요청은 순서·인수 digest·결과 digest·실행 시간 창으로 대조했다. 제공자 내부 모델의 암호학적 증명이나 운영의 새 증거 계약을 대신하지 않는다.

`--restricted`, `dontAsk`, `--strict-mcp-config`와 명시한 MCP 한 개, 빈 내장 도구 목록, hook/skill 비활성 설정을 사용했다. `--safe-mode`는 사용자 지정 MCP도 비활성화하므로 이 연결에는 사용하지 않았다. CLI의 OS 샌드박스 설정은 `enabled=true`, `failIfUnavailable=true`, `allowUnsandboxedCommands=false`를 유지했다. 명령 실행·파일 처리의 격리를 해제하거나 전역 설정·권한을 변경하지 않았다.

탐색은 CLI 3회, 비용 추정 합계 USD `0.2036885`였다. 첫 실행은 CLI가 예약된 서버명 `workspace`를 거부해 도구 호출이 없었으며 성공한 파일 검사로 세지 않는다. 이름을 `company_files`로 바꾼 사전 확인 후, 최종 소스 SHA-256을 실행 전후 고정한 위 경계 검사를 수행했다. 각 호출의 비용 상한은 USD `0.25`, 실행 상한은 60초였다. 모든 실행의 cgroup·계측 수집기가 종료됐다. 계정 한도 소진이나 운영 quota 주입은 수행하지 않았다.

## 남은 연결

새 버전의 정책에 공식 적용값·요청별 모델/effort·파일 도구 구성·작업/시도 식별자를 연결해야 한다. 그다음 별도 큐에서 모의 한도와 실제 Claude 이관·동일 세션 재개를 대조한다. 이번 고정 fixture는 실제 개발·독립 코드 검수·Workflow 자식의 파일 작업이나 마스터 APK 확정 검증으로 세지 않는다.

[비밀 없는 증적](evidence/claude-file-tools-2026-09-16.json)에 최종 소스, native 도구 호출, 감사·결과 digest, 테스트와 운영 대조를 보관한다. 원자료는 서버 `.ai-company/claude-tool-bridge-20260916/`에 있다. 두 worker PID·재시작 횟수·release·공개 이미지·커플 서비스·타이머·큐 상태별 개수·PR #10 `pending`은 이전과 같다. 공개 적용과 외부 서명키 복구·실제 마스터 조작은 계속 별도 대기 항목이다.
