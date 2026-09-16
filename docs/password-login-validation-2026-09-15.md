# 아이디·비밀번호 로그인 적용 기록

2026-09-15 KST. 사용자가 토큰 대신 아이디 `edward`, 임시 비밀번호 발급 후 변경을 요청했다.
실제 접속 주소는 **https://hyungwon.cloud**다. 비밀번호 원문은 이 문서·Git·PR에 저장하지 않는다.

## 최소 길이 후속 변경

사용자 요청에 따라 최소 길이를 **10자**로 변경했다. 이 최소 길이 변경의 실행 소스는
`92bbcf522d58e3f00bfa24505a7626e4da0f5db4`, 이미지는
`sha256:b97eb71b5e74fc6aefaf4e6cc639f8dbaed5bdbf294760bec6b731a8ddab45a0`이다.
로컬 인증 검사 7개와 [회귀 CI](https://github.com/HyungwonPark/ai-company/actions/runs/34907582731),
[브라우저 CI](https://github.com/HyungwonPark/ai-company/actions/runs/34907582728)가 통과했다.
9자 거부, 10자 변경·재로그인, 실제 배포본의 서버 검증과 HTTPS 화면의 10자 입력 제한을 확인했다.
계정 레코드와 기존 커플 서비스 프로세스는 그대로이며 계정 발급·비밀번호 변경을 대신 수행하지 않았다.
실제 적용 기록은 `.ai-company/password-min10-20260915/applied.json`에 보관한다.

## 최초 비밀번호 로그인 적용

- 실행 소스: `e429cd64e4fa6425e0a1b9d1e5e8e56f92c9da80` (초안 PR #8).
- 이미지: `sha256:dea3e4dd7832b786bd5142119285c5b44a3d35b7144b52d36a540bd225b13651`.
- 기존 공식 Python 기반 digest와 잠긴 의존성을 유지했다. 새 이미지의 API 컨테이너만 교체했다.
- `manage serve --password-login`으로 실행한다. 읽기 전용 토큰 마운트를 제거하고 state만 연결했다.
- `edward` 계정과 24시간 유효한 임시 비밀번호를 신뢰된 서버 실행기로 발급했다.
  계정과 출력 파일이 이미 있으면 덮어쓰지 않는다. 발급 파일은 개인 경로의 mode 0600이다.
- 실제 HTTPS 임시 로그인 200, 변경 필수 상태 유지, 프로젝트 API 403, 로그아웃과 세션 만료를 확인했다.
  실제 사용자의 새 비밀번호를 대신 정하거나 첫 변경을 수행하지 않았다.

첫 로그인 후 현재 임시 비밀번호와 새 비밀번호(10~128자)를 입력한다. 새 비밀번호 확인이
일치하면 서버가 변경하고 관리 화면이 열린다. 이후 상단 `비밀번호 변경`에서 다시 변경할 수 있다.
변경할 때 모든 이전 세션이 만료되므로 다른 기기에서도 새 비밀번호로 다시 로그인한다.
비밀번호 입력은 앱의 localStorage/sessionStorage·오프라인 캐시에 저장하지 않는다.

비밀번호는 [OWASP 권장 scrypt 설정](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)
N=2^17, r=8, p=1과 개별 무작위 salt로 저장한다. 임시 권한과 비밀번호 버전을 세션에서 대조한다.
기존 토큰 세션으로 우회할 수 없고, 계정이 있는 state는 새 서버의 토큰 모드 시작도 거부한다.
Host·Origin·CSRF·Secure/HttpOnly/SameSite=Strict 쿠키와 재시작 후 로그인 시도 제한을 유지한다.
암호 처리 동시 실행은 하나로 제한한다.

## 검증

- 로컬 Python 회귀 **248개 PASS**: 기존 241개와 신규 인증 경계 7개.
- [Python 3.11/3.12 회귀 및 Android 준비 CI](https://github.com/HyungwonPark/ai-company/actions/runs/34867491604) PASS.
- [실제 sandboxed Chrome 브라우저 CI](https://github.com/HyungwonPark/ai-company/actions/runs/34867491636) PASS.
  임시 로그인, 새로고침 후 변경 필수 상태, 관리 API 거부, 360px 변경 화면, 확인 입력 불일치,
  변경 완료와 새 비밀번호 재로그인, 후속 변경 화면, 기존 5개 관리 화면의 회귀를 확인했다.
- 최초 브라우저 검사는 인증 흐름을 통과한 뒤 로그아웃 후 이전 제목을 찾는 검사에서 실패했다.
  `f9735e3f54cad185cb124982bf2c163f4bfdd253`에서 제목 검사를 갱신하고 재로그인 검사를 추가했다.
  이 커밋은 테스트만 변경했으며 실제 실행 소스는 위 `e429cd6`이다.
- 배포와 같은 256MiB/CPU 0.25/비권한/네트워크 없음 컨테이너에서 별도 모의 계정 생성·로그인·변경 PASS.
  전체 5.0초, 최대 RSS 165104 KiB였다. 실제 계정의 첫 변경 증거와 구분한다.
- 실제 공개 HTTPS의 화면 200, 비인증 API 401, 비밀번호 로그인 모드와 커플 앱 307 이동을 확인했다.

## 보존과 경계

적용 전 Compose와 SQLite를 백업했다. 실제 업무 테이블의 행 수·내용 해시가 전후 동일했다.
프로젝트·PM 요청·계획·역할 작업·실행·사용량·승인·위임 기록을 변경하지 않았다.
PR #10 승인 `849f6de73d722f1ba2f4c5537111f6a0`은 **pending**이며 계획을 대신 확정하지 않았다.
기존 app/caddy/db/backup의 ID·PID·시작 시각·재시작 횟수와 Caddy/Compose 설정 해시가 동일했다.
컨테이너는 healthy, 재시작 0회, 읽기 전용 root와 cap-drop ALL을 유지하고 API 포트를 게시하지 않는다.
두 userns 제한값도 1이다. 운영 타이머·방화벽·AppArmor·도메인 설정은 변경하지 않았다.

개인 적용 기록은 `.ai-company/password-login-20260915/`의 `before.json`, `compose.before.json`,
`state.before.sqlite`, `release.json`, `applied.json`과 제한 환경 검사 기록이다.
실패 시 UI 컨테이너 설정만 이전 버전으로 되돌리는 적용 스크립트를 준비했고 실제 적용은 성공했다.
공유 DB를 과거 시점으로 되감는 원복은 수행하지 않는다.

실제 마스터의 목표 입력·PM 제안 확인·계획 확정은 여전히 마스터가 수행할 단계다.
이 로그인 검증은 후보 수용, 계획 실행, 배포·병합 승인이나 Ultracode/TWA 검증이 아니다.
