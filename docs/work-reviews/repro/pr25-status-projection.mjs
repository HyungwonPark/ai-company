/* R25-2: read-only rendering reproduction. It does not create runs or decisions. */
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
const HEAD='dbd868b1611ef374e9169c2254590d69d745b2dd';
const source=async name=>process.env.PR25_REVIEW_WEB_DIR
  ?readFile(process.env.PR25_REVIEW_WEB_DIR+'/'+name,'utf8')
  :execFileSync('git',['show',HEAD+':src/ai_company/web/'+name],{encoding:'utf8'});
const load=async name=>import('data:text/javascript;base64,'+Buffer.from(await source(name)).toString('base64'));
const journey=await load('journey-ui.js'),report=await load('report-ui.js');
const ui=report.createReportUI({esc:String,badge:s=>'<b>'+s+'</b>',stamp:String,documents:{text:(id,key,value)=>value}});
const binding={project_id:'p',plan_id:'plan',plan_digest:'digest',run_id:'run'};
const states=['MERGE_READY','RECONCILIATION_REQUIRED','NEEDS_RECONCILIATION','NEEDS_CONTEXT_HANDOFF'];
const snapshot={...binding,id:'snap',nodes:states.map((status,index)=>({...binding,id:'role-'+index,kind:'role',name:'역할 '+index,status}))};
const overview={project:{id:'p'},plans:[],runs:[{id:'run',plan_id:'plan',plan_digest:'digest',state:'blocked'}]};
const before=JSON.stringify({overview,snapshot});
const markup=ui.execution(overview,{snapshot,run:journey.journeyRun(overview,snapshot),status:journey.journeyStatus(overview,snapshot),href:v=>'#'+v,approvals:[]});
const groups=[...markup.matchAll(/<section><h3>([^<]+) <span>(\d+)<\/span><\/h3>(.*?)<\/section>/gs)].map(m=>({title:m[1],count:Number(m[2]),statuses:[...m[3].matchAll(/<b>([^<]*)<\/b>/g)].map(s=>s[1])}));
assert.deepEqual(groups.map(g=>g.count),[0,4,0]);
const rejected=journey.journeyStatus({...overview,runs:[{...overview.runs[0],state:'rejected'}]},snapshot);
assert.equal(rejected.title,'상태 미확인');
assert.equal(JSON.stringify({overview,snapshot}),before);
console.log(JSON.stringify({status:'REPRODUCED',groups,rejected,expected:'Existing completion and operator-required statuses need explicit categories; a recorded rejection is not unknown.',limitations:'Actual renderer/helper execution with synthetic read-only records; no real browser, DB or approval.'},null,2));
