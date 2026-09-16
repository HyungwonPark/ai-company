# PM과 팀을 만드는 작업실 · 2026-09-16

프로젝트 이름과 자연어 목표로 PM 대화를 시작한다. 마스터가 함수 계약·파일 경로·역할별 하네스를 먼저 작성해야 했던 검증 절차를 기본 UX에서 제거한다. 실행 범위와 승인 검사는 그대로 유지한다.

## 화면

- **프로젝트**: 이름과 목표 두 필드. `PM과 시작`은 프로젝트와 첫 PM 요청을 같은 트랜잭션에 저장한다. 역할·개발 실행·계획 확정은 생성하지 않는다.
- **매니저**: 목표 → 팀 → 작업의 세 단계. 데스크톱은 대화와 팀을 나란히, 휴대폰은 `대화 / 팀`으로 전환한다. B형 Light·Black과 짧은 한국어 제목을 유지한다.
- **대화**: 최근 대화 두 개를 앞에 표시한다. 긴 전문과 과거 대화는 펼쳐 읽는다. `역할 추천`, `역할 추가`, `더 단순하게`, 역할별 `조정`은 입력 초안을 채울 뿐 자동 전송·확정하지 않는다. 기존 초안은 덮어쓰지 않는다.
- **팀**: 역할·책임·상태·요청 모델과 관측 모델을 구분한다. 새 요청을 보낸 동안 이전 팀은 조정 중으로 남기고 이전 계획의 확정은 허용하지 않는다.
- **확정**: 역할·완료 조건을 먼저 읽는다. 허용 경로·역할 ID·원본 digest는 접힌 상세에서 확인한다. 읽기 언어를 바꾸면 검토 체크를 다시 해야 하며 실행에 전달하는 원문·digest는 바뀌지 않는다.
- **설정**: 하네스 직접 편집은 프로젝트의 `고급 설정`에 유지한다. 읽기·대화와 보고·승인 탐색은 분리한다.

## 실제 연결

후속 PM 요청은 같은 프로젝트의 최근 대화와 이전 제안을 스냅샷으로 보관한다. 최신 메시지와 함께 실제 Dispatcher PM 작업으로 전달한다. 과거 대화는 논의 맥락이며 실행 허가로 해석하지 않는다. 이전 요청·실행 명세는 다시 작성하지 않는다.

맥락은 최근 8개 메시지, 메시지당 4,000자·합계 16,000자로 제한한다. 이전 제안은 20,000자 이하일 때 원본 내용, 그 이상이면 생략 표식과 ID·digest를 전달한다. 원본 저장 기록은 줄이지 않는다. 이 범위 밖 과거 대화의 무제한 기억은 지원하지 않는다.

Dispatcher가 검증한 PM의 BLOCK 응답도 대화에 영속 기록한다. 예를 들어 범위나 추가 결정에 관한 질문을 마스터가 읽고 답할 수 있다. 이 응답은 계획·확정·승인을 만들지 않는다. 재시작이나 같은 결과의 재기록으로 대화를 중복 생성하지 않는다.

worker 상태에는 비밀을 제외한 요청 설정만 추가한다. 계정 식별자·자격 증명·clone 경로·관측되지 않은 적용 증거는 포함하지 않는다. 기존 worker가 이 정보를 제공하지 않으면 설정 확인 중으로 표시한다.

## 현재 한계

- 운영 자동화의 허용 범위는 여전히 `src/ai_company/pilot_status.py`와 `tests/test_pilot_status.py`다. 다른 프로젝트의 자유 실행 준비가 완료된 것은 아니다. 이 변경은 서버 권한·후보 정책·원격 CI 검증 범위를 넓히지 않는다.
- 실제 Claude Haiku 번역 연결은 확인했다. 후속 [Opus 5·xhigh·Ultracode와 작은 Workflow](claude-runtime-settings-2026-09-16.md)도 native 근거로 확인했다. 운영 증거 계약·쓰기 격리·이관 검증이 남아 있어 개발 자동 대체 가능으로 표시하지 않는다.
- 이 화면 회귀는 격리된 API·브라우저·모의 모델을 사용한다. 실제 마스터의 APK 입력·계획 확정과 그 실행의 최종 검수 완주 증거가 아니다.
- 기존 PR #10 후보 승인은 pending이며 배포·병합하지 않는다.

## 적용 경계

후보 웹 이미지와 worker 패키지는 검증 후 고정한다. 공개 적용은 console 이미지 교체와 두 worker의 코드 교체·순차 재시작을 요구한다. 현재 운영 버전 `51b911a`와 기존 설정·상태 경로를 복구 기준으로 보존한다. Caddy·도메인·커플 서비스·계정 한도·실행 허용 파일·타이머는 변경 대상이 아니다.

## 검증 결과

최종 실행 코드 **`c2b3fec4201912432f1f0518e3abb45303af7999`** 기준이다. 웹 파일은 캡처 당시 `1769720`과 같다. 후속 번역 복구 코드를 포함하며 이후 문서·캡처 커밋은 실행 코드와 구분한다.

| 검사 | 결과 |
|---|---|
| 로컬 Python 3.13 | 전체 회귀 321개 PASS, 마지막 출처 필드 보완 후 번역 검사 39개 PASS |
| [원격 Python 3.11·3.12](https://github.com/HyungwonPark/ai-company/actions/runs/35046468421) | 각각 최종 코드 321개·Android 준비 4개 PASS |
| [원격 Chrome UI](https://github.com/HyungwonPark/ai-company/actions/runs/35046468446) | Light·Black, 320/360px·데스크톱, 새 목표 → PM 요청, 역할 조정, 초안 유지, 한국어 계획, 승인 원문 유지, stale 거부, 응답 중단 후 실행 1건, 오프라인 PASS |
| [Android 빌드](https://github.com/HyungwonPark/ai-company/actions/runs/35046468385) | PASS. 신규 서명 APK 배포나 실기기 검증과 별개인 빌드 검사 |
| 격리 후보 이미지 | 27개 웹 파일 SHA-256, 비밀번호 로그인 모드, 익명 API·잘못된 Host 거부, 목표+PM 요청 원자적 저장 PASS |
| 고정 worker 패키지 | 저장소에 의존하지 않는 설치본에서 목표+PM 요청 1건, 실행 0건 PASS. 최종 웹 코드의 Python 파일과 바이트 단위 동일 |
| 기존 운영 | 두 worker PID·재시작 수 그대로, 기존 타이머 active, 커플 앱·Caddy·DB·백업 보존, PR #10 pending |

이미지 검사는 네트워크·공개 포트 없이 읽기 전용 루트, capabilities 제거, no-new-privileges 상태에서 했다. 운영 상태·인증 파일은 마운트하지 않았다. 새로운 실제 모델 호출·계획 확정·후보 승인은 수행하지 않았다. 이번 UX 변경의 별도 독립 모델 검수·Astra 최종 검수는 수행하지 않았으며, 기존 자동 실행의 검수 규칙을 유지한 것이다. Android runtime과 Automation evidence workflow의 이번 `skipped`는 PASS로 세지 않는다.

## 화면 예시

아래 이미지는 해당 커밋의 실제 Chrome 렌더링이다. 대화·팀은 **화면 회귀용 모의 데이터**이며 실제 마스터 입력이나 PM 수행 증거가 아니다.

| 화면 | Light | Black |
|---|---|---|
| 대화와 팀 | [작업실](images/human-workspace-2026-09-16/light-desktop-pm-workspace.png) | [작업실](images/human-workspace-2026-09-16/black-desktop-pm-workspace.png) |
| 휴대폰 목표 입력 | [프로젝트](images/human-workspace-2026-09-16/light-mobile-new-project.png) | [프로젝트](images/human-workspace-2026-09-16/black-mobile-new-project.png) |
| 휴대폰 역할 조정 | [팀](images/human-workspace-2026-09-16/light-mobile-manager-team.png) | [팀](images/human-workspace-2026-09-16/black-mobile-manager-team.png) |

## 고정한 적용안과 복구

**준비 완료·공개 미적용**이다. 현재 APK와 공개 웹은 기존 `51b911a`를 계속 사용한다. 이번 요청의 배포·병합 제외 조건을 유지한다.

- 웹 후보: `sha256:2f404f44b326c9644af1feb33f2e1fd68559554ff122e132dccc32ad84281c13`. 기존 Compose의 `services.console.image` 한 필드만 바꾸는 제안을 보관했다.
- worker 후보: `/home/edward/ai-company/releases/control-plane-c2b3fec`. 최종 코드의 Python 파일 30개와 비편집 설치본이 같고 의존성은 lock으로 설치했다. 현재 `control-plane` 링크는 여전히 `control-plane-51b911a`를 가리킨다.
- 적용 순서: 승인 후 대기 목록과 실행 중 프로세스를 다시 대조 → 두 worker를 정상 정지 → 새 링크와 웹 이미지를 적용·상태 확인 → 동일 설정으로 두 worker 재개. 작업·계정·번역 기록을 지우거나 계획을 대행 확정하지 않는다. unit/env/config/기존 설치 영수증은 덮어쓰지 않는다.
- 실패 복구: 두 worker의 종료를 확인한 뒤 링크를 `control-plane-51b911a`, 웹을 기존 `sha256:0b335d24f67c3a727a366b63879165ea2b8ef54323cbb5ca267b4d050cb3aaed`로 되돌린다. DB는 이전 사본으로 덮어쓰지 않는다. 원래 설치기의 되돌리기를 사용할 때도 먼저 원래 링크로 복원해야 소유권 검사가 맞는다.
- 제외: Caddy·도메인·커플 앱·실행 허용 경로·계정 한도·운영 타이머·PR #10 결정·병합·새 APK/서명키 변경.

최종 후보·제안 Compose·원본 Compose·이미지/패키지 검사·CI 로그는 서버 `.ai-company/translation-repair-20260916/`, 기존 캡처는 `.ai-company/human-ux-20260916/`에 보관했다. [준비 영수증](evidence/human-workspace-ux-2026-09-16.json)과 [번역 실패 복구·최종 적용 순서](translation-repair-2026-09-16.md)를 함께 제공한다.
