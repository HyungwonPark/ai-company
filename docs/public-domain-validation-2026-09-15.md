# hyungwon.cloud 실제 연결 기록

2026-09-15 KST. 사용자가 우선순위를 변경하고 같은 서버의 도메인 분기 적용을 명시적으로
승인했다. 이 승인은 새 UI·관리 API 연결에만 적용하며 PR #10 후보 수용·병합 승인은 아니다.

## 적용 결과

- 실제 UI: **https://hyungwon.cloud**. 검증된 TLS 연결에서 화면 200, 로그인 200,
  인증된 기존 overview 200, 로그인하지 않은 프로젝트 API 401을 확인했다.
- 기존 **https://talenta-edward.life**는 종전과 같은 307 `/login?next=%2F`를 반환한다.
  app/caddy/db/backup의 컨테이너 ID·PID·시작 시각·재시작 수·상태는 보존됐다.
- PR #10 승인 `849f6de73d722f1ba2f4c5537111f6a0`은 **pending**이다. 적용 전후
  프로젝트·PM 요청·계획·실행·승인·위임·flow·session job 문서가 모두 동일하다.
- 새 목표 입력·계획 확정·후보 승인 API는 호출하지 않았다. 실제 마스터의 UI 조작은 다음 단계다.

서버 DNS 조회에서 두 A 레코드는 `161.118.250.112`였고 hyungwon.cloud의 AAAA/CAA 응답은
비어 있었다. TLS hostname과 신뢰 체인을 실제 클라이언트에서 검증했다. 인증서는
Let's Encrypt YE2, SAN `hyungwon.cloud`, 만료 `2026-12-13T14:35:18Z`였다.

## 실행본과 전용 내부 연결

API 소스 커밋: `bf4f760864445ef7ae3a106971fd01ce7d27c380` (PR #8).
실행 이미지 ID: `sha256:b52530847d3a680486976c0549f553f76f5331ecbbaf8b58617acc89abdc8bfe`.
공식 Python 기반 digest:
`python@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e`.
소스는 Git archive로 분리했으며 잠긴 의존성을 설치했다. 이미지에 모델 자격 증명은 없다.

```text
Internet :80/:443
        │
        └─ existing Caddy
             ├─ talenta-edward.life → existing app:3000 (original private network)
             └─ hyungwon.cloud → 172.30.88.3:8766 (ai-company-control)
```

`ai-company-control`은 `172.30.88.0/29`의 **internal** Docker bridge다.
Caddy(`172.30.88.2`)와 새 `ai-company-console`(`172.30.88.3`)만 연결했다.
Caddy의 기존 기본 게이트웨이는 그대로이며 새 네트워크 gateway priority는 -1이다.
Docker internal network의 통신 경계는 [Docker 공식 설명](https://docs.docker.com/reference/cli/docker/network/create/#network-internal-mode---internal)을 따른다.

API 컨테이너는 UID/GID 1002, root filesystem read-only, 모든 capability 제거,
no-new-privileges, memory 256MiB, CPU 0.25, PID 64와 로그 상한으로 실행한다.
16MiB `/tmp` tmpfs, 기존 검증 state, 읽기 전용 로그인 token만 연결했다.
Docker socket·기존 커플 데이터·호스트 home·모델 자격 증명은 마운트하지 않는다.
호스트에 게시한 API 포트가 없고 호스트 8765/8766 listener도 없다.
컨테이너는 healthy이며 `unless-stopped` 재시작 정책을 사용한다.

기존 Caddyfile 끝에 새 도메인 블록만 추가했고, Compose에는 Caddy의 새 네트워크 연결과
external network 정의만 추가했다. 다른 서비스의 해석된 Compose 설정은 동일하다.
기존 Caddy 컨테이너는 재생성하지 않고 [검증 후 reload](https://caddyserver.com/docs/command-line#caddy-reload)했다.
프록시는 [공식 reverse_proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)를 사용한다.

## 공개 origin과 인증

CLI가 `--public-origin https://hyungwon.cloud`를 실제 서버에 전달한다.
별도 `--private-bind`는 HTTPS origin을 요구하며 특정 RFC1918 IPv4 주소만 허용한다.
기본 loopback 제한을 유지하고 wildcard·공개 IP·link-local 주소는 거부한다.

Host와 Origin은 설정된 origin에 정확히 일치해야 한다. Forwarded 헤더로 origin을 변경할 수 없다.
실제 로그인 쿠키의 Secure·HttpOnly·SameSite=Strict, 잘못된 Origin/CSRF 요청 403,
인증된 조회 및 정상 로그아웃을 확인했다. 신규 회귀는 잘못된 origin·포트·credential/path와
불허 인터페이스가 DB 생성 전에 거부되는 것도 검사한다.

[소스 CI](https://github.com/HyungwonPark/ai-company/actions/runs/34861601342)와
[UI CI](https://github.com/HyungwonPark/ai-company/actions/runs/34861601353)가 통과했다.
로컬 관련 관리/PM 회귀 26개도 통과했다. 기존 복구·사용량·독립 검수·CI 출처 계약은 바꾸지 않았다.
별도 `Deployed console connectivity` CI는 배포된 도메인에 인증 없는 GET만 보낸다.
그 결과는 현재 연결 확인이며 해당 CI 커밋이 배포됐다는 증적이나 마스터 UI 검증이 아니다.

## 실제 실패와 원복

처음에는 전용 bridge의 호스트 주소에서 API를 실행하려 했다. 다음 두 실패에서 새 연결을
즉시 원복했고, 기존 도메인 블록을 적용하기 전에 중단했다.

1. 컨테이너 BusyBox `ip route show default`가 전체 경로를 출력해 추가 내부 경로를 기본 경로
   변경으로 오인했다. 새 네트워크를 제거한 뒤 실제 `default` 행 비교로 보완했다.
2. 호스트 API까지의 내부 연결 검사가 실패했다. 새 API 서비스와 network를 제거했다.
   저장된 호스트 iptables에는 입력 REJECT 규칙이 있었고, 이를 변경하지 않고 API를
   전용 내부 컨테이너로 옮겼다. 이 최종 구성에서 Caddy→API 및 HTTPS 인증 검사가 통과했다.

원복은 새 API 서비스/컨테이너·네트워크만 대상으로 했으며 DB를 과거로 되돌리지 않았다.
호스트 방화벽·전역 권한·userns 제한·AppArmor 설정은 바꾸지 않았다.
두 userns 제한값은 1, bwrap 프로필 hash와 기존 운영 큐/타이머 설정·상태도 그대로다.

비공개 적용 디렉터리:
`/home/edward/ai-company/workspaces/auto-pm-flow/.ai-company/public-domain-20260915/`.
여기에 `Caddyfile.before`, `docker-compose.yml.before`, `console.compose.json`,
`release.json`, `applied.json`, 검사 기록과 `rollback-domain.py`를 보관한다.
원복 스크립트는 현재 설정 hash와 새 컨테이너 ID가 일치할 때만 추가 연결을 제거한다.
다른 변경이 생겼다면 덮어쓰지 않고 중단한다. 사용자의 실제 후속 목표·승인·작업 기록은 삭제하지 않는다.

## 로그인과 다음 직접 검증

브라우저에서 **https://hyungwon.cloud**를 연다. SSH 터널은 필요하지 않다.
로그인 토큰은 이전 검증 화면과 같은 개인 파일에 있다. 서버에서 다음 명령으로 읽어
브라우저 로그인 칸에 입력한다. 토큰을 Git·PR·채팅 검증 기록에 붙여 넣지 않는다.

```bash
cat /home/edward/ai-company/workspaces/auto-pm-flow/.ai-company/master-ui-20260914/login-token
```

새 프로젝트를 만든 뒤 매니저에 목표를 저장하고 실제 PM 제안을 기다린다.
역할·허용 경로·하네스·완료 기준을 확인한 뒤 마스터가 직접 `이 계획 확정`을 클릭한다.
이전 위임을 사용하지 않는다. 기존 검증 실행 설정의 두 파일 허용 범위와 함수 규약은
앞서 준비한 `master-ui-20260914/README.md`의 목표 예시를 따른다.
계획 확정은 PR #10 후보 수용이나 배포·병합 승인이 아니다.

PM은 기존 운영 타이머를 바꾸지 않은 30분 foreground 검증 worker이며,
이번 준비에서는 **2026-09-15 01:05:31 KST**까지 새 작업을 배정한다.
그 이후 요청은 영속 대기하며 worker 재개가 필요하다. UI 컨테이너는 계속 동작한다.
Ultracode·동적 Workflow·실제 마스터 클릭·TWA 서명/실기기 연결은 아직 완료로 표시하지 않는다.
