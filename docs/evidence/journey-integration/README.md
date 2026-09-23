# 순서형 통합 후보의 공개 검증 근거

제품 `d46136da9108608e7bdce6bdbfb0da35e825126a`, 같은 제품의 검사 `6a366e69909b204881cc1d0a547a7b5cd06173ee`. [후보 식별자·CI·해시](candidate.json), [통합 검증 기록](../../product-experience/journey-integration-validation-2026-09-24.md), [적용·복구안](../../product-experience/journey-integration-rollout-2026-09-24.md), [전체 목표 대응표](../../product-experience/journey-overall-goal-map-2026-09-24.md)를 함께 읽는다.

## 자료 구분

| 파일 | 확인 범위 |
| --- | --- |
| [독립 코드 검수](independent-code-review.md) | 별도 Astra/Ultra 개발 검수, 발견 P2 두 건과 수정 재현, 제품·이미지·미리보기 해시 대조 |
| [독립 화면 검수](independent-visual-review.md) | 최종 캡처 직접 열람과 Chrome 조작 증거 대조. 검수자 직접 APK/브라우저 조작 아님 |
| [C 읽기](journey-workspace-validation.json) | 실제 임시 API의 실행 선택·보고/승인·뒤로가기·누락·지연·오프라인·401 |
| [D 쓰기](journey-writes-validation.json) | 실제 임시 API의 생성·명세·PM 요청·계획 확정·후보 결정·경합·권한 거부. 계획/번역/후보 사실은 모의 주입 |
| [G1/G3 조작](journey-graph-validation.json) | 중앙·본문·카메라·도움말·상세·갱신·초점 |
| [G2 전후](graph-baseline-comparison.json) | 고정 기준 7522748 JS/CSS와 같은 fixture·테마·폭·실행 비교 |
| [1px 왕복](workspace-drag-stability-validation.json) | 기존 결함 기준 0655de2 재현과 후보의 실제 포인터 왕복·release·이름표 선택. 752 전후 비교와 다른 목적의 기준 |
| [터치](graph-edge-touch-validation.json) · [관계 원본](graph-edge-records-validation.json) · [독립 선 조작](graph-edges-independent-validation.json) | 최종 Console CI의 합성 fixture 실제 Chrome 조작 |
| [기존 그래프](workspace-graph-validation.json) · [독립 그래프](workspace-graph-independent-validation.json) | 임시 API·실제 갱신·선택·오프라인 회귀 |
| [고정 그림](workspace-diagram-validation.json) · [독립 그림](diagram-independent-validation.json) | 긴 역할 원문·한국어 읽기·컴파일러·요청 복구·sandbox 보존 |
| [DB 불변 C](journey_workspace-database.json) · [그래프](journey_graph-database.json) | 임시 SQLite 29개 업무 테이블 불변. 인증 표는 별도 |
| [미리보기](journey-preview-validation.json) | 실제 file:// HTML, 원본 해시·읽기·외부 요청/쓰기 거부 |
| [서비스워커](journey-service-worker-validation.json) | 격리 localhost의 v8→v9→v8, 오래된 탭·비공개 API/POST 비캐시 |
| [이미지](image-validation.json) · [114개 소스](image-source-manifest.json) · [검사 스크립트](image-check.py) | 임시 컨테이너 설치 파일·HTTP·권한 경계. 서비스 시작/worker/운영 검사 아님 |

자료의 프로젝트·run·plan·approval 식별자는 임시 시험 자료다. 운영 원문·DB·계정·승인 내용·자격 증명은 복사하지 않았다. 독립 보고서의 비공개 임시 경로는 공개본에서 자료 이름으로만 표시했다. 이미지 검사 코드의 loopback은 임시 컨테이너 안의 시험 경로다.

`screens/`는 최종 실제 캡처 중 대표 13장이다. 전체 캡처는 [Journey CI](https://github.com/HyungwonPark/ai-company/actions/runs/35905379886)의 `journey-evidence`와 [Console CI](https://github.com/HyungwonPark/ai-company/actions/runs/35905379779)의 `console-screenshots`에 있다. JSON의 다른 이미지 파일명은 전체 artifact를 기준으로 한다. 전체 artifact는 보존기간·GitHub 로그인 조건이 있으며 공개 [미리보기 ZIP](../../previews/journey/AI-Company-journey-preview.zip)은 저장소에서 별도로 제공한다.

이 자료는 **비운영 제품 개발 검증**이다. 운영 모델 흐름, 마스터의 직접 확정, APK 실기기, 도메인 서비스워커 갱신, 키 복원과 배포 완료로 확대하지 않는다.
