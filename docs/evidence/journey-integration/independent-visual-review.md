# 순서형 제품 — 최종 독립 화면·조작 근거 검수

최종 판정: **검수한 제품 화면과 격리 Chrome 조작 근거 범위에서 통과. 새 차단 결함 없음.** 아래 비차단 UX 한계와 사용자 실기기 미확인은 유지한다. 배포·병합·운영 승인 판정이 아니다.

검수자: 제품 구현과 별도 agent. PNG를 `view_image` 원본 해상도로 실제 열고, 제공된 Chrome JSON·검사 코드·Git 소스를 대조했다. 이 검수자가 브라우저나 APK를 직접 조작하지 않았으며 테스트를 재실행하지도 않았다. 운영 접근·제품 수정·보호 해제는 하지 않았다.

## 후보와 근거 고정

- 최종 후보: `6a366e69909b204881cc1d0a547a7b5cd06173ee`. 제품 코드는 `d46136da9108608e7bdce6bdbfb0da35e825126a`와 동일하다. `git diff d46136d 6a366e6 -- src`에 변경이 없다.
- 최종 Journey CI: https://github.com/HyungwonPark/ai-company/actions/runs/35905379886
- 검수 원본: `비공개 검증 자료/6a366e6-evidence`.
- 미리보기 manifest의 CI checkout: `b3d155c1100b5b1386e399e5becaab1b331fb6d9`, `source_dirty=false`. PR head와 CI checkout을 같은 커밋으로 기재하지 않는다.
- manifest 소스 29개의 SHA-256을 후보 `6a366e6`의 Git blob으로 각각 재계산해 **29/29 일치**를 확인했다. fixture·패키징 스크립트·제품 JS/CSS·폰트 등이 포함된다.
- 실제 HTML SHA-256: `f00fd8a37e30f9c90221e0b0b327b2c6e024ff2c544670f4f9dfcffdb98141ff`. 파일 자체 계산값과 manifest가 일치한다.
- 아래 최종 PNG 11개를 직접 열었다. a31의 모바일/데스크톱 그래프·결과·명세와 812의 계획 확인 실패 캡처, d461의 모바일 계획·후보 화면도 앞서 직접 읽고 비교했다.

| 최종 직접 열람 파일 | SHA-256 |
| --- | --- |
| `journey-write-plan-review-light-390.png` | `75088f8bb48b98d4468aeed97d81aa35a365e5ce9cb49ac64adf5d0c6fcbe034` |
| `journey-write-plan-review-black-320.png` | `32e884919b1dd4680968248561cf952c9f14e8b0c2160fdfd69cab6b80b3cc1f` |
| `journey-write-candidate-review-light-320.png` | `69bce3bad4313702689ea14a68845328f31e507f54b567117ac68b28858edadd` |
| `journey-write-candidate-review-black-390.png` | `a6ca31e9d3587e622784b38de6fdd1956d7531a15d887adb79e3fcda2413badd` |
| `journey-write-plan-review-light-1440.png` | `99710f1db3da67b10c643e27336d9c64711e3c97722328a9058f23b11a736348` |
| `journey-write-specification-black-390.png` | `a7d5632663e500d3081c5cccddbdeb265126abe098f9b138032379f1b6015267` |
| `journey-graph-light-390-initial.png` | `d6e9912ce216c740d8e3d1b17c0ac4c6dd9425442cfcef45c743fed84a8c0c4e` |
| `journey-graph-light-1440-initial.png` | `349285fc039f6e497ba3da23f5cceafaf3fdc9f746e959cb2214d3bc0e62baa3` |
| `journey-black-320-result.png` | `9d7c3f40b1d3fe90f1d1dc67e0ee39057a489cca143af92301b406e73d9b4ce1` |
| `graph-baseline/workspace-edges-before-recent-light-390.png` | `2e3bcfaaa5b0b7454ce2d16482d4f9d8caaaa57509f61df6a1d2ddede8a09c0b` |
| `graph-baseline/workspace-edges-after-recent-light-390.png` | `982e14ac785e3666eaeac97d0bbac91fd93a63f21b9ecd8629c849e0f5cd8c89` |

## 화면 판정

1. **계획 확정** — 390 Light·320 Black·1440 Light에서 제목, 계획 요약, 개발/검사 역할, 목표·완료 조건·선행 역할·작업 범위가 구분된다. 한국어 번역/원문 전환과 출처가 남고, 모의 계획이라는 경고와 배포·호스트 변경 승인 분리 안내가 보인다. 이전 812의 본문 크기 문제는 수정됐다. 검사 코드는 실제 `.report-summary`의 computed font size가 16px 이상인지 확인하며 최종 여섯 테마/폭 조합이 통과했다. 모든 부가 메타데이터까지 16px라고 확대 해석하지 않는다.
2. **후보 승인** — 320 Light·390 Black에서 실행 1의 시험 후보, 원문 표시/번역 참조 없음, 후보 digest, 만료 시각, 검토 의견, 취소·승인 기록이 표시된다. 계획 확정 화면과 후보 승인 화면은 구분된다. 표시 문구는 승인 기록 자체가 배포나 호스트 변경을 실행하지 않는다고 설명한다. synthetic 후보와 temporary DB의 테스트이며 실제 PR #10 결정이 아니다.
3. **터치·키보드** — 계획/후보 대화상자의 닫기 `×` 글리프 자체는 작지만 최종 코드의 공통 `matrix`에서 실제 boundingBox 폭·높이 각각 44 CSS px 이상을 assert하고 통과했다(`tests/ui/journey_writes.cjs:30`). PNG에는 원문 보기·취소 등 키보드 초점이 보이며 검사 코드/JSON에 모달 초점·동의 조작이 포함된다. 다른 모든 컨트롤의 터치 크기나 색 대비를 일괄 실측한 검수는 아니다.
4. **실행 명세** — 390 Black에서 범위·저장소·기준 커밋·허용 경로·예산·담당 후보·검사·명세 저장을 읽을 수 있다. 요청 설정과 실제 적용 설정이 구별되고, ‘저장은 실행 승인이 아님’과 유한 비용 상한 시 Codex PM/최종 검수가 막힐 수 있다는 제약이 남는다. 기술 상세는 길지만 범위 안에 분리되어 있다.
5. **진행·그래프** — 390 Light·1440 Light에서 페이지 대상과 그래프 대상이 모두 **실행 2**다. 프로젝트·계획·진행·승인·결과 제목은 짧다. 주요 노드는 초기 100%에서 중심에 놓이고 담당 모델·상태가 읽힌다. 최종 graph JSON의 여섯 테마/폭 측정은 중심 오차 0 CSS px, 역할 텍스트 14/17px이며 화면과 부합한다. 752 기준과 후보의 390 Light 전후 캡처에서 같은 최근 실행을 확인했고, 후보는 중앙 업무 흐름과 바깥 이관/반환선을 더 구별한다. 비교 JSON은 baseline JS와 CSS를 모두 752로 고정했고 최근/이전 실행의 관계 ID를 보존한다.
6. **결과** — 320 Black에서 선택 실행 2, 대기 이유, 역할 상태, 시스템 종합, PM 보고 부재, 원문 보고의 출처가 구분된다. 역할 완료와 전체 검사·검수 통과를 구별한다. 선택한 실행이 완료됐다고 오인시키는 새 화면 불일치는 찾지 못했다.

## 실제 조작 증거 대조

최종 JSON의 browser는 `152.0.7977.82`, sandbox는 `true`다. 다음 범위의 PASS를 검사 코드와 대조했다. 이 기록은 별도 Chrome 프로세스에서 제품 API·임시 SQLite를 실제 사용한 자동 조작 결과이며, PM·번역·후보·실행 사실 일부는 명시적으로 주입한 합성 데이터다.

| 근거 | 확인 범위 |
| --- | --- |
| `journey-workspace-validation.json` | Light/Black × 320/390/1440의 실제 화면 이동, 같은 실행 결과·과거 승인·URL 뒤로가기/새로고침, 미존재/타 프로젝트 대상 거부, 지연 읽기·오프라인·401 시 비공개 뷰 제거. 쓰기는 격리 로그인/비밀번호만 |
| `journey-writes-validation.json` | 실제 프로젝트 생성·명세 등록·PM 요청·계획 확인·후보 결정, 중복 실행 방지, 원문 digest/명세/번역 ID 고정, 오래된 계획 거부, 실제 성공 응답을 보류한 두 탐색 경계, 입력 보존·응답 유실·503·CSRF·Origin·타 프로젝트 거부 |
| 같은 D 검사와 PNG | 명세/계획/후보 화면 3종 × 여섯 테마/폭 = 18개 캡처, 모달 닫기 44px 실측 assertion, 계획 본문 16px assertion, 키보드 초점/확인. 200% 항목은 CSS viewport 재배치 상당 검증이며 실기기 확대 조작 아님 |
| `journey-graph-validation.json` | G1 중앙/읽기 크기, G3 도움말 유지, 노드·모드·테마·실제 갱신·실행 전환, 같은 대상의 상세 펼침/스크롤 보존, 화면 크기 변경 시 초점, 사라진 기록의 안내·닫기 복귀 |
| `graph-baseline/workspace-edges-comparison-validation.json` | 752 JS+CSS와 후보의 Light/Black × 320/390/1440 × 최근/이전 비교. 같은 관계 ID 보존. 직접 시각 대조는 위 표의 390 Light 최근 실행 전후 두 장 |
| `journey-preview-validation.json` | 실제 패키징 UI와 synthetic fetch. 읽기 탐색 및 외부 fetch/쓰기 차단. 실제 API·모델·운영 승인 사용 아님 |
| `journey-service-worker-validation.json` | 격리 localhost에서 v8 두 탭→v9 대기/활성화·API/로그아웃 비캐시·v8 되돌리기. 운영 APK 갱신 아님 |

이 검수는 역할별 실제 모델 실행·Astra 최종 모델 검수·운영 worker·실제 마스터 승인으로 확대하지 않는다. 1px 드래그의 별도 전체 경로 수치 회귀는 이 화면 검수의 독립 재실행 대상이 아니며 해당 별도 검사 기록을 참조해야 한다.

## 중간 실패와 최종 해결 구분

- a31 D는 명세 저장 후 새 PM 요청 경계에서 `1 !== 2`로 중단했다. 812에서 보류된 PM 성공 응답 뒤 탐색 보존이 통과했으며, 최종 6a D에서는 해당 경계와 확정 성공 응답 경계를 모두 통과했다. 중간 실패를 삭제하거나 처음부터 성공한 것으로 보고하지 않는다.
- 812 D는 한국어 계획 본문 16px 검사에서 실제 실패했다. 당시 실패 PNG를 직접 읽었다. 공통 닫기 버튼이 전역 44px를 36px로 덮어쓰는 CSS도 지적했다. 최종 제품의 본문/닫기 규칙과 실제 DOM assertions 및 d461·6a의 완료 결과로 해결을 확인했다.

## 남은 비차단 UX 한계와 미확인

- 페이지와 그래프가 ‘진행’ 제목과 대상 선택기를 각각 보여준다. 동일 실행 번호는 맞지만 모바일에서 그래프 진입까지 스크롤이 필요하며 밀도 개선 여지가 있다.
- 초기 모바일 그래프는 읽을 수 있는 크기를 유지하므로 외곽 이관 이름표 일부와 아래 노드가 첫 viewport 밖에 있다. 전체 보기·이동·목록 접근이 필요하다. 모든 관계가 처음부터 한 화면에 보인다는 판정은 아니다.
- 결과의 역할 요약·시스템 종합·원문 보고는 여전히 길다. 간결한 제목과 구획은 확인했으나 초보 사용자의 실제 이해 속도·선호·실수율은 측정하지 않았다.
- APK 직접 입력·계획 확정, 실제 계정/모델 흐름, 운영 서비스워커 갱신, 물리 휴대폰의 터치·뒤로가기·재실행·확대, 색 대비 전체 감사는 이 검수로 완료 처리하지 않는다.

위 한계는 이번 화면·조작 근거 검수의 새 차단 결함이 아니다. 사용자 직접 확인을 대신하거나 운영 배포 승인을 확장하지 않는다.

공개본에서는 비공개 임시 경로만 자료 이름으로 치환했다. 원문은 서버 비공개 작업 기록에 보존한다. 이 디렉터리에는 최종 대표 JSON·캡처를 선별했고 전체 자료는 연결한 CI artifact에 있다.
