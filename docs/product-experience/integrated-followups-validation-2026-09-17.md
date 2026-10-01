# 고정 그림과 역할 지침 — P2-A/B

PR #16 통합 명세의 후속 구현이다. PR #17의 진행 그래프와 기존 A/B/C 시안·PR #13 실행 및 승인 계약을 보존한다. 이 문서는 운영 적용 승인이 아니다.

## 이번 후보

- 진행 그래프의 **그림**에서 선택한 계획·실행의 저장 자료를 고정한다. 명시적 **그림 만들기**만 생성 요청을 보내며 조회·다운로드는 개발이나 승인을 시작하지 않는다.
- 실제 Archify `72c750bb070d95171dbb2244e5b62b1b7da69c12`의 고정 workflow 컴파일러를 패키지에 포함한다. 한국어 정적 SVG와 별도 읽기 화면, 원본 JSON·해시·receipt를 보관한다. 전체 Archify viewer·공식 deliver 검수를 수행한 것으로 표시하지 않는다.
- 현재 컴파일러가 처리하지 못하는 자기 이관선은 `renderer_exclusions`와 한국어 관계 목록에 원본 식별자·역할·이유·산출물을 유지한다. 전체 관계를 그림에 표현했다고 주장하지 않는다.
- 실패·중단 요청과 이전 그림을 별도로 유지한다. 응답 유실 후 같은 키는 최초 고정 자료를 반환하고, 중단된 생성은 같은 자료로 재개한다. 새 그림은 별도 요청이다. 프로젝트당 요청 1,000개, 조회 최근 100개, 생성 1개씩·30초 제한이며 기존 기록을 자동 삭제하지 않는다.
- 생성 HTML은 인증된 별도 경로의 opaque sandbox로 읽으며 스크립트·네트워크·폼 권한이 없다. 운영 페이지의 CSP를 완화하지 않는다. PNG 캡처나 생성 receipt는 실제 작업·모델 준수·마스터 승인을 뜻하지 않는다.
- ECC `8321021c54d670126ce3b2969d5deb880b4b0c2a`의 선정 문서를 PM·개발·독립 검수·최종 검수용 한국어 지침으로 변환했다. 선택한 서버 카탈로그의 `guidance`를 실행 명세에 고정하고, 문서/manifest 변조를 거부한다. 선택하지 않은 기존 config/spec의 digest는 유지한다.
- `guidance_deliveries`는 준비·실행기 호출 경계·프로세스 시작 관측·반환/실패를 구분한다. 모델이 지침을 준수했는지는 미검증으로 남긴다. fixture로 공급자 프롬프트와 실행 ID 결속·한도 이관·중단 복구를 검증한다.

## 화면과 검수

P1 그래프의 탐색 구조는 유지하고 **그림**을 보조 동선으로 추가한다. 그림 화면은 대상 설명 → 만들기/진행 → 저장본과 다운로드 → 기준/이력 순서다. 실시간 진행과 특정 시점의 그림을 구별한다. 긴 개발 설명을 첫 화면 제목에 추가하지 않는다.

실제 임시 DB·인증 API·고정 컴파일러로 검증하며 운영 DB와 모델 계정을 사용하지 않는다. `tests/ui/diagram_view.cjs`는 Light·Black × 320/390/1440px, 키보드, 다운로드 해시, opaque iframe, 응답 유실 후 동일 요청과 승인 기록 보존을 검사한다. 독립 검수와 원격 CI 결과는 최종 코드에 고정해 아래 기록한다.

검증 후보는 **`5083669c5315de13467e938ce19e73a311bc147e`**, 초안은 [PR #19](https://github.com/HyungwonPark/ai-company/pull/19)다. PR #17 위에 쌓은 별도 브랜치이며 병합하지 않았다.

| 구분 | 실제 결과 |
|---|---|
| [Python CI](https://github.com/HyungwonPark/ai-company/actions/runs/35177986558) | Python 3.11·3.12 각각 466개 중 463 PASS, 서버 전용 bwrap/profile 검사 3 SKIP |
| 같은 CI의 기존 검사 | Android 준비 4 PASS, 시뮬레이션 수정 루프 DEMO_READY |
| [화면 CI](https://github.com/HyungwonPark/ai-company/actions/runs/35177986553) | 기존 UI·PR12·A/B/C·P1 그래프·오프라인 미리보기와 신규 그림/독립 실패 시나리오 PASS |
| 실제 임시 API + Node | 응답 유실 후 같은 요청 1건, 자료/다운로드 hash, opaque iframe·권한 거부, 운영 실행/승인 원본 불변 |
| 합성 API 독립 검사 | 중단 이력 A와 브라우저의 다른 요청 B 우선순위, 403·연결 유실·새로고침, 실패 이유 재열기와 키보드 |
| ECC | 선정 문서의 실행기 전달, 고정 해시 변조 차단, quota 이관, WAITING_RETRY 중단 경계, fixture PM→병렬 역할→독립/최종 검수의 같은 실행 결속 |
| 독립 검수 | 코드·실제 캡처·브라우저 JSON·HTML/SVG hash를 대조해 개발/fixture 범위 PASS |

Chrome 152.0.7977.82의 `chromiumSandbox:true`로 검사했다. 320/390/1440px의 Light·Black, 44px 주요 터치 영역, 키보드와 640px의 실제 200% 글자 확대를 확인했다. 축소 도식은 개요이며 세부 내용은 **역할 목록·관계 목록**으로 읽는다. Android 실기기 결과는 아니다.

첫 시각 검수에서 중복 설명, 내부 Black 불일치, 한국어 단어 분리를 발견했다. 부모에는 기준·다운로드만 두고 두 개의 고정 테마 미리보기 HTML을 별도 hash로 보관해 보완했다. 모바일에서 그림 시작이 약 y970→y495로 앞당겨졌다. 다운로드 `index.html`은 독립 문서의 설명·원본 기준을 그대로 보존한다. 이전 형식의 파일은 자동 덮어쓰지 않으며, 테마 차이를 표시하고 원래 HTML로 읽는다.

- [Light 390px](assets/integrated-followups/workspace-diagram-light-390.png) · [Black 320px](assets/integrated-followups/workspace-diagram-black-320.png) · [PC 1440px](assets/integrated-followups/workspace-diagram-light-1440.png)
- [200%/640px](assets/integrated-followups/workspace-diagram-200-percent-640.png) · [실패 이력](assets/integrated-followups/diagram-independent-failed-history-390.png) · [중단 재개](assets/integrated-followups/diagram-independent-prepared-recovery-390.png)
- [예시 HTML](assets/integrated-followups/workspace-diagram.html) · [예시 SVG](assets/integrated-followups/workspace-diagram.svg) · [생성 receipt](assets/integrated-followups/workspace-diagram-receipt.json) · [외부 검수 기록](assets/integrated-followups/external-review.json)
- [원격 캡처·HTML 묶음](https://github.com/HyungwonPark/ai-company/actions/runs/35177986553/artifacts/10479138558)은 GitHub 로그인 후 내려받아 HTML을 브라우저로 열 수 있다. 예시 자료이며 로그인·계획 확정 화면을 대신하지 않는다.

HTML SHA-256: `2cfde4e97720dbb62031b049df8108f88b8ebb2ed790740cd5c038a8b795de39`. 생성 순간 receipt의 `browser/visual_review: not_run`은 덮어쓰지 않고 실제 후속 검수를 별도 연결했다. 공개 접속 CI 성공도 이 후보의 운영 배포 증거로 세지 않는다.

## 후속 적용과 복구

기존 Python 전용 웹 이미지에는 Node가 없으므로 새 그림 기능이 있다고 가정하지 않는다. 별도 `deploy/console/Dockerfile.diagrams` 후보를 제공한다. 기존 Dockerfile과 운영 이미지/컨테이너·번역 worker·Caddy·커플 서비스·타이머는 변경하지 않는다.

적용 검토 대상은 새 웹 코드와 Node가 포함된 웹 이미지, ECC를 사용하려는 경우 새 worker 코드와 **명시적으로 선택한 카탈로그 버전**이다. 이번 개발 중 운영 카탈로그에 ECC를 자동 추가하거나 계정·예산·파일·최종 검수자를 바꾸지 않는다. 모델 연결 P2-C의 로그인/제한된 실제 확인은 별도 후속 범위다.

새 테이블은 파생 그림 요청 `management_diagrams`와 실행기 전달 기록 `guidance_deliveries`다. 기존 계획·명세·승인·사용량 원문을 변경하지 않는다. 그림 산출물은 상태 루트의 `diagrams/` 아래에 보관한다.

그림 생성만 끄는 별도 운영 플래그는 아직 없다. 복구 시 새 그림 요청을 받는 웹 후보를 정상 종료하고 기존 웹 이미지로 교체한다. 중단된 요청과 파생 파일은 보존한다. 기존 코드로 웹만 복구할 경우 파생 테이블·그림을 지우거나 DB를 과거 사본으로 덮어쓰지 않는다. ECC를 선택한 새 명세나 작업이 있다면 구 worker가 이를 해석할 수 없으므로 새 배정을 멈추고 호환 worker로 기록을 읽으며 복구한다. 기존/예약 작업과 승인 기록을 삭제하거나 구 설정으로 소급 실행하지 않는다.

### 고정 이미지와 적용 경계

| 항목 | 고정값/근거 |
|---|---|
| 웹 후보 이미지 | `sha256:bf8d1b01fb6bbb7ee6bc85956dc696eaf292d80ff5b5cfa60a6b2b89cf1cb74e` |
| 후보 제품 코드 | `5083669c5315de13467e938ce19e73a311bc147e` |
| 현재 웹/복구 이미지 | `sha256:0f44d2e0a3706567bafea34c640345264fcace697a5ce317405366380cbfa1f8` (`23ac8a7`) |
| Python 기반 | `python@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e` |
| Node 기반 | `node@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436` / 실행 확인 `v22.23.2` |
| 설치 패키지 대조 | 제품 파일 113개, 경로별 SHA-256 JSON의 SHA-256 `d3b7c42163546e03360943cf23787fa41c38424f9b3c3bd7e2f23a7b30008159`: Git 소스와 wheel 설치본 일치 |

별도 후보 컨테이너를 UID 65532, read-only root, `--network none`, `--cap-drop ALL`, `no-new-privileges`, PID 64, 512MiB, CPU 1, 64MiB `/tmp` tmpfs에서 실행했다. 운영 상태·포트·계정·Docker 소켓을 연결하지 않고 **실제 Node 그림 생성·ECC 검증·루트 쓰기 거부·외부 네트워크 거부**를 확인했다. 초기 검증의 Node 경로 차이는 실행기 절대경로 확인 후 자식 환경 정리로 수정했다. `NODE_OPTIONS`가 전달되지 않는 회귀도 통과했다.

빌드에는 아래 고정 인자를 쓴다. 이것은 빌드 명령이며 배포 명령이 아니다.

```bash
docker build -f deploy/console/Dockerfile.diagrams \
  --build-arg PYTHON_IMAGE=python@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e \
  --build-arg NODE_IMAGE=node@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436 \
  -t ai-company-preview:integrated-followups-5083669 .
```

실제 적용 시 먼저 현재 Compose·이미지·상태 경로가 위 복구 기준과 같은지 확인한다. **웹 컨테이너 1개**의 이미지만 새 후보로 교체하고 기존 내부 네트워크·상태 볼륨·public origin·보안 옵션을 유지한다. Caddy와 공개 포트 변경은 필요하지 않다. 로그인·원본 기록·같은 계획/실행의 그림·실패 복구를 확인한다. 적용 후 문제가 있으면 웹만 위 복구 이미지로 되돌리고 DB/파생 파일은 유지한다.

ECC 실제 활성화는 웹 교체와 별도다. worker가 새 코드를 읽는지 먼저 확인하고, 승인된 기존 정책/파일/검사/계정/예산을 그대로 유지한 **새 카탈로그 버전**에만 고정 `guidance`를 선택해야 한다. 등록은 실행 승인이 아니며 이후 마스터가 확정한 계획에만 그 버전이 고정된다. 운영 카탈로그 선택·worker 교체는 이번에 수행하지 않았다. 이미 guidance 명세가 생긴 상태의 구 worker 재시작은 금지하고, 호환 worker에서 새 배정을 멈춘 채 기존 진행/예약 기록을 보존해야 한다.

이 후보는 이전 운영 승인 대상과 다르며 이번 프런트엔드 지시에는 배포 승인이 없다. 적용 승인을 다시 요청하거나 기존 웹/worker를 같은 내용으로 재배포하지 않았다. 읽기 전용 확인 시 기존 웹·커플 앱·Caddy·DB는 정상 상태였고 운영 변경 명령을 실행하지 않았다.

## 별도 확인 항목

운영 적용, 실제 모델의 새 지침 수신·준수, APK에서 마스터 목표 입력/계획 확정, 서명키 외부 보관/복원은 이번 코드/fixture 검사로 완료하지 않는다. PR #10 후보 승인은 pending을 유지한다. 공개 적용·병합은 수행하지 않는다.
