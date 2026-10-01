# 순서형 제품 연결과 G1~G3 구현 계획

기록일: 2026-09-24 KST. 상태: **설계·코드 대조 완료, 제품 구현 전**.

## 1. 확정한 방향과 이번 산출물

기본 동선은 **프로젝트 → 계획 → 진행 → 승인 → 결과**다. [마스터의 배치 결정과 후속 지시](https://github.com/HyungwonPark/ai-company/blob/619beabb3a0e80dae5057bab78af04896d7a557c/docs/work-reviews/journey-layout-decision-2026-09-24.md)를 반영했다. 배치 선택이나 비교 양식 제출을 다시 기다리지 않는다. 선택 이유·사용 기기·초보 사용자 조작 결과는 전달받지 않았으므로 만들어 기록하지 않는다.

첫 구현 구간은 **기존 프로젝트 찾기 → 실행 선택 → 같은 실행의 진행·결과 읽기 → 해당 실행의 승인 요청 찾기**다. PM 대화·명세 저장·계획 확정·후보 결정의 재배치는 그 다음 구간이다. 순서형은 화면 이동 방식이며, 독립 개발·검사 작업을 직렬 실행으로 바꾸지 않는다.

이번 산출물은 현재 파일·데이터·변경점·검증·후속 적용 경계를 연결한 계획이다. 제품 코드, API, DB, 서비스와 기존 시안은 변경하지 않는다. [PR #22](https://github.com/HyungwonPark/ai-company/pull/22)의 한눈형 비교 자료와 F1·F2 수정, [PR #21](https://github.com/HyungwonPark/ai-company/pull/21)의 그래프 검수, [PR #13](https://github.com/HyungwonPark/ai-company/pull/13)의 실행·승인 계약을 보존한다. [PR #23](https://github.com/HyungwonPark/ai-company/pull/23)의 Work 명세를 수정·대체하지 않고 구현 측 계획으로 연결한다.

## 2. 비교 기준과 확인 범위

| 대상 | 고정 기준 | 이번 확인 |
| --- | --- | --- |
| 선택·G1~G3 명세 | Work 문서 `619beabb3a0e80dae5057bab78af04896d7a557c` | 결정문·README·그래프 명세를 읽음 |
| 검수된 시안 게시본 | `4ffd4e702ea2f125569c3f410328a57d3976fc84` | 제품 연결 조사 기준. 시안 소스는 `c8f10ba61551be2e75e139c9538f20b8030846b2` |
| 제품 소스 | 위 `4ffd4e7`의 `src/` | 승인 배포 기록의 `2087eccae3f946bc6d676904462a9b2acabd8521`과 차이 없음 |
| 그래프 JS·CSS | `4ffd4e7` | 그래프 명세의 기준 `f901f7861a0ff08c4fdaab6444211a1d84a20c9a`와 차이 없음 |
| 운영 | [기존 적용 기록](pr21-deployment-2026-09-17.md) | 이번 작업에서는 운영 컨테이너·worker·DB를 다시 점검하지 않음 |

소스 차이 없음은 저장소 비교 결과다. 현재 운영 상태를 새로 확인했다는 뜻이 아니다. 새로운 제품 후보 커밋·이미지는 아직 만들지 않았다.

다음 두 비교로 제품 코드와 시안의 경계를 확인했다.

```bash
git diff --stat 2087eccae3f946bc6d676904462a9b2acabd8521..4ffd4e702ea2f125569c3f410328a57d3976fc84 -- src
git diff --stat f901f7861a0ff08c4fdaab6444211a1d84a20c9a..4ffd4e702ea2f125569c3f410328a57d3976fc84 -- src/ai_company/web/workspace-graph-ui.js src/ai_company/web/workspace-graph.css
```

아래 파일·행 번호는 `4ffd4e7` 기준이다. `src/ai_company/web/workspace-ui.js`와 `workspace-model.js`는 현재 제품에 없다. 실제 모듈은 아래 표를 따른다. `tests/ui/workspace_model.cjs`도 제품 API가 아닌 이전 시안의 모델 검사다.

## 3. 화면과 실제 데이터 연결

| 화면 | 현재 파일·출처 | 재사용 | 필요한 변경·수용 검사 |
| --- | --- | --- | --- |
| 프로젝트 | [app.js](../../src/ai_company/web/app.js) `projectsPage()`(105), [management.py](../../src/ai_company/management.py) `list_projects()`(325). `GET /api/projects`의 목표·최근 시각·`recent_run`·`recent_pm_request`·미만료 `pending_approval_count` | 검색, 최근 프로젝트, 로그인 사용자별 선택 복원, 생성 중복 방지 | 현재 진행 링크에 `recent_run.id` 연결. 미처리 건수에서 프로젝트 승인함 진입. 0건·이전 실행의 요청·없는 프로젝트·늦게 도착한 이전 프로젝트 응답 검증 |
| 계획 | `app.js` `manager()`(177), [manager-ui.js](../../src/ai_company/web/manager-ui.js), [plan-ui.js](../../src/ai_company/web/plan-ui.js), [execution-ui.js](../../src/ai_company/web/execution-ui.js). overview의 `messages/pm_requests/plans/execution_specs/workers` | 자연어 목표, 실제 PM 요청 상태, 기술 제안과 명세 버전, 원문·한국어 읽기, 명시적 확인 후 확정 | 현재 계획과 실행에 고정된 과거 계획을 구별. 대화·저장·새 계획 요청·확정은 별개 행동. 오래된 계획·번역 전환 후 재확인·명세 불일치 거부 검증 |
| 진행 | `app.js` `progress()`(119), [workspace-graph-ui.js](../../src/ai_company/web/workspace-graph-ui.js). `GET /api/projects/{id}/overview`의 `workspace_graph.snapshots` | 업무·역할·실제 전달·대기·이관·요청/관측 모델, 실행별 위치·선택, 갱신 순서 검사 | 순서형의 업무·팀·기록을 같은 snapshot의 읽기 표현으로 구성. `run` URL과 그래프 선택 연결. 독립 역할 동시 진행을 화면 단계로 막지 않음 |
| 승인 | `app.js` `recordItems()`(191), `approvals()`(198), `decide()`(282), [documents-ui.js](../../src/ai_company/web/documents-ui.js). snapshot의 `approval_refs`와 프로젝트 승인 목록 | 요청 만료·pending·대상 digest·후보 SHA·원문/번역·멱등 결정 | 선택 실행의 요청과 프로젝트 전체 요청을 명시적으로 구별. run+approval 링크로 직접 열기. 선택 실행에 요청이 없으면 다른 실행 요청을 대신 표시하지 않음 |
| 결과 | `app.js` `reports()`(188), [report-ui.js](../../src/ai_company/web/report-ui.js), [project_report.py](../../src/ai_company/project_report.py). `reports/documents/project_report`와 snapshot의 `report_refs` | 시스템 집계와 PM 작성물 구별, 실제 보고 원문·한국어 읽기, 보고/검수 근거 | 선택 실행의 상태·보고·검사 근거를 연결. 현재 계획의 종합 보고를 과거 실행에 재사용하지 않음. 개발 완료·통합 검수 중·승인 대기를 구별하고 자료 없음을 성공으로 채우지 않음 |

API 라우팅은 [management_server.py](../../src/ai_company/management_server.py) 296~338행에 있다. 첫 구간은 기존 두 GET 응답으로 구현한다. 새로운 endpoint·실행 엔진·큐·쓰기 API는 필요하지 않다. 프로젝트 목록의 요청 건수와 각 프로젝트 승인함으로 여러 실행의 요청을 발견할 수 있다. 전체 프로젝트의 상세 요청을 한 번에 반환하는 API가 이미 있다고 가정하지 않는다.

### 결과 집계의 현재 한계

`project_report.py:10~29`의 종합 보고는 `scope=current_plan`으로 최신 요청 revision의 계획과 일치하는 마지막 실행을 집계한다. 현재 `reports()`도 `run` 필터가 있으면 이 집계를 숨기고 해당 실행 보고 refs만 보여 준다.

첫 구간의 결과는 선택 실행의 실제 상태·해당 계획·snapshot·보고 refs를 읽는다. 완료·차단·다음 행동은 근거가 있는 범위에서 표시하고, 종합 자료가 없으면 **미확인** 또는 **아직 보고 없음**으로 남긴다. 현재 계획의 목표·PM 요약을 과거 실행의 결론으로 붙이지 않는다. 모든 과거 실행까지 같은 형식의 종합 보고가 필요해지는 후속 구간에서는 기존 overview에 실행별 읽기 집계를 추가하는 안을 별도로 검수한다. 이 확장은 첫 연결의 선행 조건이 아니다.

## 4. 같은 실행을 유지하는 탐색 계약

1. URL의 프로젝트·실행을 선택의 기준으로 삼고, 유효한 서버 snapshot에서 `plan_id/plan_digest`를 얻는다. 보고·승인 상세에는 해당 `report/approval` ID를 함께 유지한다. DOM의 보기용 문구나 시안의 상태를 권한 근거로 사용하지 않는다.
2. 현재 `projectHref()`는 실행을 버리며 `render():215`는 승인·보고 화면에서만 `run`을 보존한다. 프로젝트의 최근 실행 링크, 진행의 실행 선택, 결과 왕복, 승인 바로가기와 뒤로/앞으로 가기를 같은 계약으로 바꾼다. 기존 해시 링크는 호환되게 남기고 새 링크도 실제 `<a href>`로 열 수 있게 한다.
3. 명시적인 `run` 없이 프로젝트를 처음 열면 실제 조회 결과에서 기본 실행을 선택해 URL에 기록한다. 명시된 실행이 없거나 snapshot 조회 범위 밖이면 **해당 실행을 확인할 수 없음**과 실행 목록을 보여 준다. 임의로 최신 실행을 대신 열지 않는다. `run_id=null`인 계획만 있는 상태에서는 개발 실행이 시작됐다고 표시하지 않는다.
4. 최신 계획과 선택 실행의 고정 계획은 서로 다른 상태다. 최신 계획을 여는 행동에는 그 전환을 표시한다. 승인·결과에서 진행으로 돌아오면 같은 실행·선택으로 돌아온다. 새로고침은 URL의 실행을 복원한다. 카메라·상세 펼침은 기존 실행별 메모리 보존을 재사용하며, 브라우저 종료 후까지 영속 보존한다고 약속하지 않는다.
5. 실행에 연결된 요청의 `project/run/approval/candidate/digest`가 일치하는지 확인한다. 다른 실행의 요청은 **프로젝트 전체 요청**으로 전환하여 찾는다. 전환 사실과 대상 실행을 표시한다. 진행의 다음 행동이 후보 검토라면 정확한 run+approval 링크를 사용한다.
6. `requestEpoch`, graph envelope의 `cursor/observed_at/fingerprint`와 프로젝트 검증, 드래그 중 최신 응답 보류를 보존한다. 뒤늦은 다른 프로젝트 응답·오래된 응답이 선택을 덮어쓰지 않는다. 로그인 만료 시 폼·사적 상태·그래프를 정리한다.

상태 조회로 승인·실행을 만들지 않는다. 읽기 동선 검사는 탐색 이후 업무 관련 POST 0건과 임시 DB의 실행·승인·사용량 불변을 확인한다. 시험 로그인 자체의 인증 요청은 별도로 집계한다.

## 5. PM·확정·승인 재배치 시 보존할 계약

| 행동 | 실제 계약 | 보존할 검사 |
| --- | --- | --- |
| 프로젝트 생성 | `app.js` `createProject()`(268)의 생성 의도·멱등 키·응답 불확실성 처리 | 재시도 시 프로젝트 중복 금지. 이름·자연어 목표로 시작 |
| PM 대화 | `POST .../messages`의 `content`, `management.py:40,361` | 대화 전송은 현재 API 차원의 멱등 키가 없음. 실패한 전송을 자동 반복하지 않으며 이미 멱등 보장된 API로 설명하지 않음 |
| 명세 저장 | `GET /api/execution-catalog`, `GET/POST .../execution-specs`; `base_version/selection/idempotency_key` | 새 버전만 추가. stale base·카탈로그 변경·허용 범위 확대 거부. 저장≠계획 확정≠실행 |
| 계획 확정 | `app.js:271~280,305`, `management.py:681~749`; `plan_digest/base_harness_version/idempotency_key` 및 필요한 `execution_spec_digest/displayed_translation` | 최신 목표·대화·하네스·명세·카탈로그와 재계산 digest 일치. 기술 제안만 있는 계획은 확정 불가. 명시적 검토 뒤 한 트랜잭션으로 배정·pending run 생성. 동시 재시도도 한 실행 |
| 후보 결정 | `POST .../approvals/{approval_id}/decisions`; `decision/comment/subject_digest/idempotency_key/displayed_translation` | 정확한 프로젝트·원문 대상·만료·pending·재시도 검증. 승인 기록이 배포·병합 실행을 뜻하지 않음 |
| 한국어 읽기 | `documents-ui.js:6~17`, `plan-ui.js`, `management.py:854~865` | 완료 번역과 원문 digest가 일치할 때만 읽기 텍스트 교체. 번역 전환 시 계획 검토 체크 해제. 번역문으로 실행 필드·digest·권한을 만들지 않음 |
| 인증 | `app.js` `api()`(70), `management_server.py:227~267` | same-origin 세션·Host/Origin·비GET CSRF·비밀번호 변경 요구·401 정리 유지 |

시안의 `docs/previews/task-workspace/preview.js`는 API 없는 sessionStorage 예시다. 자연어 안내·시각적 우선순위·탐색 구조만 참조한다. `state.flow`, 단계 재생, 고정 PM 제안·후보, 로컬 결정, 예시 실행 생성은 제품에 이식하지 않는다. 실제 모델 설정은 요청 값과 관측 근거를 구분하고 관측 자료가 없으면 미확인으로 표시한다.

## 6. 그래프 G1~G3의 구체적인 변경

[G1~G3 원문](https://github.com/HyungwonPark/ai-company/blob/619beabb3a0e80dae5057bab78af04896d7a557c/docs/work-reviews/ai-company-graph-ui-review-spec-2026-09-23.md)을 수용 기준으로 삼는다. 제품 렌더러를 순서형의 팀/관계 보기에 연결하며 업무 목록·관계 목록·실제 이관 이력에 계속 접근할 수 있게 한다. 새 그래프 라이브러리나 모의 CSS 흐름도로 대체하지 않는다.

### G1 — 읽을 수 있는 첫 진입과 전체 보기

- 현재 `view():194`는 `zoom:1`, `pan:{0,0}`, `adjustCamera:false`다. `graph():200~216`, `geometry():280~298`에서 **아직 저장된 화면 상태가 없는 실행의 주요 노드 경계**로 가로 중앙을 맞춘다. 먼 선 끝·이름표는 기본 중앙 계산에서 제외한다. 세로 전체를 억지로 축소하지 않고 PM·첫 역할이 보이게 한다.
- `body():237`의 **맞춤**은 **전체 보기**로 바꾼다. 기존 `fit`은 선까지 포함한 전체를 보려는 명시적 조작으로 보존한다.
- `graphKey()`의 프로젝트·계획 digest·실행 분리, `positionsBySize/camerasBySize`를 재사용한다. 갱신·테마·폭 변경에서 이미 조작한 카메라를 초기 기본값으로 덮어쓰지 않는다.
- 고정 fixture에서 새 상태로 진입해 중심 오차 ≤8 CSS px, 주요 역할명 화면상 크기 ≥14px, 첫 주요 노드 노출을 실제 브라우저로 측정한다. 기존 실행 복귀·700px 경계 왕복·선택 노드 가시성을 별도로 확인한다. 현재 CSS의 17px 선언만으로 실제 표시 합격을 주장하지 않는다.

### G2 — 가까운 이관·반환선과 기존 안정성

- `routeGraphEdges():39~47,112~115`는 self/return/backward를 전체 노드군 오른쪽 외곽으로 우회시킨다. 자기 이관은 역할 가까운 고리, 수정 반환·상향 보고는 관련 두 노드 가까운 경로를 우선한다. 실제 장애물이 막는 경우 기존 회피 탐색을 사용한다.
- `retainedPath():87~107`와 라벨 유지/재배치 `121~159`를 보존한다. 유효한 기존 경로를 비용 순위가 조금 바뀌었다는 이유로 갈아타지 않는다. 장애물로 무효가 된 경우와 구별한다.
- 주 흐름·이관·반환을 선 종류·라벨·선택 강조로 구별한다. 첫 변경에서는 관계를 숨기지 않는다. 이후 필터를 도입한다면 숨겨진 건수와 전체 펼치기를 함께 검수해야 한다.
- 선·라벨·목록 선택이 같은 edge ID와 from/to를 가리키는지 확인한다. 자기 이관도 실제 기록과 상세를 유지한다. 원본 node/edge/이관 기록을 간결한 화면을 이유로 삭제하지 않는다.
- 같은 fixture와 배치에서 길이·꺾임·노드 관통·라벨/화살표 겹침을 전후 기록한다. 선택 가능성과 국소성을 함께 평가하며 모든 교차가 0이라고 약속하지 않는다. 현재 `graph_routing_checks.mjs:42~43`의 전체 외곽 자기 고리 강제 검사는 새 국소성·장애물 회피 요구를 검사하도록 바꾼다.
- 드래그 회귀는 포인터를 먼저 +8px 이동해 드래그를 시작한 **같은 제스처 안에서 +8↔+9px**를 반복한다. 4px 시작 문턱 아래의 무동작을 안정성 검증으로 세지 않는다. 기존 1.02px 이내 경로·라벨 이동, release 후 유지, 장애물 이동 시 재탐색을 보존한다.

기존 제품 함수로 공개 fixture의 snapshot 3개 × PC/모바일 2개 배치, 총 6회 경로 계산을 실행했다. 아래는 그중 최근 실행 `graph-fixture-new-run`의 수정 전 기준이다. 표의 각 경로 `issue`는 `null`이었다. **경로 계산 결과이며 브라우저 조작·가독성 PASS가 아니다.** 길이는 점 사이 맨해튼 거리의 합, 꺾임은 단순화된 점 개수−2다. SVG 모서리 곡선 길이나 축소 후 화면상의 CSS 길이를 측정한 것이 아니다.

| 실제 관계 | PC 길이 / 꺾임 | 모바일 길이 / 꺾임 |
| --- | ---: | ---: |
| 검사 자기 이관 | 985 / 6 | 749 / 6 |
| 개발 자기 이관 | 581 / 10 | 581 / 10 |
| 독립 검수→개발 수정 반환 | 826 / 6 | 767 / 6 |

제품 `workspace-graph-ui.js` SHA-256: `8819bcb7bab374b71ad9037c8a8a0248f764f582ee4efecd432c24689d3643b5`.
공개 `src/ai_company/web/graph-preview/fixture.json` SHA-256: `ac970aba4ccf4abe47bd862da581cdb2b412a0cd7ca6fb98b5d9e13b798b25ba`.

<details>
<summary>경로 계산 재현 방법</summary>

`4ffd4e7` 저장소 루트에서 실행한다. 같은 계산을 관계 ID·양 끝 이름과 함께 출력해 표의 대상을 확인할 수 있다.

```bash
node --input-type=module <<'NODE'
import { readFile } from 'node:fs/promises';
const src = await readFile('src/ai_company/web/workspace-graph-ui.js', 'utf8');
const { graphLayout, routeGraphEdges } = await import('data:text/javascript;base64,' + Buffer.from(src).toString('base64'));
const fixture = JSON.parse(await readFile('src/ai_company/web/graph-preview/fixture.json', 'utf8'));
for (const small of [false, true]) for (const snap of fixture.workspace_graph.snapshots) {
  const layout = graphLayout(snap.nodes, small);
  const result = routeGraphEdges(snap.edges, layout.positions, layout.nodeWidth, layout.nodeHeight);
  for (const [id, route] of result.routes) {
    if (!['self', 'return', 'backward'].includes(route.type)) continue;
    const edge = snap.edges.find(e => e.id === id);
    const length = route.points.slice(1).reduce((sum, p, i) => sum + Math.abs(p.x - route.points[i].x) + Math.abs(p.y - route.points[i].y), 0);
    console.log(JSON.stringify({ small, run: snap.run_id, id,
      from: snap.nodes.find(n => n.id === edge.from).name,
      to: snap.nodes.find(n => n.id === edge.to).name,
      length, bends: route.points.length - 2, issue: route.issue }));
  }
}
NODE
```

</details>

### G3 — 사용법과 선택 상세의 상태 보존

- 현재 `body():237`의 `<details class="rg-help">`는 상태를 저장하지 않는다. **조작 → 사용법**으로 바꾸고, 도움말 펼침을 그래프 인스턴스의 페이지 설정으로 보존한다. 실행별 선택·카메라 상태와 분리한다.
- `capture():247`는 현재 `.rg-detail`만 저장한다. 모든 DOM 교체 전에 사용법·같은 대상 상세의 펼침·스크롤·초점을 수집하고 복원한다. `app.js:218`의 기존 capture, 드래그 중 렌더 보류, 초점·문서 스크롤 복원은 유지한다. 새 target 선택 때의 의도적인 상세 초기화와 같은 target 갱신을 구별한다.
- 코드 대조에서 두 추가 경로를 확인했다. `detail():198`의 `is-open`·닫기 버튼은 실제 `item` 존재 조건이라 선택 대상이 사라지면 모바일 고정 상세 패널과 닫기 버튼이 사라지고 안내가 그래프 아래의 일반 문서 흐름으로 돌아가 기존 시야에서 벗어날 수 있다. 선택 ID를 유지하고 보이는 누락 안내·닫기·안전한 초점 복귀를 제공한다. `ResizeObserver:381~385`는 capture 없이 redraw하므로 직전 상세 상태가 누락될 수 있다. 즉시 폭 변경 경로에 실패 재현 검사를 추가한다. **이번에는 두 경로의 실제 브라우저 재현을 하지 않았다.**
- 사용법 펼침→역할/선 선택→이동→목록/그래프→실제 응답 갱신→테마→폭 변경을 연속 조작한다. 상세 안의 펼침·스크롤·키보드 초점, 모바일 제목·닫기·초점 복귀를 각각 검사한다. 대상이 응답에서 사라져도 다른 대상을 자동 선택하지 않는다.

## 7. 구현 순서와 파일 소유권

| 구간 | 담당 파일 | 구체적인 완료 기준 |
| --- | --- | --- |
| 공통 준비 | 기존 UI 검사·공개 fixture | 기준 SHA·선택 실행 계약·실패 재현 고정. 운영 DB와 분리된 임시 API·DB 사용 |
| A. 순서형 첫 연결 | `app.js`, `manager-ui.js`, `report-ui.js`, `index.html`, `styles.css` | 이름이 짧은 다섯 단계, 프로젝트/실행 탐색, 같은 실행 진행·결과·승인 링크, 뒤로/새로고침, 미존재 대상 안내. 업무 읽기 중 실행·승인 생성 0건 |
| B. 그래프 | `workspace-graph-ui.js`, `workspace-graph.css`와 관련 검사 | G1 → G2 → G3를 동일 담당자가 순차 수정. A와는 URL로 실행을 전달·선택 변경을 알리는 경계를 먼저 합의. 같은 그래프 파일을 병렬 편집하지 않음 |
| C. 첫 통합 검수 | 기존 실제 API UI 검사·새 동선 시나리오 | A/B 통합 후보의 같은 실행 왕복, 6개 테마/폭 조합, 갱신·선택·드래그·키보드. 작성자와 별도 검수자가 실제 화면·조작 확인 |
| D. 쓰기 동선 연결 | `app.js`, `manager-ui.js`, `plan-ui.js`, `execution-ui.js`, `documents-ui.js` | 기존 PM/명세/확정/승인 UI를 순서형에 재배치. 5장의 계약을 유지한 임시 API 통합 검사. PM 답변·모델 실행 결과를 시안 값으로 만들지 않음 |
| E. 후보 고정 | `sw.js`, 관련 검사·공개 검증 문서 | 수정 커밋 CI·캡처·다운로드 해시·독립 검수·적용/복구안. 공개 후에도 APK·운영 적용 여부는 별도 기록 |

A와 B는 계약을 정한 뒤 독립 파일에서 병렬 진행할 수 있다. D는 첫 읽기 동선이 안정된 뒤 진행한다. 기존 PM·승인 기능은 각 구간의 비운영 후보에서도 기존 경로로 접근할 수 있어야 한다. 시안용 별도 상태 엔진·중복 권한 모델을 만들지 않는다. 한눈형 예시는 보존하지만 제품에 두 배치를 영구 구현하는 추가 목표로 삼지 않는다.

## 8. 검증 계획과 근거의 구분

아래는 **앞으로 실행할 검사**다. 검사 파일을 읽은 사실이나 과거 CI를 새 구현의 합격 근거로 사용하지 않는다.

| 대상 | 기존 검사·추가할 시나리오 | 통과 기준 |
| --- | --- | --- |
| 같은 실행 | `tests/test_workspace_graph.py`, `tests/ui/mobile_entry.cjs`, `workspace_graph*.cjs`, `workspace_graph_checks.mjs` | 두 프로젝트·두 실행·계획만 존재·없는 실행·조회 밖 실행에서 URL/선택/보고/승인 혼입 없음. 뒤로·앞으로·새 탭·새로고침 복원, 늦은 응답 거부 |
| 결과·다음 행동 | `tests/test_project_report.py`, `tests/ui/manager_checks.mjs`와 실행별 결과 시나리오 | 이전 성공을 현재 성공으로 사용하지 않음. 개발 완료 뒤 통합 검수 안내로 변경. 과거 실행에 최신 계획 요약 혼입 없음. 프로젝트 전체 미처리 요청 발견 가능 |
| G1/G3 실제 앱 | `tests/ui/workspace_graph.cjs`, `workspace_graph_independent.cjs` | 초기 중앙·실측 글자 크기, 실행별 카메라, 도움말 연속 조작, 즉시 리사이즈, 사라진 대상 상세·초점 검증 |
| G2 계산·조작 | `graph_routing_checks.mjs`, `graph_edge_records.cjs`, `graph_drag_stability.cjs`, `graph_edge_comparison.cjs` | 전후 길이/꺾임/겹침, 관계별 선택, 유효 경로 1px 왕복 유지, 실제 장애물 재탐색. 기존 `0655de2` 회귀 자료와 이번 `4ffd4e7` 대비를 구별 |
| 생성 입력·상태 조합 | 기존 JS의 seeded 생성 검사 확장 | 긴 한국어/영문 이름·빈/누락 값·ID 순서·다른 실행·응답 순서에서도 원본 불변, 대상 연결, 라벨 회피 유지. 고정 회귀 예시는 유지하고 생성 seed를 실패 기록에 보존 |
| PM·권한 | `tests/test_execution_specs.py`, `test_pm_management.py`, `test_management.py`, `tests/ui/execution_specs.cjs`, `plan_reading.cjs` | 저장≠실행, 최신 버전/번역 확인, 동시 재시도 한 실행, 원문 digest·만료·CSRF·로그인 만료 유지 |
| 기존 시안 | `tests/ui/task_workspace*.cjs`와 F1·F2 근거 | 시안 자료를 변경하지 않는 한 기존 검수 보존. 시안 성공을 제품 API 성공으로 대체하지 않음 |
| 한국어·접근성 | Light·Black × 320/390/1440px 실제 브라우저 | 짧은 단계명, 본문 16px·주요 상태 14px·주요 터치 44px 기준, 긴 이름 줄바꿈, 가로 넘침·초점·확대·줄인 동작·실제 클릭/키보드. 기준 일부 충족을 접근성 인증으로 보고하지 않음 |

실제 API 검증은 [run_workspace_graph.py](../../tests/ui/run_workspace_graph.py)의 임시 SQLite·시험 서버 방식을 재사용한다. 격리를 해제해 브라우저 검사를 통과시키지 않는다. 원격 CI는 해당 후보 커밋·workflow·run·attempt·artifact를 연결한다. 실패 후 재실행 성공과 결함 원인 수정은 별도로 기록한다.

시안 검사 / 제품 함수 계산 / 임시 실제 API 브라우저 / 실제 모델·CI 작업 / APK 실기기 / 운영 적용을 분리해 보고한다. APK 설치·웹 CI만으로 전체 흐름을 완료 처리하지 않는다. 초보 사용자 관찰은 후속 개선 근거이며 이미 정해진 순서형 구현의 대기 조건이 아니다.

## 9. 스킬 확인과 이번 적용 내역

아래 6개 `SKILL.md`를 실제로 읽었다. 개인 설치 경로는 `${CODEX_HOME}/skills/`를 사용해 공개 문서에 홈 디렉터리를 노출하지 않는다. 별도 버전이 없는 파일은 읽은 내용의 SHA-256으로 고정했다.

| 스킬·경로 | 파일 SHA-256 | 이번 적용 / 아직 하지 않은 일 |
| --- | --- | --- |
| `ai-company-frontend` — `.agents/skills/ai-company-frontend/SKILL.md` | `551ddd01b5423b2105905903563f29c155b24550d6c26fc64c94fb67c331e601` | 선택된 방향·실제 코드·계약·모바일 검증 분할에 적용. 실제 화면 검수는 구현 후 |
| `ponytail` — `${CODEX_HOME}/skills/ponytail/SKILL.md` | `1316a2f3f95741d2300b116fe0c2d81ce4a9568656ed0a62643f54aaf09957f2` | 기존 모듈·GET API·그래프 재사용, 새 프레임워크·중복 상태·불필요한 endpoint 제외. 코드 변경 없음 |
| `web-design-guidelines` — `${CODEX_HOME}/skills/web-design-guidelines/SKILL.md`, metadata `1.0.0` | `f4647ca866a3accf763777f83e7682954f0187cd6bea7eea0399796652414e8f` | 공식 지침을 다시 읽고 링크·URL 상태·초점·이름·터치 기준에 반영. 전체 UI 감사 PASS 아님 |
| `property-based-testing` — `${CODEX_HOME}/skills/property-based-testing/SKILL.md` | `9448cacb65c6237a7c22dc59602667177ecae405524079ae9ff92240590b995a` | 원본 불변·실행 분리·ID 순서·라벨 안정성처럼 관측 가능한 속성으로 검사 계획 구성. 생성 검사 추가·실행은 아직 안 함 |
| `variant-analysis` — `${CODEX_HOME}/skills/variant-analysis/SKILL.md` | `7b6b78e499bcb88d05c68254ffc91ebc28edabf4e9808adcc40e2628ebb9407e` | G3의 DOM 교체 전 상태 미수집 원인만 영향 UI에서 추적. 도움말·ResizeObserver·누락 대상 상세 조건 확인. 전 저장소 보안 감사나 브라우저 재현으로 확대하지 않음 |
| `gh-fix-ci` — `${CODEX_HOME}/skills/gh-fix-ci/SKILL.md` | `7b326b4a2f0f5f85122144628ec02077e48841e0e0e82efce88b3415bcfb7c26` | 지침 확인. 이번에는 신규 CI 실패를 진단·수정하지 않았으므로 적용 대기 |

웹 지침 참조: [Vercel Web Interface Guidelines 공식 원문](https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/main/command.md), 2026-09-24 확인. 일반 UI 권고를 이유로 계획 검토 체크·승인 대상 확인을 생략하지 않는다.

속성 검사 지침의 생성 참고 문서와 variant-analysis의 원인·검색·분류·보고 참고 문서도 읽었다. 현재 프로젝트에는 Hypothesis/fast-check 의존성이 없고 `graph_routing_checks.mjs`에 seed 80개 생성 검사가 있다. 우선 이를 확장한다. 새 라이브러리 도입이나 축소(shrinking) 가능한 도구 사용을 이미 완료했다고 기록하지 않는다. `app.js` 갱신·기존 클릭 경로는 상세 capture를 수행하므로 모든 상세가 항상 사라진다고 일반화하지 않는다.

스킬 확인은 서버 Codex의 이번 계획 작업에 한정된다. 운영 PM·Claude worker에 스킬을 탑재하거나 실제 모델이 지침을 준수했음을 입증한 것이 아니다.

## 10. 후속 후보의 적용·복구 경계

| 대상 | 예상 영향 | 준비·복구 기준 |
| --- | --- | --- |
| 웹 | HTML/CSS/JS 동선과 그래프. 첫 구간은 기존 API 사용 | 통합 코드 커밋·이미지 digest를 구현/검수 뒤 고정. 현재 이미지·정적 자원 버전과 비교한 별도 적용안 작성 |
| 서비스워커 | 현재 `ai-company-shell-v8`의 shell 캐시 | 새 모듈·캐시 버전·구버전 탭/갱신 시험. 기존 API·인증 데이터 캐시 금지·쓰기 재생 금지 유지 |
| API·DB | 첫 구간 변경 불필요 | 후속 실행별 집계가 필요하면 읽기 계약 확장으로 따로 차이 제시. 이번 계획으로 테이블·마이그레이션을 추가하지 않음 |
| PM·조정기·번역 worker | 첫 구간 교체 불필요 | 모델·권한·예산·pilot 두 파일·큐·타이머·최종 검수 정책 유지 |
| APK | 기존 앱은 `/`에서 웹을 열므로 우선 기존 패키지 유지 | 웹 변경 때문에 새 서명·앱 ID를 만들지 않음. 설치된 APK에서 실제 갱신·로그인·진행·그래프·보고/승인·뒤로/재실행은 사용자 실기기 확인 전 미검증 |

구현 후 로그인/CSRF, 같은 실행 연결, 승인 대상, 필수 그래프 조작이 실패하면 해당 후보의 공개 적용을 중단한다. 이미 적용하는 별도 승인을 받은 단계라면 고정된 이전 웹 이미지로 복구하고 DB·실행·승인 이력은 과거 백업으로 덮어쓰지 않는다. 향후 서버 응답 변경이 있다면 구버전 웹과 호환되는지 적용안에서 먼저 검증한다. 호환성 미확인 상태를 단순 이미지 복구 가능으로 처리하지 않는다.

이번 결정은 새 운영 배포·병합 승인이 아니다. 기존 커플 서비스·worker·타이머·큐·사용량·승인 기록과 PR #10 pending을 변경하지 않는다. ECC 활성화·추가 모델 연결·서명키 작업을 UI 구현에 끼워 넣지 않는다.

## 11. 이번 완료와 남은 확인

- **완료:** 선택·검수 명세 읽기, 현재 파일/API 대조, 기존 소스와 기준 커밋 차이 확인, G1~G3 변경점과 공개 fixture의 경로 계산 6건, 스킬 지침/해시 기록, 구현 구간·검증·후속 적용 경계 작성.
- **문서 검증:** 상대 링크의 실제 파일 존재, 제품/fixture/스킬 해시, 공개 문서의 민감정보 노출 여부를 확인했다. 작성자와 별도 검수자가 결정문·G1~G3·실제 API/소스를 대조했고 차단 결함을 발견하지 않았다. 검수에서 지적한 G3의 누락 안내 표현은 실제 CSS 동작에 맞게 정밀화했다. 이 검수는 문서·코드 대조이며 브라우저 검수가 아니다.
- **다음 개발:** 7장의 첫 읽기 동선과 그래프 구현, 임시 실제 API 브라우저·후보 CI·독립 시각/조작 검수. 이번 계획에서는 구현하거나 통과했다고 보고하지 않는다.
- **별도 사용자 확인:** APK 실제 조작·서비스워커 갱신, 초보 사용자 관찰, 서명키 외부 보관·복원. 결과는 미검증으로 유지하며 독립 개발의 대기 조건으로 삼지 않는다.
- **운영:** 새 적용 후보·이미지가 생기면 변경 범위와 복구안을 구체화해 기존 승인과 대조한다. 현재 승인된 같은 버전의 재배포나 반복 사전 점검을 하지 않는다.

F1·F2의 기존 코드·CI·캡처·ZIP 근거는 [검증 기록](pr22-f1-f2-validation-2026-09-24.md)과 [Work 최종 검수](https://github.com/HyungwonPark/ai-company/blob/29e514371eb3838afe28ada8d240fdf30055e723/docs/work-reviews/ai-company-pr22-review-4ffd4e7-2026-09-24.md)에 남아 있다. 이전 화면 CI의 캡처 실패와 동일 코드 재실행 성공은 원인 수정 완료와 구별한 기존 기록을 유지한다.
