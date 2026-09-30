# 현황·매뉴얼 후보 검증

## 고정 후보

- 초안 PR: [#38](https://github.com/HyungwonPark/ai-company/pull/38), 문서 전용 기준 브랜치 `docs/work-review-handoff`.
- 제품·검사 후보: `41506127d89e6c6576555b808abcd3895b6fd9dc`.
- 문서 원본: `35df64ac5a2bfe0dd126d53128c9e63da7404aa0`의 `docs/review/index.md`, `manual.md`.
- 문서가 표시하는 설치 기준: `e659fc66cadfb5af77f85327314671b46a0383e0`. 이 코드를 운영에 다시 설치하는 작업이 아니다.
- ZIP SHA-256: `c6933191fd6e2711ceb9ff58a49c2825560d5d886c8dd16c6c4868871e2b5cb1`.
- [다운로드 미리보기](../previews/review/AI-Company-review-preview.zip): 압축을 풀고 `preview/index.html`을 연다. 인터넷 없이 현황↔매뉴얼 이동 가능. 근거 링크 조회에는 인터넷이 필요하다.
- [게시·복구안](preview-plan-2026-09-30.md), [원본·산출물 해시](../previews/review/manifest.json), [검사·캡처 해시](assets/evidence-manifest.json).

## 실제 구현·검사

| 범위 | 확인 결과 |
| --- | --- |
| 단일 원본 | 지정한 Git 커밋의 Markdown만 읽고 2개 정적 문서 생성. 운영 DB·로그·계정·승인 원문을 입력하지 않음 |
| 시각·버전 | 원문 보고 `2026-09-29 16:17 KST`, 기준일·버전·원문 커밋·설치 기준 코드를 구분. 생성 시각을 최신 관측으로 표시하지 않음 |
| 링크·HTML | 커밋 고정 근거, 두 문서 이동·한글 목차, 위험 링크·외부 이미지 거부, raw HTML 비실행 처리 |
| Python | 재현 가능 생성·보존·시각·링크·HTML·목차 검사 6개 PASS |
| Caddy | network none/read-only/cap-drop/no-new-privileges 임시 컨테이너. GET·HEAD·내용 해시·404·405·기존 경로 경계 15개 PASS |
| 실제 Chrome | [후보 CI](https://github.com/HyungwonPark/ai-company/actions/runs/36673809393), Chrome `153.0.8010.52`, sandbox=true. 320/390/1440px × Light/Black × 현황/매뉴얼 및 확대·서비스워커·오프라인 20묶음 PASS, PNG 48장 |
| 실제 조작 | 직접 진입·새로고침·목차·본문 바로가기·키보드·문서 이동·뒤로가기·버전 펼침 검사. 실제 게시용 CSP로 스타일·테마 적용 확인 |
| 확대 | h1/h2/표/목차/브랜드의 계산된 글자 크기가 200%이며 목차 영역 겹침·가로 넘침 없음. 일반 브라우저의 모든 확대 방식 검증으로 확대 해석하지 않음 |
| 서비스워커 | 실제 운영 코드 `2087eccae3f946bc6d676904462a9b2acabd8521` 원문을 임시 서버에 연결. 문서 경로를 앱 셸로 가로채지 않음 |
| 패키지 | 원격 CI ZIP과 Git ZIP을 다시 대조해 SHA-256 일치 |
| 별도 독립 검수 | 생성기·보안·해시·원본 및 실제 캡처 검수. 지적 수정 후 `4150612`에서 추가 차단 결함 없음 |

## 최초 실패와 수정

1. 최초 Chrome 검사는 한글 `URL.hash`의 인코딩을 그대로 DOM ID와 비교해 실패했다. 디코딩 후 `getElementById`로 대조하여 통과했다. [최초 실패 CI](https://github.com/HyungwonPark/ai-company/actions/runs/36672921447)와 [수정 성공 CI](https://github.com/HyungwonPark/ai-company/actions/runs/36673480375)를 구분한다.
2. 독립 검수에서 px 폰트를 제외한 불완전한 확대 검사와 목차 이동 후 첫 화면을 놓친 캡처를 지적했다. rem 폰트·실제 계산 크기와 첫 진입·표·버전 캡처를 연결했다.
3. 실제 캡처에서 확대된 두 열 목차의 제목·번호 겹침, 이중 번호와 안내 문장 붙음이 발견됐다. 한 열·자동 번호 제거·초기 접힘·문장 구분을 적용하고, 목차 박스 겹침 회귀를 추가했다. 최종 캡처에서 해결됐다.
4. 로컬 Chrome은 기존 AppArmor/userns 제한으로 sandbox 시작이 차단됐다. 전역 설정·sandbox를 해제하지 않았고, sandbox=true 원격 Chrome 성공을 별도 근거로 사용한다.

## 읽기 화면

[모바일 Light](assets/review-light-390.png) · [모바일 Black](assets/review-black-320.png) · [PC 매뉴얼](assets/review-manual-light-1440.png) · [Black 표](assets/review-manual-black-390-table.png) · [200% 목차](assets/review-manual-390-text-200.png) · [200% 버전](assets/review-manual-390-version-200.png).

## 운영 준비와 미적용

2026-09-30 읽기 대조에서 기존 웹·관리 세션 조회·Android 인증서 경로·커플 서비스는 200, `/review`·`/review/manual`은 404였다. 기존 전체 Caddyfile과 후보를 해석해 기존 도메인·기본 처리·인증서 경로가 보존되고 문서 처리 1개만 추가됨을 확인했다. 독립적인 route 그룹 번호의 이름만 정규화했으며 그룹 간 배타 관계는 그대로 비교했다.

원본·후보 Caddyfile, 실제 호스트 경로·해시·적용 순서·복구 절차·baseline은 서버 비공개 경로에 보존했다. 문서 페이지는 공개 Git 문서만 제공한다. 기존 API 비밀번호 로그인이 이 별도 정적 경로에 자동 적용되지는 않는다.

**이번 완료 범위는 구현·검사·독립 검수·패키징·게시 준비다. 운영 도메인 게시·자동 갱신·로그인 실제 조작·APK 실기기 확인은 미완료다.** Mermaid는 접힌 원문을 보존하며 SVG 렌더링은 하지 않았다.

실제 Claude 독립 검수는 기존 UNKNOWN 보호 때문에 대기다. 별도 에이전트 검수를 실제 Claude 호출로 보고하지 않는다. 프로젝트 실행기를 통한 Claude·Astra 추가 호출·시험 호출·tick·계정/장부 변경·worker 시작·서비스 재적용은 0건이다. 현재 개발 세션을 유지했으며 모델/effort 설정을 변경하지 않았다.

초기 공개 적용의 승인 범위는 **문서 HTML 2개와 전용 버전 디렉터리·연결, 기존 Caddy에 경로 처리 추가·설정 재적용 1건**이다. 웹·worker·DB·장부·quota 타이머·커플 앱·PR #10 pending·병합·서명키는 대상이 아니다. 승인 후에도 문서 push를 무조건 운영에 자동 게시하지 않는다.

[최종 수정일: 2026-09-30, v1.0]
