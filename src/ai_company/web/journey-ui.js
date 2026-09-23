/* Read-only navigation and projections. A URL selects records; it never authorizes work. */
export function journeyHref(view,projectId,{runId,snapshotId,approvalId,reportId,all=false}={}) {
  if(view==='projects')return '#projects';
  const params=new URLSearchParams({project:projectId});
  if(runId)params.set('run',runId);
  if(snapshotId)params.set('snapshot',snapshotId);
  if(approvalId)params.set('approval',approvalId);
  if(reportId)params.set('report',reportId);
  if(all)params.set('all','1');
  return '#'+view+'?'+params;
}
export function journeyRecords(overview,snapshot,kind,{all=false}={}) {
  const records=overview?.[kind+'s']||[];
  if(all)return records;
  if(!snapshot||snapshot.project_id!==overview?.project?.id)return [];
  const refs=(snapshot[kind+'_refs']||[]).filter(ref=>ref.project_id===snapshot.project_id&&ref.plan_id===snapshot.plan_id&&ref.plan_digest===snapshot.plan_digest&&ref.run_id===snapshot.run_id);
  return records.filter(item=>refs.some(ref=>ref.id===item.id&&(!item.project_id||item.project_id===snapshot.project_id)&&(!item.run_id||item.run_id===snapshot.run_id)&&(!item.plan_digest||item.plan_digest===snapshot.plan_digest)&&(kind!=='approval'||ref.subject_digest===item.subject_digest&&ref.artifact_sha===item.artifact_sha)));
}
export function journeyRun(overview,snapshot) {
  if(!snapshot||snapshot.project_id!==overview?.project?.id||!snapshot.run_id)return null;
  return (overview.runs||[]).find(run=>run.id===snapshot.run_id&&run.plan_id===snapshot.plan_id&&run.plan_digest===snapshot.plan_digest)||null;
}
export function journeyStatus(overview,snapshot) {
  const run=journeyRun(overview,snapshot);
  if(!snapshot)return {title:'대상 미확인',next:'실행 목록에서 대상을 선택하세요.'};
  if(!snapshot.run_id)return {title:'계획',next:'아직 실행되지 않은 계획입니다. 현재 계획에서 제안과 확정 상태를 확인하세요.'};
  if(!run)return {title:'실행 기록',next:'선택한 실행의 역할과 연결된 기록을 확인하세요. 실행 요약은 미확인입니다.'};
  const status=String(run.state||'').toLowerCase();
  if(status==='awaiting_approval')return {title:'승인 대기',next:'이 실행의 후보와 검수 근거를 읽고 승인 요청을 확인하세요.'};
  if(['blocked','failed','stopped','reconciliation_required'].includes(status))return {title:'확인 필요',next:run.wait_reason||run.reason||'차단 원인과 재개 조건을 진행에서 확인하세요.'};
  if(status==='waiting'||status.startsWith('waiting_'))return {title:'대기',next:run.wait_reason||run.reason||'다른 독립 작업은 계속합니다. 재개 조건은 진행에서 확인하세요.'};
  if(run.integration?.task_id&&['running','integrating','reviewing'].includes(status))return {title:'통합 검수',next:'같은 후보의 검사·CI·독립 검수·최종 검수 기록을 기다립니다.'};
  if(['complete','completed','done','merge_ready','fixture_complete'].includes(status))return {title:run.mode==='fixture'?'모의 흐름 종료':'실행 종료',next:'보고와 검수 근거를 확인하세요. 실행 종료는 후보 수용·배포를 뜻하지 않습니다.'};
  if(status==='pending')return {title:'실행 준비',next:'확정된 계획의 작업 배정을 기다립니다.'};
  if(['running','preparing'].includes(status))return {title:'작업 중',next:'역할별 작업과 기다리는 이유를 진행에서 확인하세요.'};
  return {title:'상태 미확인',next:'저장된 실행 상태와 근거를 진행에서 확인하세요.'};
}
