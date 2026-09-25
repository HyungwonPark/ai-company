/* Independent R25-2 regression: actual JS projections, immutable synthetic facts. */
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const web=new URL('../../src/ai_company/web/',import.meta.url);
const app=await readFile(new URL('app.js',web),'utf8');
const section=app.split('/* Journey projections */')[1]?.split('/* End journey projections */')[0];
const source=section?section+'\nexport {journeyStatus,roleStatus};':await readFile(new URL('journey-ui.js',web),'utf8');
const load=source=>import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const {journeyStatus,roleStatus}=await load(source),report=await load(await readFile(new URL('report-ui.js',web),'utf8'));
const ui=report.createReportUI({roleStatus,esc:value=>String(value??''),badge:value=>'<b>'+value+'</b>',stamp:value=>String(value),documents:{text:(_id,_field,value)=>value}});
const snapshot={id:'snapshot',project_id:'project',plan_id:'plan',plan_digest:'digest',run_id:'run',nodes:[]};
const run={id:'run',plan_id:'plan',plan_digest:'digest',state:'waiting',mode:'live'};
const overview={project:{id:'project'},runs:[run],plans:[],approvals:[]};
const statuses={done:['MERGE_READY','CONTRIBUTION_READY','DONE','COMPLETE','COMPLETED'],operator:['RECONCILIATION_REQUIRED','NEEDS_RECONCILIATION','NEEDS_CONTEXT_HANDOFF','NO_ELIGIBLE_AGENT','SUPERSEDED','BLOCKED','FAILED','STOPPED'],active:['RUNNING','READY','CHECK_RUNNING','HANDOFF_PENDING'],waiting:['WAITING_QUOTA','WAITING_RETRY','WAITING_CAPACITY','WAITING_DEPENDENCIES','WAITING_CHECKS'],unknown:['NEW_UNREGISTERED_STATE','WAITING_NEW_UNREGISTERED_STATE','',null]};
const groupTitles={done:'완료',active:'진행',waiting:'대기',operator:'확인 필요',unknown:'미확인'};
const nodes=values=>values.map((status,index)=>({...snapshot,id:'role-'+index,kind:'role',name:'역할 '+index,status,wait_reason:'원문 사유'}));
function rendered(values,state='waiting'){
 const selected={...snapshot,nodes:nodes(values)},data={...overview,runs:[{...run,state}]};
 const before=JSON.stringify({selected,data});
 const markup=ui.execution(data,{snapshot:selected,run:data.runs[0],status:journeyStatus(data,selected),href:v=>'#'+v,approvals:[]});
 assert.equal(JSON.stringify({selected,data}),before,'read projection preserves facts');
 return {markup,title:journeyStatus(data,selected).title};
}
const initial=rendered(['MERGE_READY','RECONCILIATION_REQUIRED','NEEDS_RECONCILIATION','NEEDS_CONTEXT_HANDOFF']);
assert.match(initial.markup,/<h3>완료 <span>1<\/span>/,'R25-2 MERGE_READY must not be in progress');
assert.match(initial.markup,/<h3>확인 필요 <span>3<\/span>/,'R25-2 operator states must not be waiting');
assert.equal(initial.title,'확인 필요','waiting run must surface exact-role operator action');
assert.equal(rendered(['CONTRIBUTION_READY'],'rejected').title,'반려됨','recorded rejection is not unknown');
assert.equal(typeof roleStatus,'function');
let cases=0;
for(const [group,values] of Object.entries(statuses))for(const value of values){
 const expected=roleStatus(value);assert.equal(expected.group,group,String(value));assert.ok(expected.label);if(group==='operator'||group==='unknown')assert.ok(expected.next);
 const {markup,title}=rendered([value]);assert.match(markup,new RegExp('<h3>'+groupTitles[group]+' <span>1</span>'));
 assert.match(markup,/원문 상태/,'raw state remains inspectable');
 if(value)assert.ok(markup.includes(value));
 if(group==='operator')assert.equal(title,'확인 필요');
 if(group==='unknown')assert.equal(title,'상태 미확인');
 cases++;
}
assert.equal(rendered(['WAITING_QUOTA']).title,'대기');
assert.equal(rendered(['RUNNING'],'running').title,'작업 중');
const foreign={...snapshot,nodes:[...nodes(['RUNNING']),{...nodes(['NEEDS_RECONCILIATION'])[0],run_id:'other-run'},{...nodes(['NEW_UNREGISTERED_STATE'])[0],plan_digest:'other-digest'}]};
assert.equal(journeyStatus({...overview,runs:[{...run,state:'running'}]},foreign).title,'작업 중','foreign role records do not change selected-run guidance');
// The original backend current_plan aggregate calls every unfinished role
// in_progress. Its display must use the same semantics as the selected run.
for(const roleState of ['NEEDS_RECONCILIATION','NO_ELIGIBLE_AGENT','SUPERSEDED','NEW_UNREGISTERED_STATE']){
 const current={project_id:'project',plan_id:'plan',plan_digest:'digest',run_id:'run',goal:'예시 목표',digest:'report-digest',candidate_verified:false,completed:[],in_progress:[{title:'담당 역할',status:roleState,role_key:'dev',role_index:0}],blockers:[],decisions:[],completion_criteria:[],pm_report_ids:[],next_actions:[{view:'progress',text:'역할별 산출물과 같은 후보의 검사를 이어갑니다.'}]};
 const input={...overview,project_report:current};const before=JSON.stringify(input),html=ui.render(input);
 const group=roleState==='NEW_UNREGISTERED_STATE'?'미확인':'확인 필요';
 assert.match(html,new RegExp('<h3>'+group+' <span>1</span>'),'current-plan uses the same explicit role classification');
 assert.match(html,/<h3>진행 <span>0<\/span>/);
 const next=html.match(/<h3>다음<\/h3>([\s\S]*?)<\/section>/)?.[1];assert.ok(next,'next action section present');
 assert.doesNotMatch(next,/산출물과 같은 후보의 검사를 이어갑니다/,'operator/unknown is not told to keep executing');
 assert.match(html,/역할별 산출물과 같은 후보의 검사를 이어갑니다/,'system original next action retained as evidence');
 assert.match(html,/원문 상태/);assert.equal(JSON.stringify(input),before,'current-plan projection cannot rewrite original report/digest');
}
console.log(JSON.stringify({status:'PASS',cases,checks:['four original states and rejection','backend completion/operator contracts','known running/reserved wait and unknown including WAITING prefix','same-run operator/unknown priority','foreign-run/digest exclusion','raw values and immutable inputs'],scope:'real JS with synthetic facts; no browser, worker, DB or production calls'}));
