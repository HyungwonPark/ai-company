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
console.log(JSON.stringify({status:'PASS',cases,checks:['four original states and rejection','backend completion/operator contracts','known running/reserved wait and unknown including WAITING prefix','same-run operator/unknown priority','foreign-run/digest exclusion','raw values and immutable inputs'],scope:'real JS with synthetic facts; no browser, worker, DB or production calls'}));
