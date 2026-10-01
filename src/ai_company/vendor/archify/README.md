# 고정 Archify workflow 컴파일러

출처: https://github.com/tt-a1i/archify/tree/72c750bb070d95171dbb2244e5b62b1b7da69c12

`UPSTREAM.json`에 원본 커밋과 파일별 SHA-256을 기록했습니다. 포함한 원본 파일은 수정하지 않았습니다. 공식 workflow renderer가 호출하는 `compileWorkflow`와 전이 의존성만 사용합니다. 전역 설치, npm 설치, 업데이트 조회, 브랜드 URL 수집은 실행 경로에 없습니다. 실행은 Node 18 이상이 필요합니다.

`../diagram_render.mjs`는 네트워크 기능을 차단하고 고정 컴파일러를 호출하는 어댑터입니다. 한국어 HTML과 읽기 컨트롤, 파일 보관과 receipt는 AI Company의 별도 코드입니다. 공식 viewer나 `deliver` CLI의 9/9 showcase 검사를 수행했다는 의미가 아닙니다. compiler 검증과 제품 검증을 각각 기록합니다.

MIT 저작권 및 허가문은 `LICENSE`에 있습니다. 의존성에 포함된 선택적 브랜드 경로는 `THIRD_PARTY_NOTICES.md`를 보존하되 현재 변환기는 브랜드 필드를 생성하지 않습니다. 공식 내장 폰트나 viewer는 포함·실행하지 않으며 한국어 글꼴은 시스템 fallback입니다.
