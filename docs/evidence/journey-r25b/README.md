# R25-1b 증적

제품·검사 `ffb62c37e4822530f38f15f4b0f64781cdf40bc6`. [후속 검증](../../product-experience/journey-r25b-validation-2026-09-25.md)을 먼저 읽는다.

- `candidate.json`: 동일 제품·CI head·workflow·artifact·이미지·HTML/ZIP 해시 연결.
- `original-node-failure.json`: 지정 검수의 실제 고정 함수·factory·v8 처리기를 이용한 VM 재현. 실제 Chrome과 구분.
- `initial-setup-failure.json`: hash 이동만 하여 파일 요청이 없던 첫 시험 준비 실패.
- `original-browser-failure.json`, `screens/original-browser-failure.png`: 준비 수정 후 실제 인증·API·혼합 bytes·active/waiting에서 원제품 메서드 오류 재현.
- `journey-mixed-validation.json`: 수정 후 네 조합의 인증·파일 bytes·제어 상태·주소·미전송 초안·업무쓰기0·명시적 회복 기록.
- `journey-mixed-database.json`: 실제 임시 SQLite 업무29테이블 불변. 시험 인증은 별도 테이블이며 제외.
- `frozen-expiry-expectation-failure.json`: 날짜 경과로 드러난 고정 예시 검사 기대 문제. 원문/기한/405 쓰기 차단을 유지한 정정 근거.
- `journey-status-validation.json`, `journey-service-worker-validation.json` 및 기타 최종 JSON: 기존 상태·읽기·쓰기·공개 shell 갱신/복구·G1~G3 회귀.
- `module-contracts.json`: 기준과 후보의 import/export 대조. app에서 쓰는 factory의 반환 메서드는 별도 코드·함수 회귀로 검사.
- `image-validation.json`, `image-source-manifest.json`, `image-check.py`: 임시 격리 이미지의113파일과 실제 HTTP·no-store·인증 경계.
- `independent-review.md`: 코드 작성과 분리한 초기 재현·회귀 작성·코드/최종 Chrome JSON·PNG 검수. 검사 준비 실패와 최종 성공을 구별.
- `public-package-review.md`: 공개 직전 독립 패키지·출처·해시·민감정보·검증 범위 대조.
- `screens/`: 최초 실제 실패와 최종 Light320·Black390 갱신 안내, 기존 회귀 화면.

`sha256sum -c SHA256SUMS`로 이 디렉터리의 증적을 대조한다. 제품·검사 데이터는 공개 합성 자료다. 운영 DB/개인 대화/승인 원문/토큰/서명키/내부 접속 정보를 포함하지 않는다. 테스트 스크립트의 loopback·임시 경로는 운영 주소가 아니다. 실제 Chrome 자동 조작과 PNG 검수는 실기기 APK나 실제 모델 실행을 뜻하지 않는다.
