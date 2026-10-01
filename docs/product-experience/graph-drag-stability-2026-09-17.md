# PR #21 드래그 안정성 후속 검증

사용자 제보: 최근 `graph-preview/fixture.json`, `graphLayout(nodes,false)` 기본 배치에서 PM을 초기 x+8↔x+9로 이동하면 PM→검사 명세선의 가로 구간이 y194↔274, 이름표 x가103↔212.6667로 바뀐다.

## 수정

- 직전 표시 경로의 방향과 내부 굴곡을 보존하고 이동한 연결점의 첫·마지막 구간만 조정한다. 카드 주변6px 여백과 출입 방향을 재검사하며 유효하지 않으면 기존 장애물 회피 탐색을 수행한다.
- 기존 이름표의 선상 연결점을 새 경로에 투영해 위치를 유지한다. 카드·화살촉·다른 이름표와 충돌하면 그 이름표를 재배치한다.
- 캐시는 실행별 화면 안에만 두고 화면 크기 전환 시 초기화한다. 관계 집합·종류·끝점·노드 크기가 바뀌면 새 그래프로 배치한다. 기존 라벨 예약이 새 짧은 의존선의 라벨을 숨기는 독립 검수 발견사항을 함께 수정했다.
- 기존 경로가 유효하다는 이유로 가장 짧은 새 경로를 계속 선택하지 않는다. 실제 장애물이 들어오면 우회가 달라질 수 있으며, 관계 추가 시 전체 포트·이름표 재배치는 작은 좌표 이동과 구분한다.

## 검증 범위

`tests/ui/graph_routing_checks.mjs`: +0/+8/+9 세 시작 경로에서 반복1px 왕복, 직전 결과 불변, 포인터 해제 후 같은 입력 재계산, 실제 장애물 진입, 관계 교체·삭제·추가. 기존38관계·480생성ID 배치 검사 유지.

`tests/ui/graph_drag_stability.cjs`: 이전 고정 코드0655de2와 수정본에 같은 최근 fixture, Light·Black320/390/1440. 실제 Playwright mouse 이동으로 +8↔+9를 반복하고 경로·히트영역·이름표 좌표·trusted pointer 이벤트를 기록한다. 해제·다시 렌더·정확한 이름표 선택과 원본 기록 불변도 검사한다. 화면 캡처는 각 조건의 +8/+9에서 저장한다.

## 확인 결과와 출처

- 제품 수정: `5fafaaa270cafedeec0e18d24a24fced506dcd5d`. 이후 변경은 검사·문서·증거이며 이 제품 코드와 동일하다.
- 실제 1px 조작 증거: `bc7b9d6c3ef5d82ec518462fb4f12832d02f37fa`, Chrome `152.0.7977.82`, `chromiumSandbox:true`. [원격 실행 로그](https://github.com/HyungwonPark/ai-company/actions/runs/35187598708)에서 새 왕복 드래그 검사, 전후 비교, 독립 관계 선택 검사가 각각 PASS했다.
- 두 테마×세 화면폭×이전/수정본 =12조건, 각8개 포인터 좌표를 저장했다. 실제 `isTrusted=true`, mouse 이벤트와 노드 x좌표를 확인했다. 수정본의 경로 좌표·이름표 변화는 각1px 이내이며 해제 후 경로와 이름표도 같다.
- 회귀: 기존38관계·480생성ID 배치, 세 시작점의24왕복 좌표, 신규 관계 도착38경계, 장애물 진입과 관계 제거/교체 검사 PASS. 독립 검수자가 별도로 같은 개수의 ID/종류 교체76경계도 통과 확인했다.
- [이 커밋의 Python CI](https://github.com/HyungwonPark/ai-company/actions/runs/35187598636)는 PASS. 화면 CI 전체는 기존 터치 검사의 ‘이름표가 반드시 바뀌어야 함’ 조건에서 실패했다. 유효 이름표 유지 요구와 충돌하는 조건을 선·이름표 연결 정합 검사로 수정했고, 노드 실제 이동·경로 변화·path=hit·during=after·실제 터치·원본 기록 보존 조건은 유지한다. 이 이전 실행 전체를 PASS로 바꾸어 기록하지 않는다.
- 앞선 [35187353857](https://github.com/HyungwonPark/ai-company/actions/runs/35187353857) 실패는 새 테스트가 이동 모드를 켜지 않은 원인이었다. 실제 ‘이동’ 버튼을 클릭하도록 수정한 이후 위12조건을 통과했다.
- 독립 검수자가 위12조건/96포인터 표본과 Light1440 전후4장·Black320 왕복·Light390 화면을 대조해 새 시각 차단이 없음을 확인했다. 수정한 터치 정합 검사는 최종 후보 CI에서 따로 실행한다.
- 최종 후보의 Python·전체 화면 CI와 독립 검수 결과는 [PR #21](https://github.com/HyungwonPark/ai-company/pull/21) 본문과 해당 HEAD의 Checks에 연결한다. 아래 그림·좌표 원본은 위 `bc7b9d6`에 고정하며 새 실행의 증거로 덮어쓰지 않는다.
- 로컬 임시 Chromium은 공유 라이브러리 부족으로 시작하지 못했다. 로컬 브라우저 PASS로 보고하지 않으며, GitHub의 샌드박스 Chrome 조작 결과와 구분한다.

| 실제 데스크톱 조건 | +8px | +9px | 변화 |
| --- | --- | --- | --- |
| 이전 가로선 y |194|274|80px 급변|
| 이전 이름표 x |103|212.6667|109.6667px 급변|
| 수정 가로선 y |274|274|유지|
| 수정 이름표 x |211.6667|212.6667|1px|

수정본을 기본 배치에서 처음 드래그하면 y274 경로를 유지한다. +8에서 처음 계산한 y194 경로로 시작하는 경우도 순수 함수 회귀에서 보존을 확인했다. 특정 y값을 강제한 것이 아니라 현재 유효한 경로를 유지한다.

## 조작 화면과 다운로드

[작동하는 HTML 내려받기](assets/graph-drag-stability/AI-Company-graph.html) · [96개 실제 포인터 표본](assets/graph-drag-stability/workspace-drag-stability-validation.json) · [파일별 SHA-256·출처](assets/graph-drag-stability/manifest.json)

HTML에서 ‘진행’ → ‘실행 2 · 최근’ → ‘이동’을 누른 뒤 PM 카드를 조금 오른쪽으로 옮겨 왕복한다. 노드·이름표 선택은 예시 기록만 읽으며 운영 API·모델 호출·승인 쓰기는 하지 않는다.

| 테마·폭 | 이전 +8 / +9 | 수정 +8 / +9 |
| --- | --- | --- |
| light 320px | [+8](assets/graph-drag-stability/workspace-drag-before-light-320-plus8.png) / [+9](assets/graph-drag-stability/workspace-drag-before-light-320-plus9.png) | [+8](assets/graph-drag-stability/workspace-drag-after-light-320-plus8.png) / [+9](assets/graph-drag-stability/workspace-drag-after-light-320-plus9.png) |
| light 390px | [+8](assets/graph-drag-stability/workspace-drag-before-light-390-plus8.png) / [+9](assets/graph-drag-stability/workspace-drag-before-light-390-plus9.png) | [+8](assets/graph-drag-stability/workspace-drag-after-light-390-plus8.png) / [+9](assets/graph-drag-stability/workspace-drag-after-light-390-plus9.png) |
| light 1440px | [+8](assets/graph-drag-stability/workspace-drag-before-light-1440-plus8.png) / [+9](assets/graph-drag-stability/workspace-drag-before-light-1440-plus9.png) | [+8](assets/graph-drag-stability/workspace-drag-after-light-1440-plus8.png) / [+9](assets/graph-drag-stability/workspace-drag-after-light-1440-plus9.png) |
| black 320px | [+8](assets/graph-drag-stability/workspace-drag-before-black-320-plus8.png) / [+9](assets/graph-drag-stability/workspace-drag-before-black-320-plus9.png) | [+8](assets/graph-drag-stability/workspace-drag-after-black-320-plus8.png) / [+9](assets/graph-drag-stability/workspace-drag-after-black-320-plus9.png) |
| black 390px | [+8](assets/graph-drag-stability/workspace-drag-before-black-390-plus8.png) / [+9](assets/graph-drag-stability/workspace-drag-before-black-390-plus9.png) | [+8](assets/graph-drag-stability/workspace-drag-after-black-390-plus8.png) / [+9](assets/graph-drag-stability/workspace-drag-after-black-390-plus9.png) |
| black 1440px | [+8](assets/graph-drag-stability/workspace-drag-before-black-1440-plus8.png) / [+9](assets/graph-drag-stability/workspace-drag-before-black-1440-plus9.png) | [+8](assets/graph-drag-stability/workspace-drag-after-black-1440-plus8.png) / [+9](assets/graph-drag-stability/workspace-drag-after-black-1440-plus9.png) |

## 적용·복구

이번 변경은 PR #21 검수 후보이며 운영 적용하지 않는다. 웹 UI의 읽기 전용 경로 배치만 바뀌고 데이터베이스·worker·모델·실행·승인 계약은 바뀌지 않는다. 향후 적용 승인 시 기존 웹 이미지 보존 후 교체하고 실패 시 웹 이미지만 복원한다. DB 복원은 필요하지 않다. 기존 서비스·큐·타이머·기록과 PR #10 pending을 유지한다.
