# Claude 운영 연결 준비 · 2026-09-16

**최신 코드의 웹 이미지·worker 설치본·Claude 후보 설정·복구 기준을 별도 패키지로 준비했다. 운영에는 적용하지 않았다.** 기존 `c2b3fec` 공개 작업실·번역 복구 승인안을 교체하지 않는다. [준비 증거](evidence/claude-activation-2026-09-16.json)

## 고정한 패키지

| 항목 | 값 |
|---|---|
| 실행 코드 | `559474405885fda7995fb97075cf647f7cae8921` |
| 웹 이미지 | `sha256:399e353b856c71b234c2bd984d9092cfddb789dd1ea6077c4ae84c38da9cc55e` |
| worker 설치본 | `/home/edward/ai-company/releases/control-plane-5594744` |
| 원본 소스 tar SHA-256 | `3544e6bb38f6ce0e9e2ea84ab7c8d87dc41dca143609deae766248ddc2b9dad7` |
| 새 설정 digest | `2f925f653446f89b1b0d717f9a42e0d99a9df4660c90283a4cd2ba66a6d66415` |
| 적용 제안 SHA-256 | `1ddcb29d35e613e233fa5bb9d68d0b2c4d303dae007467f4383cab67ea919c7d` |

웹 27개 파일은 `c2b3fec`의 [작업실 화면](human-workspace-ux-2026-09-16.md#화면-예시)과 같다. 새 패키지는 후속 Claude 실행 연결·실패 비용 보존·검수 근거·차단 보고의 Python 변경을 포함한다. 설치본의 Python 파일 35개가 고정 소스와 같고, 편집 모드로 설치하지 않았다. 소스·설치본·웹·vendor 등 129개 파일의 SHA-256 목록을 보관한다.

검증한 `socat` 실행 파일과 저작권 고지는 이 설치본의 `vendor/`에 복사했다. 바이너리 SHA-256은 `8bce608454b1f027e42ad6d9e49935dfad44605da7f7f138b06a0f36b38d6025`이며 동적 라이브러리 누락이 없다. 전역 패키지·PATH·권한·AppArmor 프로필을 바꾸지 않았다.

## 적용 시 달라지는 것

이 목록은 검토할 적용안이며 실행 허가나 적용 기록이 아니다.

1. 기존 console Compose의 `services.console.image` 한 필드를 위 이미지로 바꾼다.
2. 두 worker가 사용하는 `control-plane` 링크를 위 설치본으로 바꾼다.
3. `runtime/control-plane/automation.json`에서 새 증거 정책 `cli_configuration_v2`를 선택하고, 기존 개발 후보 뒤에 `pilot-claude-dev` 한 명을 추가한다. Claude는 두 pilot 파일의 **개발 대체자**로만 등록한다. PM·독립 검수·Astra 최종 검수 후보, 허용 파일·검사·원격 CI·예산·최대 병렬 수는 유지한다. 현재 등록된 Claude 자격 증명·공유 한도 그룹을 사용한다.
4. 기존 `worker.env`에 `AI_COMPANY_CLAUDE_SOCAT=/home/edward/ai-company/releases/control-plane-5594744/vendor/bin/socat` 한 변수를 추가한다. 기존 값과 `translation.json`, 두 service unit은 유지한다. 두 worker의 정상 정지·재시작이 필요하다.

기존 계획과 실행은 이전 설정 digest를 유지한다. 새 정책에 맞춰 원문·digest·확정 기록을 다시 쓰지 않는다. 새 후보를 쓰는 실제 검증은 적용 후 마스터가 새 목표를 입력하고 새 계획을 확정한 실행이어야 한다. 이전 위임은 사용하지 않는다. 첫 실행의 허용 범위는 두 pilot 파일과 기존 CI 메타데이터다.

## 확인한 범위

- 격리 이미지: 공개 포트·운영 DB 마운트 없이 네트워크 없음, 읽기 전용 root, capabilities 제거, `no-new-privileges`로 실행했다. 27개 웹 파일·비밀번호 로그인 모드·익명 API 거부·잘못된 Host 거부를 확인했다.
- 설치본: 별도 임시 systemd 검사 프로세스에서 worker와 같은 `NoNewPrivileges=yes`를 적용했다. 설치된 파일 도구로 담당 파일 읽기·쓰기와 비담당 쓰기 거부를 확인했다. 실제 Claude 바이너리·socat·bwrap·프로필의 고정 해시와 두 userns 제한값 `1`을 대조했다. 임시 프로세스는 종료됐고 unit은 남지 않았다.
- 운영 DB 사본: 기존 27개 테이블의 행을 그대로 보존했다. PM 완료 1건, 실행 차단 1건·승인 대기 1건, 세션 완료 9건, 번역 완료 14건·실패 6건이다. 현재 새로 처리할 PM·개발·번역 대기는 없다. 실패 6건은 자동 재시도 대상으로 바꾸지 않았다. 기존 실행과 새 설정의 digest가 다름을 확인했다.
- 기존 flow 후보의 `MERGE_READY` 1건은 PR #10의 승인 대기 실행에 연결돼 있다. 저장된 후보·예약 값은 보존하며 이를 새 개발 실행이나 승인으로 취급하지 않는다.
- 새 빈 시험 DB: 프로젝트·PM 요청 1건이 저장되고 개발 실행은 0건이다. 마스터 확정이나 모델 호출은 수행하지 않았다.
- 원격: 고정 코드의 [Python CI](https://github.com/HyungwonPark/ai-company/actions/runs/35056932181)는 Python 3.11·3.12 각각 362개 중 359개 PASS·호스트 전용 3개 skip, Android 준비 4개 PASS다. [UI](https://github.com/HyungwonPark/ai-company/actions/runs/35056932124)·[APK](https://github.com/HyungwonPark/ai-company/actions/runs/35056932162)도 PASS다. Android runtime·Automation evidence의 skip은 실기기나 전체 자동 실행 성공이 아니다.

이 패키지 검사에서는 모델을 호출하거나 전체 coordinator tick을 실행하지 않았다. [이전 실제 개발 이관·세션 재개](claude-dispatcher-2026-09-16.md), [새 읽기 전용 검수](claude-review-2026-09-16.md), 이번 설치본 검사를 서로 구분한다. 실제 APK 입력부터 같은 실행의 원격 CI·독립 검수·Astra 최종 검수까지는 여전히 미완료다.

## 적용 전 대조와 복구

서버의 `.ai-company/claude-activation-20260916/`에 원본/제안 Compose, 설정·환경 파일, 해시 목록, 검사 로그와 읽기 전용 `preflight.py`를 보관한다. 인증 정보를 포함할 수 있는 원본 파일은 비공개이며 저장소에 올리지 않는다. 아래 명령에는 적용 기능이 없다.

```bash
cd /home/edward/ai-company/workspaces/auto-pm-flow
python3 .ai-company/claude-activation-20260916/preflight.py \
  --proposal-sha256 1ddcb29d35e613e233fa5bb9d68d0b2c4d303dae007467f4383cab67ea919c7d
```

현재 원본은 worker `51b911a`, 웹 `sha256:0b335d24f67c3a727a366b63879165ea2b8ef54323cbb5ca267b4d050cb3aaed`다. 사전 검사는 원본 링크·실행 중 이미지·Compose·설정·unit·운영 DB의 대조를 요구한다. **먼저 `c2b3fec`가 적용되거나 새 작업이 들어오면 이 기준은 낡은 것이므로 적용을 멈추고 새 상태로 검토해야 한다.** 이전 승인이나 경과 시간을 이번 패키지의 승인으로 해석하지 않는다.

실제 적용 허가를 받은 뒤에는 새 요청 유입을 포함해 대기를 다시 점검하고, 두 worker와 자식 프로세스의 종료를 확인한 다음 원본 해시가 일치하는 파일만 바꾼다. 동일 계정·상태 경로로 재시작하고 HTTPS 로그인·Origin/CSRF·보고·승인·heartbeat와 설정 digest를 확인한다. 이 준비 검사만으로 공개 적용 검사를 대체하지 않는다.

실패하면 새 실행을 중단하고 두 worker와 자식의 종료를 확인한다. 이번 제안과 일치하는 설정·환경·링크·이미지만 기록된 원본으로 복원한다. 기존 설치기 되돌리기를 사용할 때도 먼저 원본 설정·환경·링크를 복원해야 소유권 검사가 맞는다. DB를 과거 사본으로 복구하거나 실행·사용량·승인 이력을 지우지 않는다. 새 설정으로 시작된 작업이 있으면 해당 설정에 묶인 영속 대기를 유지하고 재개 방안을 별도로 판단한다.

현재 커플 앱·Caddy·DB·백업·기존 타이머·두 worker·PR #10 `pending`은 유지됐다. 이 패키지는 번역 복구 등록, 도메인 변경, APK 교체, 서명키 이동, 병합을 수행하지 않았다. 서명키의 PC 외부 보관·외부 복원과 실제 호스트 재부팅 관측도 남아 있다.
