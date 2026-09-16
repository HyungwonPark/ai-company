/* One shared, explicitly synthetic dataset for all three layout alternatives. */
(function(global){
  'use strict';
  const H=character=>character.repeat(64);
  const data={
    version:1,source:'design_fixture',
    projects:[
      {id:'fixture-pilot',name:'역할 상태 확인',goal:'흩어진 역할 상태를 한눈에 확인하고 싶어요. 개발과 검사를 나눠 진행해 주세요.',next:'PM 질문에 답하기',changed:'오늘 14:20',phase:'question'},
      {id:'fixture-notes',name:'읽은 책 정리',goal:'책에서 기억할 문장을 모아 보고 싶어요.',next:'목표 구체화하기',changed:'어제',phase:'empty'}
    ],
    pm:{name:'Astra',model:'gpt-6-astra',effort:'ultra',question:'처음 보는 상태는 어떻게 셀까요?',body:'진행·대기·차단·완료에 해당하지 않는 상태를 ‘알 수 없음’으로 모으는 방법을 제안해요. 이렇게 하면 누락 없이 전체 역할 수를 확인할 수 있습니다.',suggested:'좋아요. 알 수 없음으로 모으고 빈 입력도 확인해 주세요.'},
    roles:[
      {id:'implementation',name:'개발',responsibility:'역할 상태를 집계하는 함수',task:'알 수 없는 상태의 처리 보완',status:'진행',provider:'Codex',model:'gpt-6-astra',effort:'high',observed:null,path:'src/ai_company/pilot_status.py',wait:null,run:'fixture-run-02'},
      {id:'tests',name:'검사',responsibility:'같은 계약의 독립 테스트',task:'빈 입력·모든 분류·입력 보존 검사',status:'대기',provider:'Claude',model:'claude-opus-5',effort:'xhigh',observed:null,path:'tests/test_pilot_status.py',wait:'Claude 공유 계정 한도 회복을 기다립니다. 개발 역할은 계속 진행합니다.',resume:'15:10 예약 재개',run:'fixture-run-02'}
    ],
    transfer:{id:'fixture-transfer-02',from:'개발',to:'검사',title:'집계 함수 초안',path:'src/ai_company/pilot_status.py',sha:'8f42b19a2c153d981af054b2668d490d8e4b62cc',summary:'다섯 분류를 항상 반환하는 초안을 전달했습니다. 검사 역할은 수신했으며 계정 한도 회복 후 확인합니다.',run:'fixture-run-02',status:'수신',time:'14:18'},
    approval:{id:'fixture-approval-01',project_id:'fixture-pilot',run_id:'fixture-run-01',status:'pending',title:'이전 후보를 수용할까요?',action:'검수를 통과한 후보 커밋 수용',impact:'함수와 독립 테스트 두 파일만 후보에 포함합니다. 운영 배포·자동 병합은 포함하지 않습니다.',rollback:'후보를 수용하지 않고 검토 대기로 남길 수 있습니다. 운영 DB나 기존 기록은 되돌리지 않습니다.',cost:'추가 실행 없음',artifact_sha:'41bc027fd0380c6baf7203543a9e0720c10576ab',subject_digest:H('b'),source_digest:H('c'),original:'Accept the reviewed candidate only. Do not deploy or merge. Keep the existing database and approval history unchanged.',translated:'검수한 후보만 수용합니다. 배포·병합은 하지 않습니다. 기존 DB와 승인 이력을 그대로 유지합니다.',evidence:['격리 검사 8개','원격 CI · 같은 후보','독립 검수 · 같은 후보','Astra 최종 검수 · 같은 후보']},
    catalog:{id:'pilot',digest:H('a'),repository:'HyungwonPark/ai-company',branch:'feat/auto-pm-flow',base_sha:'352a31a79223fb77f377dbec578651ac665001f2',paths:['src/ai_company/pilot_status.py','tests/test_pilot_status.py'],budget:{max_cost_usd:null,max_runtime_seconds:1800,max_executions:24,max_repairs:2},parallel:2},
    report:{system:'개발 초안 1건이 전달됐습니다. 검사 역할은 계정 한도로 대기 중입니다. 이전 후보의 수용 여부는 아직 결정되지 않았습니다.',pm:'개발을 계속하고 검사는 예약 시각에 재개합니다. 지금은 알 수 없는 상태의 처리 기준을 정하면 됩니다.'}
  };
  if(typeof module==='object'&&module.exports)module.exports=data;else global.WorkspacePreviewData=data;
})(globalThis);
