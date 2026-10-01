# R25 동일 후보 검증 증적

제품·검사 `14114a71584c65c6dbbd0b33a1a26c7246d1e1ad`. [검증 기록](../../product-experience/journey-r25-validation-2026-09-24.md)을 먼저 읽는다.

- `candidate.json`: 제품·검사·CI·artifact·이미지·미리보기 해시 연결. workflow run의 head와 artifact의 head 일치 확인.
- `initial-node-*`, `initial-browser-*`, `screens/initial-*`: 수정 전 최초 실패. Node 재현과 실제 Chrome 실패를 구분.
- `current-plan-original-failure.txt`, `selector-original-failure.txt`: 독립 검수에서 찾은 같은 원인의 추가 변형. 공개 합성 입력이며 임시 로컬 경로만 비식별화.
- `intermediate-input-expectation-failure.json`: SW 부팅 성공 뒤 기존 로그인 암호의 offline 초기화로 실패한 중간 검사. 업무 초안과 구분해 최종 검사를 분리한 근거.
- `journey-status-validation.json`: 임시 SQLite·실제 API·격리 Chrome의 48상태 조합과 선택기 문구.
- `journey-service-worker-validation.json`: 2개 기존 탭과 waiting worker의 부분/전체 갱신·offline·복구, 실제 app/report SHA.
- 나머지 `journey-*-validation.json`: C 읽기, D 쓰기, G1~G3, preview의 최종 검사. 실제 쓰기는 임시 상태에서만 진행.
- `journey_*-database.json`: 인증 시험 기록과 구분한 29개 업무 테이블 불변. 운영 DB의 hash나 내용이 아님.
- `graph-baseline-comparison.json`: 같은 합성 자료의 기준/후보 경로 비교.
- `image-validation.json`, `image-source-manifest.json`, `image-check.py`: 로컬 arm64 검수 이미지113파일·HTTP·권한 확인. 운영 mount·worker·모델 호출 없음.
- `independent-review.md`: 제품 작성자와 분리된 재현·코드·화면 검수. 역사적 중간 판정과 최종 판정을 함께 보존.
- `screens/`: 같은 최종 CI의 직접 렌더링 캡처와 최초 실패. Korean Light/Black 320/390, 대표1440, current_plan과 SW 포함.

`SHA256SUMS`는 이 디렉터리 기준으로 `sha256sum -c SHA256SUMS`로 검사한다. 파일 수정 시 기존 증적을 새 성공으로 바꾸지 말고 출처와 후보를 대조한다.

공개 합성 예시, 비운영 검사 결과 및 원본 코드 해시만 포함한다. 실제 DB·계정 비밀·개인 대화·승인 원문·내부 운영 경로는 포함하지 않는다. 자동화된 격리 Chrome 결과는 실기기 APK, 실제 모델, 사용자 확정, 운영 적용 결과를 뜻하지 않는다.
