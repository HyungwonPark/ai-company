# 서버 세션에서 이어서 할 작업

기준일: 2026-09-13, v0.1

2026-09-14 추가: 서버 환경과 기반 검증 이후 SQLite 세션 큐·CLI 프로세스 실행기·사용자 타이머를 추가했다. 현재 운영 경로, 자동 대기·재개와 모의 타이머 검증은 [quota-resume.md](quota-resume.md)를 먼저 확인한다. 아래 내용은 최초 기반 인수인계 당시의 기록이다.

## 현재 위치

이 대화에서 하네스·루프의 첫 시뮬레이션 코드를 작성하고 별도 환경에서 검증했다. Oracle에는 접속하지 않았다. 서버의 기존 Claude Code tmux 세션은 사용자가 운영 중이다.

계획은 `docs/bootstrap-plan-2026-09-13` 브랜치, 구현은 `feat/harness-loop-foundation` 브랜치에 있다. 구현 PR은 계획 브랜치를 기준으로 하며, 두 PR 모두 검토 전 초안이다. 원래 두 설계·인수인계 파일은 원문을 확보하면 현재 계획과 대조한다.

## 기존 SSH·tmux에서 시작

기존 SSH 경로로 서버에 접속한다. ai-company의 별도 작업 디렉터리와 tmux 세션에서 진행한다. 기존 서비스나 Claude 작업 공간을 동시에 수정하지 않도록 분리한다.

먼저 아래 정보를 조회한다. 설치·서비스 변경을 하지 않는 확인 단계이다.

```bash
hostname
id
pwd
uname -m
cat /etc/os-release
nproc
free -h
df -h /
command -v git
command -v python3
command -v uv
command -v claude
command -v codex
```

현재 PATH에 있는 도구는 버전을 확인한다. 전체 환경변수, 인증 파일, 토큰은 출력하지 않는다. Codex가 없거나 인증되지 않았으면 실제 OS·CPU·계정 조건에 맞춰 준비한다.

서버 Codex CLI에 다음 작업 지시를 전달하면 된다.

> HyungwonPark/ai-company의 feat/harness-loop-foundation 브랜치에서 AGENTS.md, README.md, docs/server-handoff.md, docs/harness-foundation.md와 계획 문서를 읽어라. 기존 서비스와 작업 세션을 유지하며 서버 환경을 조회하라. 먼저 잠긴 환경에서 테스트와 모의 데모를 재현하고, 그 결과를 바탕으로 Claude Code·Codex 비대화형 어댑터의 실제 실행·취소·인증·권한 격리 설계를 구체화하라. 모의 검증을 실제 CLI·원격 CI 검증으로 간주하지 마라.

로그인한 서버 CLI는 별도 세션이다. 이 웹 대화가 그 tmux에 자동 연결되지 않으므로 저장소 문서·작업 명세·커밋으로 맥락을 전달한다.

## 다음 구현 완료 조건

1. 각 CLI의 정확한 버전·인증 방식·비대화형 결과 규격을 확인한다.
2. 하네스의 역할·맥락·결과 계약에 실제 어댑터를 연결한다.
3. 작업 계정·권한·작업 경로와 실제 자원 제한을 검증한다.
4. 성공, 인증 실패, 한도 초과, 시간 초과와 하위 프로세스 종료를 구분한다.
5. 그다음 실제 GitHub 변경·필수 CI·커밋별 검수로 병합 준비를 판정한다.

자동 병합·배포와 금융 프로젝트 재개는 현재 시험 범위에 포함하지 않는다.
