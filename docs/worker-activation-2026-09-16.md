# 상시 worker 연결 · 2026-09-16

상위 서버 구축 목표와 앱에서 실행할 작은 검증 목표를 분리한다. [공개 웹 적용](live-use-readiness-2026-09-16.md#공개-적용복구)은 완료된 항목이다. 서명키 외부 보관은 별도 작업이며 worker 연결의 선행 조건이 아니다.

## 활성화 범위

사용자가 전달한 범위는 `ai-company-automation.service`와 `ai-company-translation.service` 두 user service다. 기존 타이머·커플 서비스·Caddy·PR #10 후보 수용·병합은 포함하지 않는다. 자동화의 기존 허용 파일·정책·Astra 최종 검수는 변경하지 않는다.

활성화 전 조건인 설치 복구 수정, 시작 시 처리할 대기 목록 대조, 승인 범위 밖 실행 방지를 확인한 뒤 두 서비스를 활성화했다. 실제 결과는 아래에 기록한다.

## 설치 복구 수정

기존 설치기는 계정 등록과 설치 영수증 작성 뒤 실패하면 파일만 지웠다. 남은 영수증은 재설치를 막고, 없어진 파일은 되돌리기를 막았다. 실제 서버 장애가 아니라 임시 환경에서 재현한 문제다.

수정한 설치기는 변경 전에 영속 설치 의도를 기록하고 설치·실패·중지·되돌리기 상태를 구분한다. 실패 시 서비스 중지를 확인하되 복구에 필요한 파일·기록을 남긴다. 같은 제안만 재개하고, 없는 파일을 다시 만들며, 기존 공유 한도·cooldown을 초기화하지 않는다. 되돌리기는 이미 없어진 파일도 허용하며 일치하는 소유 파일만 제거한다. 다른 내용으로 바뀐 파일은 재설치·되돌리기 모두 거부한다.

파일은 완성된 내용만 독점 게시하고, 같은 설치 제안의 동시 실행은 OS 잠금으로 막는다. SIGKILL 후에는 잠금이 해제돼 다음 실행이 남은 기록을 대조한다. 중지가 확인되지 않으면 파일 제거를 진행하지 않는다. 이전 형식의 실패 영수증도 복구할 수 있다.

[설치기](../scripts/install_control_plane_workers.py)와 [회귀 검사](../tests/test_worker_installation.py)의 7개 시나리오를 검증했다: 등록 이후 실패→재설치, 한 worker 시작 후 실패→누락 파일이 있는 되돌리기→재설치, 기존·변조 파일 거부, 중지 실패 재시도, 이전 형식 영수증, 실제 설치 프로세스 SIGKILL와 동시 설치 거부, 파일 게시 중간 실패. 테스트는 새 임시 파일·SQLite와 모의 systemd를 사용하며 운영 서비스를 변경하지 않는다.

## 시작할 기존 작업

운영 SQLite를 읽기 전용으로 복사한 뒤, 그 복사본에서 기존 실행기와 조정기를 돌렸다. 모델·Git 호출을 금지한 검사에서 두 실행 슬롯 모두 `IDLE`이었다.

| 대상 | 시작 전 상태 | 활성화 시 동작 |
|---|---|---|
| 기존 PM 요청 1개 | completed | 재실행 없음 |
| 기존 세션 작업 9개 | SESSION_COMPLETED | 재실행 없음 |
| 이전 실행 `8f6d0475a8f34818aff5991d720074d9` | blocked, 원격 checkout 증적 누락 | 과거 BLOCK 보존; 실행 가능한 예약 없음 |
| PR #10 실행 `42eb1c2d3f3d4b44903a83a6ecd1ccf4` | awaiting_approval | 후보 승인 pending 보존 |
| 번역 큐 | 기존 job 0개 | 기존 프로젝트의 승인·보고·검수·계획 등 읽기 문서 20개를 새로 등록 |

번역 문서 20개는 원본을 바꾸지 않는 번역 전용 작업이다. 원본 ID와 source digest별 목록은 서버 `.ai-company/live-use-20260916/activation-inventory.json`에 고정했다. 실제 시작 직전에 원본 테이블의 digest가 이 목록과 같은지 대조한다. 목록에 없는 대기 개발 작업은 이번 활성화로 임의 실행하지 않는다.

## 상위 구축 목표와 앱 검증 목표

| 상위 구축 항목 | 상태 | 다음 행동 |
|---|---|---|
| 최신 웹·짧은 제목·Light/Black 공개 적용 | 완료 | 기존 이미지·설정 복구안 유지 |
| 한국어 계획 상세·확정, 시스템 종합 보고 | 구현·검사 완료, 실제 번역 일부 실패 | 실패한 6개 문서의 보존 규칙·출력 형식 보완; 새 마스터 조작 확인 |
| PM·조정기·번역 상시 운영 | 서비스 2개 활성화·heartbeat·실제 재시작 확인 | 영속 큐 감시; 실제 호스트 재부팅은 미실시 |
| 앱 직접 확정 → 동일 실행 검수·보고 | 미검증 | 아래 작은 새 프로젝트를 마스터가 입력·확정 |
| Claude 자동 대체·이관 | 차단 | 실제 적용 effort 증거와 엄격한 정책 적격성 필요 |
| Ultracode·동적 workflow | 미검증 | 요청 옵션·실제 적용·동작을 각각 증명 |
| TWA 실기기 로그인·뒤로가기·테마·승인 | 미검증 | APK에서 사용자 조작 결과 확인 |
| 서명키 외부 보관·외부 복구 | 별도 대기 | PC 비공개 폴더와 비밀번호 관리자에 분리 인수 |

큰 서버 구축 목표를 앱의 PM 목표로 넣지 않는다. 현재 자동화 설정은 `pilot_status.py`와 그 테스트만 허용한다. 과거 목표·계획·확정·PR #9 BLOCK·PR #10 pending은 그대로 둔다.

마스터는 APK의 **프로젝트**에서 새 프로젝트를 만들고 [작은 한국어 검증 목표](live-use-readiness-2026-09-16.md#apk에서-마스터가-수행할-단계)를 입력한다. 실제 PM 제안이 나오면 두 역할의 독립 범위와 완료 조건을 확인하고 **계획 확정**을 직접 누른다. 서버는 이를 대행하지 않는다. 새 revision·plan digest·확정 이벤트·run ID를 기준으로 병렬 실행, 같은 후보 커밋의 검사·CI·독립 검수·Astra 최종 검수·보고·승인 요청을 대조한다.

## 서명키 인수는 병행

암호화본은 서버의 `~/.local/share/ai-company/android-offsite-preparation/20260915T234537Z-ec4b9fa3/ai-company-signing.tar.gpg`다. 같은 위치의 `recovery-code`를 공개 채널이나 GitHub로 보내지 않는다.

마스터의 기존 SSH/SFTP 접속으로 암호화 파일만 PC의 비공개 백업 폴더에 내려받는다. 복구 코드는 별도로 비밀번호 관리자에 보관하고 다운로드 폴더에 함께 두지 않는다. [암호화본 SHA-256과 외부 복구 확인 절차](live-use-readiness-2026-09-16.md#서명키인수인계)에 따라 별도 기기에서 검증한다. 서버에서 한 로컬 복구 검사로 외부 보관을 완료 처리하지 않는다.

## 실제 활성화 결과

설치기 기준 커밋 `deaeefd`의 [Python CI](https://github.com/HyungwonPark/ai-company/actions/runs/35040278462), [UI](https://github.com/HyungwonPark/ai-company/actions/runs/35040278228), [Android APK](https://github.com/HyungwonPark/ai-company/actions/runs/35040278364), [공개 연결](https://github.com/HyungwonPark/ai-company/actions/runs/35040278211)이 통과했다. 로컬 전체 회귀는 **305개 PASS**, 새 설치 복구 검사는 7개다. 실행 중인 worker 패키지와 공개 이미지는 기존 `51b911a`로 고정하며, 이번 수정은 호스트 설치기에 적용했다.

| 확인 | 실제 결과 |
|---|---|
| 자동화 서비스 | enabled / active / running, PID 1673116 |
| 번역 서비스 | enabled / active / running, PID 1670358 |
| 비정상 재시작 | 두 서비스 NRestarts=0 |
| heartbeat | 두 서비스의 반복 pass와 최신 heartbeat 확인 |
| 중복 실행 | 실제 가동 중인 두 component의 추가 소유권 획득이 모두 거부됨 |
| 실제 재시작 | idle 자동화 worker를 graceful restart: PID 1670357 → 1673116, 새 pass 완료 |
| 다른 역할 독립 실행 | 위 재시작 동안 번역 PID는 그대로이며 heartbeat가 계속 갱신됨 |
| 기존 개발 재개 | 새 모델/Git 실행 없음. 과거 요청·실행·세션 기록과 원래 14개 논리 테이블 digest 동일 |
| 기존 운영 보존 | 커플 앱·Caddy·DB·백업 컨테이너, 기존 quota timer, 공개 console 이미지 동일 |
| 승인 | PR #10 pending 유지; 새 목표·계획 확정을 대행하지 않음 |

실제 번역 20개 중 **14개 completed / 6개 failed**다. 실패한 문서는 원문을 그대로 제공하고 승인된 번역으로 표시하지 않는다. worker는 실패한 문서에 멈추지 않고 나머지 작업을 처리했으며 임의 재시도를 하지 않았다. 관측 모델은 `claude-haiku-4-5-20251001`이며 요청 effort와 실제 미관측 effort는 계속 구분한다.

실패 내역은 보고 2개·검수 2개·프로젝트 목표 1개·과거 계획 1개다. 일부는 실제 식별자/조건 누락이나 JSON 외 설명 출력이고, 목표·계획은 경로 뒤 문장 마침표까지 보호 문자로 인식해 거부된 사례다. 모두 성공했다고 보고하지 않으며, 보존 규칙을 무조건 완화하거나 원본/실패 결과를 덮어쓰지 않았다. 별도 후속 수정과 원본에 연결된 재검증이 필요하다. 새 검증 목표는 한국어로 입력하고 PM도 한국어로 제안하도록 구성돼 있다.

호스트를 실제 재부팅하지 않았다. 재부팅 후 시작은 enabled user units와 기존 linger=yes로 준비했고, 앞선 테스트에서 이전 boot 식별자의 실행을 재사용하지 않는 복구를 확인했다. 실제 APK 새 목표 입력·계획 확정·그 실행의 끝까지 대조는 마스터 조작을 기다린다.

원본 증거는 서버 `.ai-company/live-use-20260916/`의 `activation-inventory.json`, `activation-preview-result.json`, `worker-before.json`, `worker-actual.json`, `worker-preservation.json`, `actual-worker-restart.json`이다. [비밀 없는 활성화 요약](evidence/worker-activation-2026-09-16.json)에 관련 결과를 묶었다.
