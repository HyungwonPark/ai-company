/* In-memory contract rehearsal. No transport, credentials, workers or production writes. */
(function(global){
  'use strict';
  const copy=value=>JSON.parse(JSON.stringify(value));
  const canonical=value=>Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(k=>[k,canonical(value[k])])):value;
  async function digest(value){
    const bytes=await global.crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(canonical(value))));
    return [...new Uint8Array(bytes)].map(v=>v.toString(16).padStart(2,'0')).join('');
  }
  function create(data,saved){
    const original=copy(data);
    const fresh=()=>({version:1,projects:original.projects.map(p=>({...p,messages:[],revision:1,spec:null,plan:null,runs:[]})),events:[],receipts:{},decisions:[],sequence:0});
    const state=saved?.version===1&&Array.isArray(saved.projects)&&saved.receipts&&Array.isArray(saved.events)?copy(saved):fresh();
    function project(id){const p=state.projects.find(p=>p.id===id);if(!p)throw Error('프로젝트를 찾을 수 없습니다. 목록에서 다시 선택하세요.');return p;}
    function authorize(context){if(context?.readOnly)throw Error('읽기 권한으로는 저장할 수 없습니다.');if(context?.offline)throw Error('연결이 끊겼습니다. 입력은 보관했으니 연결 후 다시 확인하세요.');}
    const inflight=new Map();
    async function once(kind,id,key,payload,context,perform){
      authorize(context);if(!key||!key.startsWith('fixture-'))throw Error('예시 요청 식별값이 필요합니다.');
      const fingerprint=await digest({kind,id,payload}),prior=state.receipts[key];
      if(prior){if(prior.fingerprint!==fingerprint)throw Error('이전 요청과 내용이 다릅니다. 원래 요청을 먼저 확인하세요.');return copy(prior.result);}
      if(inflight.has(key)) {
        await inflight.get(key);
        return once(kind,id,key,payload,{...context,loseResponse:false},perform);
      }
      const pending=Promise.resolve().then(async()=>{
        const result=await perform();state.receipts[key]={fingerprint,result:copy(result)};
        state.events.push({kind,project_id:id,request_key:key,...copy(result)});
        return result;
      });
      inflight.set(key,pending);
      let result;try {result=await pending;} finally {inflight.delete(key);}
      if(context?.loseResponse){const e=Error('저장 응답을 받지 못했습니다. 같은 요청으로 결과를 확인하세요.');e.code='result_unknown';throw e;}
      return copy(result);
    }
    return {state,project,original,
      createProject(name,goal,key,context){return once('project_created','new',key,{name,goal},context,()=>{
        if(!name.trim()||!goal.trim())throw Error('이름과 목표를 입력하세요.');
        if(name.length>120||goal.length>8000)throw Error('이름은 120자, 목표는 8,000자까지 입력하세요.');
        const id='fixture-created-'+(++state.sequence);
        state.projects.push({id,name:name.trim(),goal:goal.trim(),next:'PM 제안 확인',changed:'방금 · 예시',phase:'proposal',messages:[],revision:1,spec:null,plan:null,runs:[]});
        return {project_id:id,pm_request_id:'fixture-pm-'+state.sequence,executions_created:0};
      });},
      message(id,content,key,context){return once('pm_message',id,key,{content},context,()=>{
        const p=project(id);if(!content.trim())throw Error('PM에게 전할 내용을 입력하세요.');
        p.messages.push({id:'fixture-message-'+(++state.sequence),content:content.trim()});p.revision++;p.phase='proposal';p.plan=null;
        return {message_id:'fixture-message-'+state.sequence,request_revision:p.revision,executions_created:0};
      });},
      saveSpec(id,key,context){return once('execution_spec_registered',id,key,{catalog_id:original.catalog.id,catalog_digest:original.catalog.digest},context,async()=>{
        const p=project(id);if(p.phase!=='proposal')throw Error('PM 제안을 먼저 확인하세요.');
        const version=(p.spec?.version||0)+1;
        const ref={version,digest:await digest({project_id:id,version,catalog:original.catalog}),catalog_id:original.catalog.id,catalog_digest:original.catalog.digest};
        p.spec=ref;p.phase='spec_saved';p.plan=null;
        return {execution_spec:ref,executions_created:0};
      });},
      requestPlan(id,key,context){const p=project(id);const reference=copy(p.spec);return once('fresh_plan_requested',id,key,{execution_spec:reference},context,async()=>{
        if(!p.spec)throw Error('명세를 먼저 저장하세요.');
        const content={summary:'역할 상태를 다섯 분류로 집계하고 독립 검사합니다.',roles:original.roles.map(r=>({key:r.id,name:r.name,responsibility:r.responsibility,goal:r.task,allowed_paths:[r.path],acceptance:['빈 입력·모든 분류·알 수 없는 상태·입력 보존'],depends_on:[]})),completion_criteria:['같은 후보의 격리 검사·원격 CI·독립 검수·Astra 최종 검수']};
        const plan={id:'fixture-plan-'+(++state.sequence),project_id:id,request_revision:p.revision,execution_spec:copy(p.spec),content};
        plan.digest=await digest(plan);p.plan=plan;p.phase='plan_ready';
        return {plan_id:plan.id,plan_digest:plan.digest,execution_spec:plan.execution_spec,executions_created:0};
      });},
      confirm(id,payload,context){return once('plan_confirmed',id,payload.idempotency_key,payload,context,()=>{
        const p=project(id),plan=p.plan;
        if(!plan||plan.id!==payload.plan_id||plan.project_id!==id||plan.digest!==payload.plan_digest||plan.request_revision!==p.revision||p.spec?.digest!==payload.execution_spec_digest||plan.execution_spec.digest!==p.spec.digest)throw Error('계획 또는 실행 범위가 바뀌었습니다. 최신 계획을 다시 확인하세요.');
        if(!payload.reviewed)throw Error('역할과 완료 조건을 확인해 주세요.');
        if(p.runs.some(run=>run.plan_id===plan.id))throw Error('이 계획의 예시 실행이 이미 있습니다. 진행에서 확인하세요.');
        const run={id:'fixture-run-'+(++state.sequence),project_id:id,plan_id:plan.id,plan_digest:plan.digest,execution_spec:copy(p.spec),state:'running',source:'design_fixture'};
        p.runs.push(run);p.phase='running';return {run_id:run.id,execution_spec:copy(p.spec),executions_created:1};
      });},
      decide(id,payload,context){return once('candidate_choice_preview',id,payload.idempotency_key,payload,context,()=>{
        const approval=original.approval;
        if(id!==approval.project_id||payload.approval_id!==approval.id||payload.subject_digest!==approval.subject_digest||payload.artifact_sha!==approval.artifact_sha)throw Error('승인 대상이 달라졌습니다. 원문과 후보를 다시 확인하세요.');
        if(!payload.reviewed||!['approve','reject'].includes(payload.decision))throw Error('영향과 근거를 확인한 뒤 선택하세요.');
        if(payload.decision==='reject'&&!payload.comment?.trim())throw Error('수정이 필요한 이유를 입력하세요.');
        if(state.decisions.some(d=>d.approval_id===approval.id))throw Error('이 후보의 예시 선택을 이미 기록했습니다.');
        const receipt={approval_id:approval.id,subject_digest:approval.subject_digest,artifact_sha:approval.artifact_sha,decision:payload.decision,comment:payload.comment||'',scope:'preview_only'};
        state.decisions.push(receipt);return receipt;
      });}
    };
  }
  const api={create,digest};if(typeof module==='object'&&module.exports)module.exports=api;else global.WorkspacePreviewModel=api;
})(globalThis);
