import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const source=await readFile(new URL('../../src/ai_company/web/app.js',import.meta.url),'utf8').then(text=>text.split('/* Journey projections */')[1].split('/* End journey projections */')[0].replaceAll('function ','export function '));
const {journeyHref,journeyRecords,journeyRun,journeyStatus}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
for(let seed=0;seed<60;seed++){
 const id=part=>part+' 한글 /?#&='+createHash('sha256').update(seed+':'+part).digest('hex');
 const snapshot={project_id:id('project'),plan_id:id('plan'),plan_digest:id('digest'),run_id:id('run'),id:id('snapshot')};
 const approval={id:id('approval'),project_id:snapshot.project_id,run_id:snapshot.run_id,plan_digest:snapshot.plan_digest,subject_digest:id('subject'),artifact_sha:id('candidate')};
 const report={id:id('report'),project_id:snapshot.project_id,run_id:snapshot.run_id,plan_digest:snapshot.plan_digest};
 snapshot.approval_refs=[{...snapshot,...approval}];snapshot.report_refs=[{...snapshot,...report}];
 const run={id:snapshot.run_id,plan_id:snapshot.plan_id,plan_digest:snapshot.plan_digest,state:'waiting'};
 const overview={project:{id:snapshot.project_id},runs:[{...run,id:id('other-run')},run],reports:[{...report,id:id('foreign-report')},report],approvals:[{...approval,id:id('foreign-approval')},approval]};
 const before=JSON.stringify([snapshot,overview]);
 for(const view of ['manager','progress','reports','approvals']){
  const url=journeyHref(view,snapshot.project_id,{runId:snapshot.run_id,snapshotId:snapshot.id,approvalId:approval.id});
  const params=new URLSearchParams(url.split('?')[1]);
  assert.equal(params.get('project'),snapshot.project_id,`seed ${seed} exact project round trip`);
  assert.equal(params.get('run'),snapshot.run_id);assert.equal(params.get('snapshot'),snapshot.id);assert.equal(params.get('approval'),approval.id);
 }
 assert.deepEqual(journeyRecords(overview,snapshot,'report').map(r=>r.id),[report.id]);
 assert.deepEqual(journeyRecords(overview,snapshot,'approval').map(r=>r.id),[approval.id]);
 for(const key of ['project_id','plan_id','plan_digest','run_id']){
  const wrong={...snapshot,[key]:id('wrong-'+key)};
  assert.deepEqual(journeyRecords(overview,wrong,'report'),[],`seed ${seed} ${key} cannot borrow reports`);
  assert.deepEqual(journeyRecords(overview,wrong,'approval'),[],`seed ${seed} ${key} cannot borrow decisions`);
  assert.equal(journeyRun(overview,wrong),null);
 }
 for(const field of ['subject_digest','artifact_sha']){
  const changed={...overview,approvals:[{...approval,[field]:id('replaced-'+field)}]};
  assert.deepEqual(journeyRecords(changed,snapshot,'approval'),[],`seed ${seed} changed ${field} invalidates approval binding`);
 }
 const planned={...snapshot,run_id:null,report_refs:[],approval_refs:[]};
 assert.equal(journeyRun(overview,planned),null);assert.equal(journeyStatus(overview,planned).title,'계획');
 assert.deepEqual(journeyRecords(overview,planned,'report'),[]);
 assert.equal(journeyStatus(overview,snapshot).title,'대기');
 assert.equal(journeyStatus({...overview,runs:[{...run,state:'unknown-new-state'}]},snapshot).title,'상태 미확인');
 assert.equal(JSON.stringify([snapshot,overview]),before,'URL and read projections never mutate source facts');
}
console.log('PASS: 60 deterministic Unicode/URL and project-plan-run-candidate binding cases; planned, waiting, unknown states; input facts unchanged');
