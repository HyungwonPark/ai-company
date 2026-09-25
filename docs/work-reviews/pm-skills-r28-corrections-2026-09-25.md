# PR #28 Work PM 후속 수정 검증

2026-09-25 · PR #28 초안 · 비운영 후보. [Work PM 검수](https://github.com/HyungwonPark/ai-company/blob/a9bb3de46c9083d3bf6572f0c8112d39a62803ba/docs/work-reviews/ai-company-pr28-review-2dcb2ce-2026-09-25.md)의 R28-1~5를 같은 PR에서 수정했다. PR #26·#27의 수용·병합이나 운영 적용을 뜻하지 않는다.

## 최초 실패 → 수정 후 경계

| 항목 | 수정 전 재현 | 수정 후 검사 |
| --- | --- | --- |
| R28-1 콜백 재귀 | ECC와 역할 지침을 함께 켜면 `RecursionError`, 원래 `on_spawn` 0회, 프로세스 시작 receipt 없음 | 두 래퍼가 각각의 이전 콜백을 캡처한다. ECC만·지침만·둘 다·원 콜백 없음의 호출 순서와 receipt를 `tests/test_skill_spawn.py`에서 검사한다. |
| R28-2 수리 질문 | 계획 수리 중 저장한 필수 질문이 새 PM 요청에서 사라져, 무관한 후속 메시지 뒤 계획 확정과 실행 1건이 가능 | 수리 질문을 별도 대화 메시지와 출처 계획·시도로 저장한다. 재시작 뒤 답변 없는 요청은 새 계획/실행 0건이며, 그 질문보다 늦은 동일 프로젝트의 답변과 정확한 출처를 연결해야 새 계획·실행 1건을 만들 수 있다. `test_repair_question_survives_restart_and_new_unanswered_request`. |
| R28-3 출처 가장 | 존재하지 않는 질문 ID와 예전 일반 목표 메시지로 새 필수 결정을 `answered` 처리해 확정/실행 1건 | 질문 출처·시도·표시 시점·답변 메시지를 대조한다. 원래 목표의 결정은 저장된 사용자 메시지의 **정확한 인용**을 `goal_quote`와 `resolution`에 같이 기록해야 한다. 가짜 출처는 계획/실행 0건, 저장 문구 인용만 통과한다. `test_new_question_cannot_claim_nonexistent_source_and_old_goal_message`, `test_original_goal_decision_needs_verbatim_saved_master_evidence`. |
| R28-4 공개 조사 | GitHub 검색 결과 1건을 찾아도 개수만 남고 고정 URL·문서·역할 추천이 없었다 | PM 계획 전 승인된 일반 기술어만 검색한다. 검색 저장소의 커밋·트리에서 `SKILL.md`와 라이선스를 찾아 파일 해시·원문 발췌를 PM 입력에 제공한다. PM이 제안한 후보 ID는 역할의 부족한 역량과 서버 저장 결과에 맞아야 하며, 계획에는 출처·커밋·역할별 결과를 고정한다. 후보는 검토 전이며 프롬프트 전달 0건이다. `test_public_search_documents_reach_pm_and_only_matching_role`, `test_pm_cannot_recommend_public_candidate_for_unrelated_role`. |
| R28-5 시간 상한 | 40ms 예약에 문서 3개를 각각 30ms씩 읽어 약 91ms 사용 | 문서 묶음에 하나의 마감시간을 적용하고, 다음 파일에는 남은 시간만 전달한다. 느린 본문과 헤더를 포함한 HTTP 호출 전체를 제한시간 뒤 자식 프로세스 종료로 막는다. 실패 결과·소비 예산은 저장되며 재시작 후 자동 재조회하지 않는다. `test_multi_file_fetch_has_one_deadline_and_persists_failed_budget`, `test_public_read_stops_slow_trickle_at_cumulative_deadline`, `test_public_lookup_process_is_killed_on_slow_headers`. |

독립 검수에서 추가로 발견한 경계도 같은 후보에서 고쳤다. 원래 `max_fetches=4`를 늘리지 않고 검색 저장소 발견 2회와 문서/라이선스 2회만 허용한다. 설정 URL 조회가 실패하면 남은 예산으로 불완전한 검색을 이어가지 않는다. 공개 후보 ID가 승인된 로컬 지침과 충돌하면 후보를 제외한다. 조사 결과는 역할이 필요한 기술어별로 보관해 한 역할의 문서 미발견·문서 조회 실패를 다른 역할에 전파하지 않는다. 두 검색어까지만 조회한 경우 세 번째 기술어를 요구하는 역할은 `not_searched`로 표시한다. 이 세 표시 경계는 수정 전 실패→수정 후 성공 검사로 남겼다. 자동 수리 최대 2회와 기존 사용량·공유 한도는 바꾸지 않았다.

## 계약과 실제 검증의 구분

새 요청은 PM 지침 `pm-requirements-v4`, 내용 검수 `plan-content-review-v3`로 고정한다. 예전 v3 미확정 계획은 현재 기준으로 다시 요청·검수해야 하며, 이미 확정한 실행은 예전 기록과 digest로 복구한다. 질문·답변·명세·별도 내용 검수·직접 확정·역할 배정 경로는 임시 SQLite와 합성 PM/검수자 응답으로 검증했다. `tests/ui/pm_v2_flow.cjs`는 인증된 격리 API의 **실제 계획 확정 모달**에서 체크박스와 버튼을 눌러 한 실행과 두 역할이 생기는지 확인하도록 고쳤다. 이는 자동 브라우저 조작이며 실제 마스터의 승인이 아니다.

공개 조회는 실제 GitHub 검색 `a11y`에서 [weAAAre/a11y-agents-kit의 고정 문서](https://github.com/weAAAre/a11y-agents-kit/blob/b8a87116e1eb58e98e7a12851114b064834ac4b2/skills/figma-a11y-audit/SKILL.md)와 [LICENSE](https://github.com/weAAAre/a11y-agents-kit/blob/b8a87116e1eb58e98e7a12851114b064834ac4b2/LICENSE)를 읽어 각각 SHA-256 `135b24580dc05683f5e95546815a92cafe59edfc92e0e70cf21e02430e98e3ab`, `54d3e5573391d8ccbca898b4615b574eb0857ac39d920605743ae62dfd77f866`을 기록했다. 검색→문서→역할 매핑 제품 경로는 합성 PM 응답으로 검사했다. 실제 Astra의 추천 품질, 사용자 조작, 운영 효과는 미검증이다. 이 공개 문서는 설치·승인·전달하지 않았다.

로컬 전체 Python 회귀는 최종 제품 수정 `2e89623` 기준 524개(3개 skip), 관련 Python 46개, JS 매니저·역할 지침 계약 검사와 구문 검사가 통과했다. 격리 Chrome은 이 로컬 환경의 `libatk-1.0.so.0` 부재로 시작하지 못했다. 실제 브라우저 통과 여부는 이 PR의 최종 HEAD Journey CI 증적으로 판정한다. 독립 코드 검수는 예산·출처·ID 충돌·마감시간·역할별 조사 상태 문제를 발견해 수정 후 재검수했다. `2e89623`의 역할별 3개 재현, v3/v2 확정 실행의 v4/v3 복구, R27-1과 JS 계약을 다시 확인했고 추가 차단 결함은 찾지 못했다.

## 고정 후보와 화면 증적

제품·검사 커밋 `173034b1e03c1e020f7b57698387128d32dc53b3`, 미리보기 소스 커밋 `b34338517589b3c11206ae8cbc9535ac5d469de4`를 기준으로 했다. 해당 소스의 [Python CI](https://github.com/HyungwonPark/ai-company/actions/runs/36126433903), [Journey CI](https://github.com/HyungwonPark/ai-company/actions/runs/36126433907), [Console UI CI](https://github.com/HyungwonPark/ai-company/actions/runs/36126433902)는 성공했다. Journey의 격리 Chrome에서 인증된 임시 API와 합성 PM·독립 검수 응답을 사용해 질문 대기→답변 중복 방지→계획 수정→별도 검수→**실제 모달 클릭**→실행 1건·역할 작업 2건을 확인했다. 실행 ID `4dbc7eb83a334fd39481c472cdb2f0d2`, 계획 ID `f0f52ea92aae4c5d9141fed754826103`, 계획 digest `29b551e20a8dee4949c762b063540bc99926ac5d59e1cdf3c81107e7544c7f9a`였다. 브라우저 오류·외부 요청 위반은 0건이다. 실제 Astra와 마스터의 조작은 아니다.

[읽기 전용 HTML](../previews/pm-requirements/AI-Company-PM-preview.html)의 SHA-256은 `468366a4e621ca0eaf1d9284cdf6080bc0e05b42222a4dcb40a35584cbbbafc2`, [화면 5장 포함 ZIP](../previews/pm-requirements/AI-Company-PM-preview.zip)은 `8178f34b02fd42db1d644a83d0140b525ef17883317a1ad0a1801456eec05fbd`이다. 이미지는 원격 Journey 산출물의 Light 질문, Black 검토, v2 Light 질문, v2 Black 통과 검토, v2 Light 확정 모달을 그대로 복사했다. ZIP 무결성 검사를 통과했다. HTML은 합성 GET만 응답하며 저장·실제 로그인·모델·승인을 실행하지 않는다.

| 화면 | SHA-256 |
| --- | --- |
| `question-light-390.png` | `a1259412478c6520b49deb1783060ef3e5e8d06510dc7c948730feea3670326c` |
| `review-black-390.png` | `5d43cc3a9124c0dff493da5021ad6b422444a98615e63f718e4ad9c7330abfed` |
| `v2-question-light-390.png` | `14f169d605254d4153f076547a30f6fac5a43575b2446a4112f078ea38a147e9` |
| `v2-review-black-390.png` | `3f48ede6365b37032a64d0b33c98e8dfcc52dd749c7652bf1a51f6d6a361ddff` |
| `v2-confirmation-modal-light-390.png` | `b4a9183d4e2199574aff181cc242ca241ca604f3f43b05ada947e6e519b9ba97` |

## 적용·복구안 — 운영 승인 아님

이 후보는 웹 1개와 PM·조정기 worker 1개의 **향후** 교체 대상이다. 기존 번역 worker·커플 서비스·타이머·큐·PR #10 `pending`은 유지한다. 이 수정 자체는 관리 DB의 새 테이블이나 기존 기록 재작성·마이그레이션을 요구하지 않는다. 이미 쓰는 별도 `skill-research.sqlite`에는 새 조회 사실과 예산 예약이 추가될 수 있다.

적용 전 실제 운영 커밋·이미지 해시, worker 구성·계정 예산·대기 PM 요청과 미확정 계획, DB 상태를 비공개로 대조하고 SQLite 온라인 백업과 이전 웹/worker 이미지를 보관한다. 새 배정을 잠시 멈춘 뒤 단일 worker와 웹을 동일 후보로 교체한다. 로그인·기존 기록·이전 확정 실행 복구·오래된 미확정 계획 차단·새 PM 질문/검수·역할 지침 미전달·단일 worker를 확인한다. 필수 검사 실패 시 **새 v4 요청과 조사 기록이 생기기 전**에만 이전 웹/worker 이미지를 함께 복원한다. 새 기록이 생겼다면 새 실행만 멈추고 DB·큐·승인 원문과 관리/보고 화면을 유지한 채 호환 수정한다. DB를 옛 백업으로 덮어쓰거나 조사 예산을 초기화하지 않는다.

미리보기는 합성 GET 데이터만 포함하며 저장·모델 호출·확정은 차단한다. 운영 배포·병합·PR #10 승인 변경·서명키 작업은 수행하지 않았다.
