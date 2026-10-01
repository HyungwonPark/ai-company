# 2026-09-14 호스트 격리 적용 및 실제 실행 검증

## 결과와 범위

승인된 bubblewrap 패키지와 단일 AppArmor 파일을 적용했고, 호스트 검증은
`HOST_VERIFIED`로 완료했다. 두 전역 제한값은 모든 관측 시점에 `1`이었다.
기존 운영 timer·queue·커플 서비스 구성은 유지했고 배포·자동 병합은 하지 않았다.

별도 로컬 Git fixture에서 실제 SessionQueue/Codex 개발 → 독립 sandbox 검사 →
Claude 독립 정적 검수 → Astra 최종 정적 검수를 감독하에 실행했다.
모델 호출과 실제 파일·프로세스 결과를 확인했지만, 제품의 자동 Dispatcher/PM/UI 흐름을
완주한 결과나 원격 CI에 연결된 병합 준비 상태는 아니다.

기계 판독 결과: [runtime-20260914.json](evidence/runtime-20260914.json).
개인 원시 기록은 `.ai-company/runtime-applied-20260914/`에 보존했다.
이 경로에는 실패·거부 결과도 포함하며 Git에는 올리지 않았다.

## 적용과 호스트 보존

사용자가 전용 tmux 터미널의 sudo에 직접 인증했다. 비밀번호를 채팅·환경 변수·
`.env`에 받거나 저장하지 않았고 sudoers·그룹 권한도 바꾸지 않았다.
같은 터미널에서 hash로 고정한 적용 코드를 실행했다. 작업 완료 후 해당 터미널에서
`sudo -k`를 수행하고 비대화형 sudo가 거부됨을 확인한 뒤 전용 tmux 세션을 정리했다.

| 대상 | 적용 내용 |
|---|---|
| 패키지 | `bubblewrap 0.9.0-1ubuntu0.1`, 신규 1개, 다른 패키지 변경 없음 |
| DEB SHA-256 | `3fb4ca3a8d2060444836568ed49d6897a403467e4ba29c93440900093fb96a38` |
| 설치 바이너리 | `/usr/bin/bwrap`, root:root 0755, setuid/setgid·file capability 없음 |
| 바이너리 SHA-256 | `ae27935781511400c65ebcc0b4669775d602f46251b8707c947a1ac1b160c1c8` |
| 단일 파일 | `/etc/apparmor.d/bwrap-userns-restrict` |
| 프로필 SHA-256 | `11d39094f044f0cda0febb3ad517b830301da6b2ce929664af09ee9e4dd264f9` |
| 파일이 정의하는 kernel profile | `bwrap`, `unpriv_bwrap` 모두 enforce |
| 기존 AppArmor 상태 | 사전 127개 프로필의 mode 유지, 기존 bwrap 정의·파일·local include 없음 |

root `dpkg --audit`는 깨끗했고 dpkg 잠금 충돌이 없었다. simulation의 Inst/Conf 대상은
bubblewrap뿐이었다. 패키지 작업은 강제 timeout으로 끊지 않고 완료를 기다렸다.
프로필은 exclusive create로 설치해 기존 파일 덮어쓰기를 방지했다. 설치 후
`dpkg --verify bubblewrap`도 통과했다.

패키지 postinst가 `kernel.unprivileged_userns_clone` 관련 전역 설정을 재적용한다.
**전역 설정을 전혀 건드리지 않았다는 주장은 하지 않는다.**

| 관측 시점 | unprivileged_userns_clone | apparmor_restrict_unprivileged_userns |
|---|---:|---:|
| root 적용 직전 | 1 | 1 |
| package postinst 직후 | 1 | 1 |
| profile 로드 직후 | 1 | 1 |
| 호스트 검증 완료 시점 | 1 | 1 |

운영 timer는 active/enabled, 큐 0건과 digest가 동일하다.
timer/service/Caddy/Compose 파일 hash도 동일하다. app/caddy/db는 running/healthy,
backup은 running이며 healthcheck가 없다. 컨테이너 ID·StartedAt·PID·RestartCount가
모두 적용 전과 같다. 원복이 필요한 호스트 검증 실패는 없었으며 실제 원복은 실행하지 않았다.

## 격리 검증

worker와 같은 edward(uid1002), PATH, Type=exec, KillMode=control-group,
TimeoutStopSec=5, UMask=0077, 독립 WorkingDirectory로 실행했다.
진단에는 RuntimeMaxSec=45 상한을 추가했다. Codex 0.154.0의 일회성 permission profile은
root read / 해당 workspace write / network disabled이며 사용자 설정 파일은 바꾸지 않았다.

| 검사 | 실제 결과 |
|---|---|
| 최소 bwrap + true | exit 0; 기존 loopback RTM_NEWADDR 오류 해소 |
| 자식 정책 | `unpriv_bwrap` 중첩 확인, CapEff=0 |
| 허용 쓰기 | workspace 파일 생성과 호스트의 내용 확인 성공 |
| 같은 UID 외부 쓰기 | 외부 형제 경로는 호스트에서 쓰기 가능; sandbox에서는 EROFS(errno30), 파일 없음 |
| 네트워크 | 동일 `1.1.1.1:443` 호스트 연결 성공, sandbox EPERM(errno1); timeout으로 판정하지 않음 |
| 분리된 자식 종료 | start_new_session=True 자식과 부모의 PID/start ticks 관측 후 cgroup·해당 PID identity 소멸 |
| 중단 대비 | 모든 진단 unit은 시작 전 fsync 기록, finally 정리와 별도 종료 재조회 |

비모델 진단 unit 7개와 실제 개발·검사·검수 unit 6개, 총 13개를 최종 재조회했다.
모두 LoadState=not-found, ActiveState=inactive, MainPID=0이었다.
독립 검수자도 원시 진단 결과와 7개 unit의 소멸을 읽기 전용으로 재확인했다.

## 작은 실제 작업

후보는 문자열의 소문자화·공백 정리·하이픈 연결·문장부호 보존을 수행하는
`normalize_title` 함수다. 미리 고정한 테스트 5개는 worker가 수정하지 않았다.
작업 공간·큐·프로세스 기록은 운영 디렉터리와 분리했다.

로컬 후보 SHA: `535d5c529d59dc59789d8216822c7773be79ecde`.
이 후보는 push하지 않았으며 원격 CI 증거가 없다.

| 단계 | 실제 실행 및 결과 |
|---|---|
| 개발 | 실제 SessionQueue + run_session, Codex 요청 gpt-6-astra/high; SESSION_COMPLETED, 구현 파일 생성 |
| 독립 검사 | 네트워크 차단 Codex sandbox에서 고정 unittest 5개 통과, 후보 커밋 고정 |
| Claude 독립 검수 | 별도 세션의 실제 Read/Glob/Grep, 같은 SHA에 category=success / PASS / findings=[] |
| Astra 최종 검수 | 별도 세션의 실제 git rev-parse/status/show 읽기, 같은 SHA에 category=success / PASS / findings=[] |
| 실행 종료 | 개발·검수 기록의 cgroup_stopped=true, 최종 unit 재조회 종료 확인 |

개발 session: `01a09fa2-7561-7101-b88d-4f7d63d55a33`.
Claude 합격 session: `1b35cf85-4620-457d-a5a1-0d171a6deb3c`.
Astra 합격 session: `01a09faa-dc23-7f91-9f7c-4d8debe17a8f`.

### 추가 재현과 처리

- 첫 Claude 검수는 PASS JSON을 반환했지만 context-mode 실행 도구 호출이 거부되어
  adapter category=permission이었다. **합격으로 사용하지 않았다.**
  별도 새 세션에서 정적 검수의 허용 읽기 도구를 명시했고 권한 변경 없이 success/PASS를 얻었다.
- 첫 Astra 요청에 Claude 전용 도구명이 포함되어 실제 파일을 읽지 못하고 BLOCK했다.
  그 기록을 보존하고 Codex가 제공하는 읽기 명령에 맞춘 별도 새 요청을 실행했다.
  실제 명령 이벤트에서 후보 SHA, clean status, source/test 내용을 확인한 뒤 PASS를 얻었다.
- 두 재실행은 감독하의 새 진단 요청이다. 기존 거부/BLOCK 기록을 수정하거나
  세션 큐의 non-retryable 판정을 자동 재시도 정책으로 완화하지 않았다.
  이 provider별 요청 보정은 제품 PM/역할 하네스에 자동 연결된 구현을 뜻하지 않는다.

## 설정 증거의 수준

| 항목 | 이번에 확인한 근거 | 남은 한계 |
|---|---|---|
| Codex CLI | 0.154.0, 실제 실행 경로와 session/cwd/time에 묶인 rollout | CLI 설정과 backend 확인은 별개 |
| 개발 설정 | turn_context의 gpt-6-astra/high | backend_model_verified=false |
| Astra 최종 설정 | turn_context의 gpt-6-astra/ultra | backend_model_verified=false |
| Claude CLI | 2.1.270, 요청 claude-opus-5 / xhigh / Ultracode | 요청 수용은 실효 설정 보장이 아님 |
| Claude 주 모델 | 해당 응답 metadata의 claude-opus-5 | effort·Ultracode 실효값은 없음 |
| Claude initialize | Opus 5 및 xhigh 지원 목록 | applied_effort=null, applied_ultracode=null |
| 동적 Workflow | 두 실제 Claude 검수에서 호출 0회 | 작고 정적인 과제였으며 동적 동작은 미검증 |

모델의 자기 설명은 설정 검증에 사용하지 않았다.
[Codex 구성 reference](https://learn.chatgpt.com/docs/config-file/config-reference)와
[Claude effort 설정](https://code.claude.com/docs/en/model-config#adjust-effort-level)은
해석 근거이며, 위 실행에 대한 metadata를 대신하지 않는다.

## 기존 회귀와 기록 검수

호스트 적용 후 기존 unittest 168개를 다시 실행해 모두 통과했다(37.997초).
중단 복구·공유 한도·이관·CI 출처 검증을 포함한 기존 코드에 변경은 없다.
독립 검수자는 공개 요약을 원시 로그·후보 SHA·실행 종료·기준선과 대조했고
필수 수정 사항이나 비밀 노출·완료 범위 과장을 발견하지 않았다.

## 남은 연결

- 실제 Claude effort·Ultracode 적용 metadata 및 적절한 과제의 동적 Workflow 증거.
- 실제 PM 소비기·역할별 하네스와 작업 제출·관리 UI 연결, 자동 Dispatcher 전체 흐름 검증.
- 같은 실제 산출물의 승인 workflow 원격 CI와 필수 check/artifact를 연결하는 실사용 검증.
  기존 PR #5의 복구·공유 한도·이관·CI 출처 검증 코드는 변경하지 않았다.
- hyungwon.cloud의 운영 연결, 별도 Android 서명·Digital Asset Links·실기기 검증.
  이 기록은 운영 timer 전환·서비스 배포·자동 병합 승인이 아니다.
