# 역할 그래프 통합 검증

## 목적과 기준

PR #18의 문구·조작과 PR #19의 그림 저장·ECC를 보존한 하나의 후보에서, 역할 사이에 어떤 관계가 있는지 선과 이름표로 구분하고 실제 기록을 선택하게 한다.

- PR #18: `53ee2df8a578b4424a9db714534e1bad12518cc3`
- PR #19: `faf227d90b3e637b4902ae155d1a61e8c983eb36`
- 양쪽 부모를 보존한 로컬 통합 기준: `1b50332`
- 통합 코드·검사 기준: `a77a5a97fc2453704e6d635fa4b42cea98b56792`
- 검수 후보: [PR #21](https://github.com/HyungwonPark/ai-company/pull/21)

GitHub의 두 기존 PR을 병합한 것이 아니다. 별도 브랜치의 통합 후보이며 운영 배포 승인도 포함하지 않는다.

PR #20의 [연결선 명세](graph-edge-readability-2026-09-17.md)는 `bd29d81f510c7cbd946411c9aa6f9cd4f03d5fac`에서 문서만 가져와 기준에 추가했다. 문서 PR의 병합을 기다리거나 기존 구현을 되돌리지 않았다.

통합 충돌은 세 곳에서 해결했다. `automation.py`에는 PR #18의 짧은 한국어 PM 지침과 PR #19의 guidance 전달을 함께 유지했다. `app.js`에는 그림 링크와 조작 중 갱신 보류를, `workspace-graph-ui.js`에는 그림 옵션과 PR #18의 포인터·화면 폭·초점 처리를 함께 보존했다. 두 고정 SHA가 후보의 조상임을 Git으로 확인했다.

## 화면과 조작

| 상황 | 구현 |
| --- | --- |
| 한 역할에서 여러 전달 | 안정적인 관계 ID 순으로 역할 면의 연결점을 분산한다. API 배열 순서로 위치가 바뀌지 않는다. |
| 같은 행 | 두 역할의 옆면을 연결한다. 예정 관계는 점선을 유지한다. |
| 자기 이관 | 출발·도착 연결점이 다른 외부 경로를 표시한다. 관계와 산출물의 원본 식별자는 유지한다. |
| 수정 반환·역방향 | 전용 외곽 경로로 구분하고 드래그 중 연결 면을 유지한다. |
| 역할 상자·이름표 겹침 | 역할 상자를 피하는 직각 경로와 둥근 모서리를 사용한다. 공간이 좁은 짧은 관계부터 이름표를 배치한다. 다른 이름표·역할·화살촉을 피하고 좁은 공간에서는 짧은 안내선으로 분리한다. |
| 선택 강조 | 출발·도착 역할, 선택 경로·화살촉·이름표를 함께 강조하고 다른 관계는 약하게 표시한다. 선택 해제 시 원래 표현으로 돌아온다. |
| 선 선택 영역 겹침 | 실제 포인터 위치와 가까운 선을 선택한다. 이름표·키보드·목록은 명시한 관계 ID를 선택한다. |
| 드래그·갱신 | 이동 중 전체 관계를 다시 계산하며 DOM의 포인터 대상을 보존한다. 조작 종료 후 보관한 최신 응답을 반영하고 그림 링크도 최신 fingerprint에 연결한다. |

화면의 위치와 경로는 읽기 표현이다. 원문·계획 digest·실행·승인·공유 한도·ECC 전달 기록을 수정하지 않는다. 그림 저장은 특정 시점의 별도 산출물이며 실시간 경로 계산과 같은 기능으로 집계하지 않는다.

## 검증 방식

- 순수 Node 검사: 예시 38관계의 역할 상자/이름표 충돌, 안정적 연결점, 자기 이관·반환·같은 행, 배열 순서 독립성, 입력 불변, 가까운 선 판별.
- 기존 브라우저 회귀: Light·Black, 320/390/1440px, 손떨림·드래그·다음 클릭, 너비 왕복, 키보드·갱신·연결 끊김·접속 만료·실행별 승인 링크.
- 실제 기록 독립 검사: 그래프 응답을 주입하지 않은 임시 API의 최근 9개·이전 8개 관계를 6화면에서 모두 선택한다. 두 자기 이관과 두 역할→PM 상향 보고, 원문 reference·실행 ID·목록·SVG Enter를 대조한다. 선택한 경로·화살촉·이름표·양끝 역할 강조와 해제 복원, 외곽 맞춤을 검사한다. 확대 캡처와 CDP 브라우저 터치 드래그의 입력/중간/종료 geometry를 저장한다.
- 중복 관계 독립 검사: 임시 HTTP 서버에 3노드·8관계의 예시 읽기를 주입한다. 6화면마다 모든 관계를 실제 포인터로 선택해 방향·실행 ID·고유 전달 근거를 대조한다. 자연 갱신 중 드래그, 최신 반환 근거와 그림 대상을 확인한다.
- 전후 비교: 통합 기준 `1b50332`와 후보가 같은 최근·이전 실행의 오프라인 예시를 읽는다. 각 화면의 기본 배치와 맞춤 배치를 캡처한다. 예시 관계 ID가 양쪽에서 동일한지도 검사한다.
- Chrome은 `chromiumSandbox: true`, 한국어 Noto 글꼴을 사용한다. 운영 API·외부 모델 호출과 승인 변경은 하지 않는다.

첫 후보의 이름표 위에 다른 선의 투명 클릭 영역이 놓이는 결함을 독립 검토에서 재현했다. 모든 이름표를 선보다 위의 레이어로 옮기고 각 라벨의 실제 클릭을 필수 검사로 추가했다. 이어서 키보드 초점의 44px 클릭 영역이 실제 선을 덮는 문제를 캡처에서 발견해 클릭 영역은 투명하게 유지하고 실제 선으로 초점을 표시하도록 고쳤다. 실제 생성 ID 순서에 따라 긴 관계의 라벨이 좁은 같은 행 관계의 공간을 먼저 차지하는 누락도 재현했다. 짧은 관계의 라벨부터 배치하도록 순서를 분리한 뒤, ID 80조합·480배치에서 누락과 라벨/카드/화살촉 겹침이 없음을 확인했다. 모바일 상세시트가 열린 상태에서 뒤 목록을 다시 누르던 테스트는 상세 닫기 → 목록 → 같은 관계 선택의 정상 조작 순서로 수정했다.

| 구분 | 결과/출처 |
| --- | --- |
| 로컬 순수 라우팅 | PASS. 실제 예시 38관계, 추가 중복 관계, 카드·라벨·화살촉 회피, 안정적 연결점과 배열 순서 독립성. |
| 로컬 Python | 통합 후 466개 중 463 PASS·3 SKIP. 최초 도구 샌드박스에서는 localhost 소켓을 만들지 못해 18건이 오류였고, 임시 DB/localhost를 허용한 실행에서 모두 해소했다. 제품 샌드박스 정책은 변경하지 않았다. |
| 최초 통합/라벨 수정 CI | `e7509d6`, `7e08c68`의 Python·화면 CI PASS. PR #20 추가 조건을 포함한 최종 결과로 대체하지 않는다. |
| 최종 통합 CI | `a77a5a9`의 [Python CI](https://github.com/HyungwonPark/ai-company/actions/runs/35184185225)·[화면 CI](https://github.com/HyungwonPark/ai-company/actions/runs/35184185229) 모두 PASS. Python 3.11/3.12 각각 463 PASS·3 SKIP, Android 준비 각각 4 PASS. 화면은 기존 콘솔·PR12·작업실·그래프·고정 그림 회귀와 신규 관계/터치 검사 모두 PASS. |

3 SKIP은 별도 승인된 서버 bwrap/profile 환경을 요구하는 기존 검사다. GitHub Python CI의 Android 준비 4개 및 `DEMO_READY`는 실제 앱 사용이나 모델 검수 완료가 아니다.

실제 API의 최근 9개·이전 8개 관계 × 6화면 = 102회 물리 선택과 같은 원문 reference·실행 ID·키보드·목록 대조가 통과했다. 별도 중복 관계 8개 × 6화면의 선/라벨 선택도 통과했다. 브라우저 버전은 Chrome `152.0.7977.82`다.

최종 터치 검사는 실제 `pointerType=touch`의 마지막 이동 좌표가 처리된 뒤 중간 경로를 읽는다. 초기 검사는 마지막 브라우저 프레임보다 먼저 읽어 실패했으며, 고정 지연이나 성공 조건 완화 없이 관측 경계를 보정했다. 중간 path=hit, 중간=종료 geometry, 이동 전/후 차이를 성공 JSON에서 대조했다.

독립 검수자는 최종 성공 JSON, Light/Black 320/390/1440px의 실제 캡처와 HTML 실파일을 대조해 **통합 개발·격리 fixture 검증 범위 PASS**로 판정했다. 두 자기 이관·반환·합류·PM 상향, 화살촉·경로·이름표·양끝 역할 강조를 확인했으며 추가 차단 결함은 발견하지 못했다. 운영·실기기·실제 모델 검수 승인으로 확대하지 않는다.

중간 실패는 보존한다: [`cf3d031`](https://github.com/HyungwonPark/ai-company/actions/runs/35182682480)은 상세시트 뒤 목록 재클릭, [`dd00f34`](https://github.com/HyungwonPark/ai-company/actions/runs/35183196912)는 생성 ID 순서에 따른 라벨 누락, [`75f9223`](https://github.com/HyungwonPark/ai-company/actions/runs/35183803686)는 마지막 터치 프레임 전 읽기에서 실패했다. 이 실행들을 소급 성공 처리하지 않는다.

## 전후·선택 캡처

아래는 최종 코드와 동일한 `a77a5a9`의 실제 Chrome 캡처다. 전후는 같은 정적 예시·폭·테마이며, 선택 화면은 임시 API의 저장된 예시 기록이다. 운영 모델 실행 화면이 아니다.

| 화면 | 최근 전 | 최근 후 | 이전 전 | 이전 후 |
| --- | --- | --- | --- | --- |
| Light 320px | [최근 전](assets/integrated-graph-edges/workspace-edges-before-recent-light-320.png) | [최근 후](assets/integrated-graph-edges/workspace-edges-after-recent-light-320.png) | [이전 전](assets/integrated-graph-edges/workspace-edges-before-previous-light-320.png) | [이전 후](assets/integrated-graph-edges/workspace-edges-after-previous-light-320.png) |
| Light 390px | [최근 전](assets/integrated-graph-edges/workspace-edges-before-recent-light-390.png) | [최근 후](assets/integrated-graph-edges/workspace-edges-after-recent-light-390.png) | [이전 전](assets/integrated-graph-edges/workspace-edges-before-previous-light-390.png) | [이전 후](assets/integrated-graph-edges/workspace-edges-after-previous-light-390.png) |
| Light 1440px | [최근 전](assets/integrated-graph-edges/workspace-edges-before-recent-light-1440.png) | [최근 후](assets/integrated-graph-edges/workspace-edges-after-recent-light-1440.png) | [이전 전](assets/integrated-graph-edges/workspace-edges-before-previous-light-1440.png) | [이전 후](assets/integrated-graph-edges/workspace-edges-after-previous-light-1440.png) |
| Black 320px | [최근 전](assets/integrated-graph-edges/workspace-edges-before-recent-black-320.png) | [최근 후](assets/integrated-graph-edges/workspace-edges-after-recent-black-320.png) | [이전 전](assets/integrated-graph-edges/workspace-edges-before-previous-black-320.png) | [이전 후](assets/integrated-graph-edges/workspace-edges-after-previous-black-320.png) |
| Black 390px | [최근 전](assets/integrated-graph-edges/workspace-edges-before-recent-black-390.png) | [최근 후](assets/integrated-graph-edges/workspace-edges-after-recent-black-390.png) | [이전 전](assets/integrated-graph-edges/workspace-edges-before-previous-black-390.png) | [이전 후](assets/integrated-graph-edges/workspace-edges-after-previous-black-390.png) |
| Black 1440px | [최근 전](assets/integrated-graph-edges/workspace-edges-before-recent-black-1440.png) | [최근 후](assets/integrated-graph-edges/workspace-edges-after-recent-black-1440.png) | [이전 전](assets/integrated-graph-edges/workspace-edges-before-previous-black-1440.png) | [이전 후](assets/integrated-graph-edges/workspace-edges-after-previous-black-1440.png) |

| 선택 화면 | 자기 이관 두 건 | 수정 반환 | 합류·PM 상향 보고 |
| --- | --- | --- | --- |
| light 320px | [이관1](assets/integrated-graph-edges/graph-record-recent-light-320-handoff-2.png) · [이관2](assets/integrated-graph-edges/graph-record-recent-light-320-handoff-5.png) | [반환1](assets/integrated-graph-edges/graph-record-recent-light-320-revision_return-1.png) | [보고1](assets/integrated-graph-edges/graph-record-previous-light-320-result_report-1.png) · [보고2](assets/integrated-graph-edges/graph-record-previous-light-320-result_report-4.png) · [보고3](assets/integrated-graph-edges/graph-record-previous-light-320-result_report-5.png) · [보고4](assets/integrated-graph-edges/graph-record-previous-light-320-result_report-6.png) · [보고5](assets/integrated-graph-edges/graph-record-recent-light-320-result_report-6.png) · [보고6](assets/integrated-graph-edges/graph-record-recent-light-320-result_report-7.png) |
| light 390px | [이관1](assets/integrated-graph-edges/graph-record-recent-light-390-handoff-2.png) · [이관2](assets/integrated-graph-edges/graph-record-recent-light-390-handoff-5.png) | [반환1](assets/integrated-graph-edges/graph-record-recent-light-390-revision_return-1.png) | [보고1](assets/integrated-graph-edges/graph-record-previous-light-390-result_report-1.png) · [보고2](assets/integrated-graph-edges/graph-record-previous-light-390-result_report-4.png) · [보고3](assets/integrated-graph-edges/graph-record-previous-light-390-result_report-5.png) · [보고4](assets/integrated-graph-edges/graph-record-previous-light-390-result_report-6.png) · [보고5](assets/integrated-graph-edges/graph-record-recent-light-390-result_report-6.png) · [보고6](assets/integrated-graph-edges/graph-record-recent-light-390-result_report-7.png) |
| light 1440px | [이관1](assets/integrated-graph-edges/graph-record-recent-light-1440-handoff-2.png) · [이관2](assets/integrated-graph-edges/graph-record-recent-light-1440-handoff-5.png) | [반환1](assets/integrated-graph-edges/graph-record-recent-light-1440-revision_return-1.png) | [보고1](assets/integrated-graph-edges/graph-record-previous-light-1440-result_report-1.png) · [보고2](assets/integrated-graph-edges/graph-record-previous-light-1440-result_report-4.png) · [보고3](assets/integrated-graph-edges/graph-record-previous-light-1440-result_report-5.png) · [보고4](assets/integrated-graph-edges/graph-record-previous-light-1440-result_report-6.png) · [보고5](assets/integrated-graph-edges/graph-record-recent-light-1440-result_report-6.png) · [보고6](assets/integrated-graph-edges/graph-record-recent-light-1440-result_report-7.png) |
| black 320px | [이관1](assets/integrated-graph-edges/graph-record-recent-black-320-handoff-2.png) · [이관2](assets/integrated-graph-edges/graph-record-recent-black-320-handoff-5.png) | [반환1](assets/integrated-graph-edges/graph-record-recent-black-320-revision_return-1.png) | [보고1](assets/integrated-graph-edges/graph-record-previous-black-320-result_report-1.png) · [보고2](assets/integrated-graph-edges/graph-record-previous-black-320-result_report-4.png) · [보고3](assets/integrated-graph-edges/graph-record-previous-black-320-result_report-5.png) · [보고4](assets/integrated-graph-edges/graph-record-previous-black-320-result_report-6.png) · [보고5](assets/integrated-graph-edges/graph-record-recent-black-320-result_report-6.png) · [보고6](assets/integrated-graph-edges/graph-record-recent-black-320-result_report-7.png) |
| black 390px | [이관1](assets/integrated-graph-edges/graph-record-recent-black-390-handoff-2.png) · [이관2](assets/integrated-graph-edges/graph-record-recent-black-390-handoff-5.png) | [반환1](assets/integrated-graph-edges/graph-record-recent-black-390-revision_return-1.png) | [보고1](assets/integrated-graph-edges/graph-record-previous-black-390-result_report-1.png) · [보고2](assets/integrated-graph-edges/graph-record-previous-black-390-result_report-4.png) · [보고3](assets/integrated-graph-edges/graph-record-previous-black-390-result_report-5.png) · [보고4](assets/integrated-graph-edges/graph-record-previous-black-390-result_report-6.png) · [보고5](assets/integrated-graph-edges/graph-record-recent-black-390-result_report-6.png) · [보고6](assets/integrated-graph-edges/graph-record-recent-black-390-result_report-7.png) |
| black 1440px | [이관1](assets/integrated-graph-edges/graph-record-recent-black-1440-handoff-2.png) · [이관2](assets/integrated-graph-edges/graph-record-recent-black-1440-handoff-5.png) | [반환1](assets/integrated-graph-edges/graph-record-recent-black-1440-revision_return-1.png) | [보고1](assets/integrated-graph-edges/graph-record-previous-black-1440-result_report-1.png) · [보고2](assets/integrated-graph-edges/graph-record-previous-black-1440-result_report-4.png) · [보고3](assets/integrated-graph-edges/graph-record-previous-black-1440-result_report-5.png) · [보고4](assets/integrated-graph-edges/graph-record-previous-black-1440-result_report-6.png) · [보고5](assets/integrated-graph-edges/graph-record-recent-black-1440-result_report-6.png) · [보고6](assets/integrated-graph-edges/graph-record-recent-black-1440-result_report-7.png) |

선택 캡처는 90% 이상 확대한 상태다. 데스크톱에서는 해당 관계와 양끝 역할을 함께 보도록 이동했고, 모바일은 일부 연결과 읽기 상세를 보여 준다. 전체 경로는 같은 폴더의 `*-fit-*.png` 및 미리보기의 **맞춤**으로 대조한다. 작은 맞춤 그림을 본문 읽기 화면으로 평가하지 않는다.

- [실제 기록 102회 선택·확대·목록·키보드 대조](assets/integrated-graph-edges/graph-edge-records-validation.json)
- [중복 관계 6화면의 포인터·라벨·갱신 대조](assets/integrated-graph-edges/graph-edges-independent-validation.json)
- [브라우저 실제 터치 입력과 중간/종료 경로 대조](assets/integrated-graph-edges/graph-edge-touch-validation.json)
- [전후 동일 관계 ID 대조](assets/integrated-graph-edges/workspace-edges-comparison-validation.json)
- [파일 해시·CI 출처 목록](assets/integrated-graph-edges/manifest.json)

## 미리보기

[작동하는 오프라인 HTML](assets/integrated-graph-edges/AI-Company-graph.html)을 내려받아 브라우저에서 연다. Light/Black 전환, 노드·선 선택, 목록, 이동, 실행 대상 변경, 예시 보고·승인을 사용할 수 있다. 로그인·운영 연결·계획 확정·모델 호출은 없다. 실제 APK 검증이 아니다.

미리보기 SHA-256: `3efca13e6308a54db2ee00ce625226621ab581acf114bbe79dbd97a6f9ccb787`. CI에서 생성·실행한 HTML과 저장소 다운로드 파일(125,661바이트)이 동일함을 최종 아티팩트에서 확인했다. [고정 코드의 직접 다운로드](https://github.com/HyungwonPark/ai-company/raw/a77a5a97fc2453704e6d635fa4b42cea98b56792/docs/product-experience/assets/integrated-graph-edges/AI-Company-graph.html).

## 조작 재현

1. 미리보기 HTML을 열고 **진행 → 대상 → 실행 2 · 최근**을 선택한다. **맞춤**으로 외곽 관계까지 본 뒤 **+**로 글자를 읽을 크기까지 확대한다. **이동**을 켜면 화면과 역할을 끌 수 있다.
2. 각각의 **이관** 이름표를 누른다. 상세의 출발·도착 역할이 같은지 확인한다. **수정 반환**, 개발·검사의 **결과 보고**도 선택하여 출발·도착 역할과 연결된 원본 참조를 확인한다. 선택한 선·화살촉·이름표·양끝 역할이 강조된다.
3. **닫기**로 강조를 해제한다. **목록**에서 같은 관계를 선택하여 같은 원문 참조가 열리는지 대조한다. 그래프에서는 Tab으로 선에 이동한 뒤 Enter를 사용할 수 있다.
4. **실행 1**로 바꿔 역할에서 PM으로 올라오는 결과 보고를 확인한다. Light/Black을 전환하고 화면 폭을 바꿔도 관계 수는 유지된다.

오프라인 파일은 새 서버 기록을 받지 않는다. 5초 갱신·접속 만료·자연 poll 중 드래그 검증은 임시 관리 API를 쓰는 브라우저 검사 기록으로 구분한다. 재현 환경·명령은 `.github/workflows/console.yml`의 고정 Chrome 설정을 사용한다.

```sh
node tests/ui/graph_routing_checks.mjs
node tests/ui/graph_edge_comparison.cjs
uv run --frozen python tests/ui/run_workspace_graph.py --scenario graph_edges_independent.cjs
uv run --frozen python tests/ui/run_workspace_graph.py --scenario graph_edge_records.cjs
```

`CHROME_CHANNEL=chrome`, Playwright를 찾는 `NODE_PATH`, 캡처를 저장하는 `UI_OUTPUT`을 CI와 동일하게 설정한다. 테스트는 임시 계정·DB·localhost 서버를 만들며 실행·승인·외부 API 쓰기를 차단한다. 기록 JSON은 선택한 edge ID·실행 ID·화면 폭·테마, 캡처 파일과 터치 geometry를 연결한다.

## 적용과 복구 경계

이번 산출물은 검수 후보까지다. 현재 운영 웹·PM/조정기·번역 worker·커플 서비스·큐·타이머·DB는 교체하지 않는다. PR #10과 최종 후보 승인 상태도 변경하지 않는다.

후속 공개 적용을 요청할 때는 검수한 정확한 커밋의 웹 이미지와 기존 가동 이미지 식별자를 고정하고, 공유 JS/CSS 정적 자산의 교체를 대조한다. 이번 연결선 수정 자체에는 DB 마이그레이션이나 worker 재시작이 필요하지 않다. 다만 현재 운영 버전에서 PR #19 그림 저장·ECC까지 함께 적용하려면 그 문서의 별도 코드·저장소·카탈로그 변경 범위를 함께 검토해야 한다. 이를 단순 정적 자산 교체 승인에 포함하지 않는다.

공개 적용 전 실패하면 후보를 적용하지 않는다. 정적 웹 변경을 추후 적용한 뒤 화면 결함이 발생하면 승인한 직전 웹 이미지로 복구하고 DB·큐·승인 이력을 과거 상태로 덮어쓰지 않는다. PR #19 저장 기능까지 적용된 경우 그 기능의 고정 복구 조건을 따른다.

## 남은 제약

- 사용자가 노드를 서로 포개거나 매우 많은 관계를 좁은 공간에 모으면 모든 선의 교차를 없앤다고 보장하지 않는다. 경로 탐색 상한 또는 이름표 공간 부족은 안내하고, 이동·확대·목록에서 원본 관계를 계속 선택할 수 있다. 정확히 같은 교차점은 이름표나 별도의 선 구간으로 구분한다.
- 고정 Archify 그림의 자기 이관 제외는 PR #19의 기존 한계로 유지한다. 실시간 그래프의 자기 이관 표시와 구분한다.
- 실제 모델의 ECC 준수, APK 실기기 조작, 서명키 외부 보관·복원은 이번 예시 브라우저 검증으로 완료하지 않는다.

최종 수정일: 2026-09-17 UTC
