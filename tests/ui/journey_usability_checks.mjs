/* Independent usability contracts: original request facts are never rewritten. */
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const app=await readFile(new URL('../../src/ai_company/web/app.js',import.meta.url),'utf8');
function declaration(name){
 const start=app.indexOf(`function ${name}(`);assert.ok(start>=0,`${name} is present`);
 const next=app.indexOf('\nfunction ',start+1);return app.slice(start,next<0?undefined:next);
}
const source=declaration('isPending')+'\n'+declaration('approvalDisplay')+'\nexport {approvalDisplay};';
const {approvalDisplay}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const now=2_000_000_000_000,checks=[];
for(const raw of ['pending','PENDING','WAITING_APPROVAL'])for(const expiry of [null,now/1000-1,now/1000,now/1000+1,new Date(now-1).toISOString(),new Date(now).toISOString(),new Date(now+1).toISOString()]){
 const input={id:'request',status:raw,expires_at:expiry,subject_digest:'immutable',artifact_sha:'candidate',run_id:'selected'};
 const original=JSON.stringify(input),display=approvalDisplay(input,now);
 const millis=typeof expiry==='number'?expiry*1000:expiry===null?NaN:Date.parse(expiry);
 assert.equal(display.actionable,millis>now,`${raw} at ${expiry}`);
 if(millis<=now)assert.match(display.status,/expired/i,'boundary is expired, not pending');
 assert.equal(JSON.stringify(input),original,'display is read-only');checks.push({raw,expiry,actionable:display.actionable});
}
for(const status of ['approved','rejected','changes_requested','expired','NEW_UNKNOWN_STATE'])for(const expires_at of [now/1000-1,now/1000+1]){
 const input={status,expires_at,subject_digest:'immutable'},original=JSON.stringify(input),display=approvalDisplay(input,now);
 assert.equal(display.actionable,false);assert.equal(display.status,status==='approved'&&expires_at*1000<=now?'expired':status,'matches server expiry projection; recorded decisions remain immutable');assert.equal(JSON.stringify(input),original);checks.push({raw:status,expires_at,actionable:false});
}
console.log(JSON.stringify({status:'PASS',cases:checks.length,scope:'actual product projection; synthetic clocks; original approval status, digest and decisions unchanged',checks}));
// Adjacent callers pass already translated display text to the shared badge.
const labelSource=app.match(/^const labels = .*;$/m)?.[0];assert.ok(labelSource);
const badgeModule=await import('data:text/javascript;base64,'+Buffer.from(labelSource+'\nconst esc=value=>String(value);\n'+declaration('badge')+'\nexport {badge};').toString('base64'));
for(const state of ['가동 신호','처리 중','중지','오류','미확인'])assert.ok(badgeModule.badge(state).includes('>'+state+'<'),'pretranslated worker state '+state+' retained');
assert.match(badgeModule.badge('NEW_UNREGISTERED_STATE'),/상태 미확인/);
// A PM-authored report linked to an old execution remains a PM report even if
// the current-plan aggregate belongs to another run.
const reportSource=await readFile(new URL('../../src/ai_company/web/report-ui.js',import.meta.url),'utf8');
const {createReportUI}=await import('data:text/javascript;base64,'+Buffer.from(reportSource).toString('base64'));
const snapshot={id:'snapshot',project_id:'project',plan_id:'plan',plan_digest:'digest',run_id:'old-run',nodes:[],report_refs:[{id:'old-pm-report',project_id:'project',plan_id:'plan',plan_digest:'digest',run_id:'old-run'}]};
const overview={project:{id:'project'},plans:[],reports:[{id:'old-pm-report',project_id:'project',run_id:'old-run',plan_digest:'digest',source:'pm'}],project_report:{project_id:'project',run_id:'new-run',plan_id:'new-plan',plan_digest:'new-digest',pm_report_ids:[]}};
const before=JSON.stringify(overview),ui=createReportUI({esc:value=>String(value??''),badge:badgeModule.badge,stamp:String,documents:{text:(_id,_field,value)=>value}});
const rendered=ui.execution(overview,{snapshot,run:{id:'old-run',state:'running'},status:{title:'작업 중',next:'검사 대기'},href:view=>'#'+view,approvals:[],reports:overview.reports,now});
assert.match(rendered,/PM 작성 보고 1건/,'old execution PM report source remains truthful');assert.equal(JSON.stringify(overview),before);
console.log(JSON.stringify({status:'PASS',adjacent_boundaries:['pretranslated worker state badges','unknown code remains unknown','old-run PM source independent of current-plan aggregate']}));

for(const key of ['project_id','run_id','plan_digest']){
 const foreign=structuredClone(overview);foreign.reports[0][key]='foreign';
 const html=ui.execution(foreign,{snapshot,run:{id:'old-run'},status:{title:'작업 중',next:'대기'},href:view=>'#'+view,approvals:[],now});
 assert.match(html,/PM 작성 보고 없음/,'foreign report '+key+' rejected');
}
for(const key of ['project_id','plan_id','plan_digest','run_id']){
 const foreign=structuredClone(snapshot);foreign.report_refs[0][key]='foreign';
 const html=ui.execution(overview,{snapshot:foreign,run:{id:'old-run'},status:{title:'작업 중',next:'대기'},href:view=>'#'+view,approvals:[],now});
 assert.match(html,/PM 작성 보고 없음/,'foreign reference '+key+' rejected');
}
console.log(JSON.stringify({status:'PASS',foreign_pm_boundaries:7}));
