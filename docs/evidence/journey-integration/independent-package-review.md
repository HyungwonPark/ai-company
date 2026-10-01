# 순서형 공개 산출물 독립 검수

판정: 현재 공개 파일 범위에서 게시를 차단할 비밀정보·운영 원문 혼입 또는 패키지 불일치를 찾지 못했다. 제품 코드·운영 환경은 변경하거나 접근하지 않았다. 이 검수는 공개 산출물의 내용과 출처 대조이며 배포/실기기/실제 모델 검증 승인이 아니다.

## 패키지 원본 결속

- ZIP: `docs/previews/journey/AI-Company-journey-preview.zip`
- ZIP SHA-256: `10467c136710cdbc135bf0c465b0b7467798af97de186a30d41f2a608d238b44`
- ZIP 내부 HTML SHA-256: `f00fd8a37e30f9c90221e0b0b327b2c6e024ff2c544670f4f9dfcffdb98141ff`
- ZIP 내부 HTML과 manifest는 최종 6a Journey CI 검증 산출물과 바이트 단위 동일하다. ZIP의 네 항목은 HTML, manifest, README, SHA256SUMS이며 추가 데이터베이스·환경 파일·서명키가 없다.
- ZIP 내부 README는 공개 폴더 README와 동일하다. 내부 SHA256SUMS의 HTML·manifest·README 세 값도 실제 ZIP bytes와 일치한다. 외부 SHA256SUMS의 ZIP·manifest·README·fixture 값도 대조했다.
- 선별된 최초 PNG 11개와 Journey JSON 7개가 최종 검증 산출물과 바이트 단위 동일하다. 이 PNG 11개는 앞선 시각 검수에서 직접 읽었다. 이후 추가된 1px 드래그 PNG 두 장도 직접 열었고 공개 시험 그래프임을 확인했다. 드래그 브라우저 조작을 이 검수자가 재실행한 것은 아니다.

## 공개 정보 경계

- 공개 fixture의 외부 주소는 `github.com/fixture/graph/commit/`의 a/b 반복 시험 커밋 둘뿐이며 로그인 비밀·인증 헤더·쿠키·CSRF 실제 값은 없다.
- evidence의 데이터베이스 JSON은 임시 SQLite 업무 테이블 이름·불변 해시·검사 상태다. 운영 DB 레코드나 개인 대화 원문 덤프가 아니다. 쓰기 JSON에는 임시 API 경로/방법과 fixture 식별자가 기록되어 있고 요청 인증값/개인 메시지 본문은 없다.
- 공개 코드·시각 검수 보고서를 읽었다. 비공개 작업 경로가 자료 라벨로 치환됐으며 중간 실패·수정 및 최종 제한을 보존한다. 일반적인 `/tmp` 용어는 코드 검수 방법 설명에 남지만 실제 사용자 내부 경로는 없다.
- 문자열·JSON 키 및 ZIP 텍스트 검사에서 private-key 블록, GitHub/AWS credential 형식, 실제 내부 경로/접속 주소를 찾지 못했다. 발견된 `127.0.0.1`은 `image-check.py`의 임시 loopback 시험 코드이고 검사 계정 비밀번호는 실행 시 생성하는 난수이며 공개값이 아니다. `password_auth.py`의 64자리 값은 파일 해시다.
- `candidate.json`은 제품/검사/CI checkout을 구별하고 이미지 ID가 로컬 Docker 식별자임을 명시한다. 실제 모델 미호출·운영 미접근·실기기 미확인을 보존한다.

## README 판정

읽기 전용 GET fixture, 실제 쓰기/명세 저장/계획 확정/후보 결정 금지, 외부 링크·모델·서비스워커 제한, 실제 API 쓰기 검사의 별도 임시 환경, 만료 규칙 보존을 정확히 설명한다. PR head와 CI merge checkout 및 HTML 원본 결속을 구별한다. 브라우저에서 ZIP 해제 HTML을 열도록 안내하고 배포·병합·APK 직접 조작 완료를 주장하지 않는다.

아직 발급되지 않은 운영 승인이나 실제 마스터 조작을 새로 인정하는 문구는 확인하지 못했다. 일반적인 패턴 검색의 무검출을 모든 형태의 비밀정보 부재 보장으로 확대하지 않는다.
