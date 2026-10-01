# AI Company 순서형 사용성 미리보기

ZIP을 풀고 `AI-Company-journey.html`을 Chrome에서 연다. GitHub/운영 로그인이 필요 없다. 파일 앱의 간이 미리보기에서는 JavaScript가 제한될 수 있다.

- 프로젝트 → 진행: 공통 대상 선택기에서 실행을 고르고 역할·전달선을 누른다.
- 승인: 선택한 실행/프로젝트 전체, 결정할 요청/조회된 요청을 구분한다. 지난 기한의 예시는 목록·상세 모두 만료다.
- 결과: 현재 결론·남은 일·다음 행동부터 읽고 역할별 내역·시스템 집계 근거·원문을 펼친다.
- Light/Black, 뒤로가기, 본문 건너뛰기와 키보드 초점을 확인한다.

공개 합성 자료를 사용하는 읽기용 미리보기다. 원래 fixture·기한·digest를 수정하지 않았고 모든 쓰기는405로 차단한다. 실제 API·모델·후보 결정·서비스워커·외부 링크는 실행하지 않는다. 실제 인증 및 API 검사는 별도 임시 SQLite와 격리 Chrome 증적을 참고한다. 이전 PR #25 ZIP은 기존 경로에 보존했다.

제품·검사 `6e39dfc0b8ede91c235f681a9b18c2708138ec48`. [실제 HTML 검사 CI](https://github.com/HyungwonPark/ai-company/actions/runs/36086876188). 이 CI가 검사한 HTML·manifest bytes 그대로이며 원본28파일 SHA-256을 제품과 대조했다. manifest의 source_commit `b882151bbc8a21b8b03a023c99810bfab3ac09ac`은 PR 합성 checkout이며 head와 구분한다.

검증 기록: ../../product-experience/journey-usability-validation-2026-09-25.md

Linux `sha256sum -c SHA256SUMS`, macOS `shasum -a 256 -c SHA256SUMS`, Windows `Get-FileHash .\AI-Company-journey.html -Algorithm SHA256`으로 확인한다.

이 파일과 자동 Chrome 검사는 운영 배포·실기기 APK·실제 모델 실행 완료를 뜻하지 않는다.
