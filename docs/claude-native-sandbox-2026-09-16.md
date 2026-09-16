# Claude 개발 격리 · 2026-09-16

**Claude 로그인·Opus 5·xhigh·Ultracode·병렬 Workflow는 확인됐지만, 현재 서버의 Claude 내장 Bash는 격리 초기화에서 실패한다. 개발 대체자로 활성화하지 않았다.** [실제 설정·Workflow 기록](claude-runtime-settings-2026-09-16.md)과 별개인 도구 실행 문제다. 기존 AI Company의 bubblewrap 검사 성공을 취소하거나 전체 서버 격리가 고장났다고 해석하지 않는다.

후속 [별도 파일 도구](claude-file-tools-2026-09-16.md)는 기존 bubblewrap·프로필을 유지하며 실제 Claude의 담당 파일 읽기/쓰기와 비담당 거부를 검증했다. 아래 native Bash 실패 기록은 그대로 보존하고 이 대안의 성공으로 덮어쓰지 않는다.

## 결과

| 단계 | 관측 결과 |
|---|---|
| 설치 CLI | `2.1.270`, 바이너리 SHA-256 `7bf9f33acc124df9abccf6f2366397a82a740378d535fa12d426fa77fdbc9946` |
| 기존 worker 환경 | `bwrap` 있음, `socat` 없음. `failIfUnavailable=true`이면 모델 요청 전에 종료 1 |
| 검증 전용 의존성 | Ubuntu `socat 1.8.0.0-4ubuntu0.1 arm64`를 비공개 디렉터리에 압축 해제하고 해당 프로세스 PATH에만 추가. 초기화·설정 조회는 종료 0 |
| 실제 Claude Bash | 고정된 진단 명령 1회 호출. `apply-seccomp`의 `/proc/self/setgroups` 접근 거부로 종료 1. 진단 파일은 실행되지 않음 |
| 모델 없는 최소 명령 | 같은 systemd 사용자 실행 조건에서 bubblewrap + `true`는 종료 0, 같은 bubblewrap + 내장 helper + `true`는 같은 오류로 종료 1 |
| 운영 보존 | 두 worker의 PID·재시작 횟수, 공개 이미지·release 링크, 커플 서비스·Caddy·DB·백업 컨테이너, 기존 타이머 동일 |

실제 Bash 세션은 `f3e05936-b239-40af-b335-934ac283ebdd`다. CLI의 최종 분류는 `success`이지만 native `tool_result.is_error=true`와 tool-use ID `toolu_017Rp3prvpAqfE31K2w9ZGpW`가 명령 실패를 증명한다. **CLI가 응답을 끝낸 사실을 도구 검사 PASS로 세지 않는다.** 이 추가 실행의 주 요청은 2회, 제목 생성 보조 요청은 1회이며 CLI 추정 비용은 USD `0.080055`다. 이후 최소 재현·추적에는 모델을 호출하지 않았다.

## 재현

다음 명령을 기존 worker와 같은 사용자 systemd 실행에서 `NoNewPrivileges=yes`, `KillMode=control-group`, 최대 10초로 실행했다. 환경은 비우고 모델 프롬프트·인증 정보를 전달하지 않았다. `ARGV0`는 설치 CLI에 내장된 helper 진입점이다.

```bash
/usr/bin/bwrap --new-session --die-with-parent \
  --unshare-user --cap-drop ALL --unshare-pid --unshare-net \
  --ro-bind / / --proc /proc --dev /dev --chdir / --clearenv \
  --setenv ARGV0 apply-seccomp -- \
  /home/edward/.local/share/claude/versions/2.1.270 /usr/bin/true
```

마지막 helper를 `/usr/bin/true`로 바꾼 대조군은 성공했다. 대조군의 AppArmor는 `bwrap//&unpriv_bwrap (enforce)`, `CapEff=0`, `CapBnd=0`, `NoNewPrivs=1`이다. 추적하지 않은 재현의 실패를 주 증거로 삼았다. 별도 `strace`에서는 PID·mount namespace 생성 `EPERM` → user namespace 생성 성공 → `setgroups` 열기 `EACCES`를 확인했다. 같은 재현 시각의 커널 감사 로그에는 `unpriv_bwrap` 프로필이 `2.1.270` 프로세스의 `sys_admin` capability를 거부한 기록이 있다. 추적 자체의 `sys_ptrace` 거부는 원래 실패 원인과 구분한다.

공식 helper 구현은 추가 PID·mount 격리를 생성할 수 없으면 중단한다. 이 서버의 감사 로그와 최소 재현은 기존 자식 프로필의 capability 제한에 걸린 경로와 일치한다. 관련 upstream 이슈는 참고 자료이며, 제안된 capability 추가를 검증된 해결책으로 채택하지 않았다. [고정한 공식 helper 소스](https://github.com/anthropics/sandbox-runtime/blob/0bab820dace6809fb5cfe117b6ae2693bf5858b5/vendor/seccomp-src/apply-seccomp.c), [관련 공개 이슈](https://github.com/anthropics/sandbox-runtime/issues/498)

`socat`은 공식 CLI 샌드박스의 의존성이지만 초기화 성공만으로 명령 격리가 검증되지는 않는다. 실제 호출에는 `enabled=true`, `failIfUnavailable=true`, `allowUnsandboxedCommands=false`, `strictAllowlist=true`, 빈 허용 도메인을 지정했다. 진단 명령 전에 중단됐으므로 허용·비허용 쓰기, 읽기 차단, 네트워크 차단, 진단용 자식 종료 검사는 **미실행**이다. 실행기 cgroup과 계측 수집기 종료만 확인했다. [공식 샌드박스 안내](https://code.claude.com/docs/en/sandboxing)

## 다음 조치

현재 Claude 개발 후보의 부적격 상태를 유지한다. 후속 검증은 먼저 모델 호출 없는 이 최소 명령으로 시작한다. 공식 실행기의 호환 수정 또는 기존 회사 bubblewrap을 사용하는 제한된 작업 도구 연결을 검토하고, 실제 경로·네트워크·자식 프로세스 검사를 통과한 뒤 버전이 분리된 설정 증거 정책에 연결한다. 어느 대안도 이번 기록에서 구현·승인·적용한 것으로 표시하지 않는다.

`enableWeakerNestedSandbox`, `allowAllUnixSockets`, 샌드박스 비활성화, capability 추가, AppArmor 프로필 확장은 적용하지 않았다. 두 기존 제한값 `kernel.unprivileged_userns_clone=1`, `kernel.apparmor_restrict_unprivileged_userns=1`과 프로필 SHA-256 `11d39094f044f0cda0febb3ad517b830301da6b2ce929664af09ee9e4dd264f9`가 유지됐다. 의존성 패키지는 `dpkg-deb -x`만 사용했으며 시스템 패키지 DB·maintainer script·전역 PATH를 변경하지 않았다.

[비밀 없는 증적](evidence/claude-native-sandbox-2026-09-16.json)에 패키지·실행·최소 재현과 운영 대조를 기록했다. 비공개 원자료는 서버 `.ai-company/claude-sandbox-20260916/`에 있다. 기존 PM·개발·번역 큐의 상태별 개수와 PR #10 `pending`은 동일하다. [작업실 공개 적용안](translation-repair-2026-09-16.md)의 `c2b3fec` 이미지·worker와 별개이며, 해당 승인 요청은 계속 대기 중이다.
