# 한국어 표시 복구 · 2026-09-16

**구현·검증 완료, 운영 미적용.** 최종 코드 `c2b3fec4201912432f1f0518e3abb45303af7999`는 [PM과 팀을 만드는 작업실](human-workspace-ux-2026-09-16.md)과 번역 복구를 함께 포함한다. 운영 번역은 현재 완료 14건·실패 6건이며, 아래 사본 검증 결과를 실제 운영 결과로 표시하지 않는다.

## 발견한 문제

실패 6건의 기존 CLI 출력과 원문을 대조했다. 프로젝트 목표와 계획 번역 2건은 `src/ai_company/pilot_status.py.`의 마지막 마침표를 파일 경로로 잘못 판단해 거부됐다. 번역에는 실제 경로가 보존돼 있었다. 다른 4건은 상태 식별자·경로 일부·부정 또는 조건 표시 누락, JSON 형식 위반이 있어 계속 거부한다.

새 파서는 문장 끝 마침표를 분리하고 상대 경로 전체를 보호한다. 다른 접두 경로·확장자·중복 경로는 거부하며 백틱 안의 코드는 마침표까지 그대로 검사한다. 조건·예외·숫자·승인 상태 검사를 완화하지 않는다.

## 원기록을 유지하는 복구

- 기존 실패와 원문은 수정하지 않는다. 원래 작업 digest와 source digest에 연결된 새 번역 산출물을 만든다.
- 2건은 저장된 native stdout만 재해석한다. 초기화·성공 결과가 각각 하나인지, 실제 모델·빈 도구 목록·세션·종료 확인이 연결되는지 검사한다. 출처에는 원래 실패 상태·이유·세션·stdout SHA-256과 추가 호출 0회를 기록한다.
- 나머지 4건은 원래 허용한 두 번 중 남은 한 번만 등록할 수 있다. 소비한 횟수·시간·공유 계정 한도를 그대로 이어받는다. 새 작업 ID를 이용해 예산을 초기화하지 않는다. 최대 추가 모델 호출은 합계 4회이며 실제 추가 호출은 아직 0회다.
- 일반 동기화와 재시작은 같은 원문·설정에 연결된 복구 산출물을 유지한다. 다른 원문이나 새 설정의 선택을 덮어쓰지 않는다. 동시 요청·응답 유실 후 재등록도 산출물 한 건이다.
- 기존 기본 번역 설정과 완료 14건은 변경하지 않는다. 버전 `translation-json-v3`·`ko-translation-v2`는 명시한 복구 작업에만 적용한다. 앞으로 생기는 모든 번역이 자동으로 새 정책을 쓰는 것은 아니다. 새 PM은 사용자에게 표시하는 문장을 한국어로 작성하도록 요청한다.

복구 도구 [repair_recorded_translations.py](../scripts/repair_recorded_translations.py)는 기본 실행이 읽기 전용 미리보기다. 명시한 실패 ID·원문 digest·원시 출력 SHA-256·재해석 결과 digest·최대 추가 호출 수를 0600 파일로 고정한다. 적용 시 제안 파일 SHA-256, 실제 번역 worker PID·작업 디렉터리·DB 경로·설치 코드의 일치를 다시 검사한다. 기존 `51b911a` worker는 이 검사를 통과하지 못한다. 공개 HTTP나 모델에는 복구 등록 권한을 제공하지 않는다.

## 검증

| 근거 | 결과 |
|---|---|
| [최종 Python CI](https://github.com/HyungwonPark/ai-company/actions/runs/35046468421) | 3.11·3.12 각각 321개 PASS, Android 준비 각각 4개 PASS |
| 신규 복구 회귀 | 10개 PASS. 저장 출력 재해석, 경로·조건 보존, 남은 예산·공유 한도, 동시 등록·재시작, 선택 충돌 시 트랜잭션 취소, 오래된 적용안·다른 worker·도구/세션 변조 거부 |
| [최종 브라우저 CI](https://github.com/HyungwonPark/ai-company/actions/runs/35046468446) | 한국어 계획·확정, 원문과 권한 유지, Light/Black·모바일·PM 대기·중복 실행 방지 PASS. 모의 API와 기존 저장 번역 fixture를 사용 |
| 실제 운영 DB의 비공개 사본 | 완료 16건·대기 4건. 원래 번역 20건, 보호 테이블 23개와 기존 이벤트 모두 보존. 번역 연결 이벤트 6개만 추가 |
| 최종 이미지 | 공개 포트·네트워크·운영 DB 없이 파일 27개·로그인 방식·익명 API/잘못된 Host 거부 PASS |
| 최종 worker 설치본 | 고정 원본 Python 30개와 동일. 임시 프로젝트의 PM 요청 1건·개발 실행 0건. 실제 모델 호출 없음 |

[사본 검증 영수증](evidence/translation-repair-2026-09-16.json)은 운영 적용과 분리돼 있다. 자동 검사 통과는 번역 의미의 독립 검수 완료를 뜻하지 않으며 `semantic_validation=not_independently_verified`를 유지한다. 이번 UX/복구 변경을 별도 모델이 독립 검수하거나 Astra가 최종 검수한 것은 아니다.

## 적용안

이번 요청의 배포·병합 제외 경계 때문에 **아래 교체는 아직 실행하지 않았다.** 이전 두 서비스 활성화와 이번 코드 교체는 구분한다. 적용 후에도 실제 마스터의 목표 입력과 계획 확정은 마스터가 APK에서 직접 한다.

| 대상 | 현재 → 후보 |
|---|---|
| console image | `sha256:0b335d24f67c3a727a366b63879165ea2b8ef54323cbb5ca267b4d050cb3aaed` → `sha256:2f404f44b326c9644af1feb33f2e1fd68559554ff122e132dccc32ad84281c13` |
| worker release | `/home/edward/ai-company/releases/control-plane-51b911a` → `/home/edward/ai-company/releases/control-plane-c2b3fec` |
| 기존 Compose SHA-256 | `872f8beb446ebdd78df223ea8d9aae03f0b2149ddef00b5ae1931036dc41614b` |
| 번역 복구 제안 SHA-256 | `7b0405ba361ae583b1edca634992310a79b377c7f3b3593bc7a466dd4a601a80` |

1. 기존 Compose·release 링크·계정·PR #10 pending·타이머·커플 서비스·Caddy·assetlinks와 PM/실행/번역 대기 목록을 다시 대조한다. 검토 이후 다른 변경이나 허용 범위 밖 대기 작업이 발견되면 덮어쓰거나 실행하지 않는다. 현재 처리 가능한 기존 PM/개발 대기는 없고, 과거 blocked 실행과 승인 대기 실행은 보존한다.
2. 두 worker를 정상 정지하고 자식 실행 종료를 확인한다. 기존 unit·env·실행 설정·설치 영수증은 그대로 둔다. 종료가 불확실하면 코드 링크를 바꾸지 않는다.
3. 원본 Compose와 기존 링크를 보관하고 `services.console.image` 한 필드, `control-plane` 링크만 원자적으로 교체한다. `docker compose -f <기존 console.compose.json> up -d --no-build --no-deps console`로 console만 재생성한다. Host·Origin·CSRF·쿠키 설정은 유지한다.
4. 공개 HTTPS의 파일 27개·비밀번호 로그인 방식·비인증 API 거부와 기존 서비스 상태를 대조한다. 두 worker를 같은 설정으로 시작하고 실제 PID·release·heartbeat·중복 실행 방지를 확인한다.
5. 위 적용 검증 성공 후에만 고정한 번역 복구 6건을 등록한다. CLI 실행에는 `--apply --proposal <repair-proposal-c2b3fec.json> --expected-proposal-sha256 7b0405ba361ae583b1edca634992310a79b377c7f3b3593bc7a466dd4a601a80 --candidate-release /home/edward/ai-company/releases/control-plane-c2b3fec`가 필요하다. 등록 전 다시 원기록을 대조하며 일반 번역 worker가 남은 예산 내 작업을 처리한다.
6. 복구된 계획의 한국어 표시와 원문·계획 digest, 원래 승인 상태를 대조한다. 마스터에게 새 프로젝트 → 목표 → PM 대화 → 팀 검토 → 계획 확정을 안내한다. 이전 위임·검증 클라이언트 자동 확정은 사용하지 않는다.

번역 등록 전 적용 검증이 실패하면 worker 종료를 확인하고 기존 `51b911a` 링크와 `0b335d…` 이미지를 복원한 후 두 서비스를 재개한다. DB를 과거 백업으로 덮어쓰지 않는다. 등록 후 뒤늦게 코드 복구가 필요하면 v3 번역이 실행/대기 중인지 먼저 확인한다. 실행/대기가 남아 있는 상태에서 구버전 번역 worker를 시작하지 않는다. 종료·보류 상태를 확정할 수 없으면 해당 worker는 중지 상태로 두고 기록을 대조하며, 웹은 기존 이미지로 복구할 수 있다.

제안·원본 Compose·고정 원본 tar·비공개 DB 사본·로그는 서버 `.ai-company/translation-repair-20260916/`에 보관한다. 비공개 DB 사본과 native 로그를 저장소에 올리지 않는다. Caddy·도메인·커플 서비스·계정 권한·기존 타이머·실행 허용 파일·PR #10 승인·병합·APK/서명키는 변경하지 않는다.

후속 [Claude 적용 설정·동적 Workflow 프로브](claude-runtime-settings-2026-09-16.md)는 별도로 검증했다. 운영 Claude 개발 대체, 마스터 APK 실행 완주와 서명키 외부 보관·복구 확인은 계속 미완료다. 이 번역 복구를 해당 항목들의 완료 증거로 사용하지 않는다.
