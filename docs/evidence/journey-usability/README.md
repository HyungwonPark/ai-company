# 순서형 사용성 증적

[검증 기록](../../product-experience/journey-usability-validation-2026-09-25.md) · [적용/복구](../../product-experience/journey-usability-rollout-2026-09-25.md).

- `candidate.json`: 같은 최종 제품·CI·artifact·이미지·ZIP 연결.
- `journey-usability-validation.json`: 독립 작성 자동 조작, 실제 임시 인증 API 흐름8개, Light/Black×320/390/1440 전후36비교·54PNG, 만료 전후·본문 이동·확대.
- `journey-usability-database.json`: 임시 업무29테이블 불변. 인증 테이블 제외를 명시.
- `journey-status-validation.json`: 기존 R25 상태48조합. `journey-mixed-validation.json`: 기존 인증 혼합모듈4조합. 나머지 journey JSON: 읽기/쓰기/그래프/응답경합/미리보기/SW 갱신·복구 관련 회귀.
- `screens/`: 고정 fixture의 변경 전후 화면과 실제 임시 API 조작. 파일명의 before/after·테마·폭·화면을 맞춰 비교한다. 전체 페이지 캡처의 고정 탐색은 촬영 시 뷰포트 위치에 표시되므로 이를 실제 스크롤 전부의 가림으로 계산하지 않는다.
- `b52beac-status-check-failure.json`, `65b0f5a-status-check-failure.json`: 옛 검사 selector 중복과 이미 열린 상세를 다시 닫은 검사 실패. 제품 상태 분류 실패로 바꾸어 보고하지 않는다.
- `usability-local-initial.log`, `compact-blocker-first-failure.log`: 미확인 상태의 진행색과 접힌 집계 차단 근거 누락의 실제 함수 실패. 수정 후 관련 독립 회귀 성공과 대조한다.
- `image-validation.json`, `image-source-manifest.json`, `image-check.py`: 새 이미지113파일 및 읽기 전용/network none HTTP 확인.
- `css-contrast.json`: 선언색의 대비 계산. 실제 캡처 검수와 구분하며 접근성 인증을 뜻하지 않는다.
- `independent-review.md`: 코드 작성과 분리한 실제 코드·자동 조작 증적·PNG·후보 해시 검수.

`sha256sum -c SHA256SUMS`로 전체 증적을 확인한다. 실행·대화·승인은 합성 시험 자료다. 운영 DB/개인 대화/승인 원문/토큰/서명키를 포함하지 않는다. loopback은 임시 시험 서버 경로다. 만료 검사의 시계 이동은 브라우저 안에서만 수행했고 원문 fixture의 기한을 연장하지 않았다.
