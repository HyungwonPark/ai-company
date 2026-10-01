/* Operator catalog facts and a PM selection stay separate from execution consent. */
export const executionMessage='등록한 실행 명세로 역할과 완료 조건을 다시 제안해 주세요.';
export function currentExecutionProposal(overview) {
  const plan=(overview.plans||[]).filter(item=>item.status==='proposed'&&item.request_revision===overview.project.request_revision).at(-1);
  return plan?.content?.execution_spec_proposal?plan:null;
}
export function createExecutionUI({esc,stamp}) {
  const roleLabel=role=>({pm:'PM',developer:'개발',reviewer:'독립 검수',final:'최종 검수'}[role]||role);
  const list=values=>Array.isArray(values)&&values.length?`<ul>${values.map(value=>`<li>${esc(value)}</li>`).join('')}</ul>`:'미등록';
  function detail(summary, selection=null, names={}) {
    const paths=selection?.allowed_paths||summary.allowed_paths||[];
    const pools={...summary.candidates,...selection?.candidates};
    const rolePools=selection?.role_candidates||summary.role_candidates||{};
    const budget={...summary.budget,...Object.fromEntries(Object.entries(selection?.budget||{}).filter(([,value])=>value!=null))};
    const agents=summary.agents||[];
    const agent=id=>{const found=agents.find(item=>item.agent_id===id);return found?`${id} · ${found.model} · ${found.reasoning_effort||'추론 미등록'}${found.ultracode_enabled===true?' · Ultracode 켜짐 요청':found.ultracode_enabled===false?' · Ultracode 꺼짐 요청':' · Ultracode 미등록'}${found.enabled===false?' · 비활성':''}`:`${id} · 카탈로그에서 확인되지 않음`;};
    const costWarning=budget.max_cost_usd!=null?'유한한 비용 상한을 선택하면 현행 실행기는 Codex 후보를 제외합니다. 지정된 Codex PM·최종 검수는 적격 후보 없음으로 차단됩니다.':'';
    const candidateRows=Object.entries(pools).map(([role,ids])=>`<div><dt>${esc(roleLabel(role))}</dt><dd>${list(Array.isArray(ids)?ids.map(agent):[])}</dd></div>`).join('');
    return `<dl class="detail-grid execution-facts"><div><dt>저장소</dt><dd>${esc(summary.repository||'미확인')}</dd></div><div><dt>기준 브랜치</dt><dd>${esc(summary.base_branch||'미확인')}</dd></div><div><dt>기준 커밋</dt><dd class="mono">${esc(summary.base_sha||'미확인')}</dd></div><div><dt>허용 경로${selection?' · PM 제안':''}</dt><dd class="mono">${list(paths)}</dd></div><div><dt>비용 기준</dt><dd>${budget.max_cost_usd==null?'카탈로그의 금액 기준 없음':`USD ${esc(budget.max_cost_usd)}`}</dd></div><div><dt>시간 기준</dt><dd>${budget.max_runtime_seconds==null?'미등록':`${esc(budget.max_runtime_seconds)}초`}</dd></div><div><dt>실행·수정 횟수</dt><dd>실행 ${esc(budget.max_executions??'미등록')}회 · 수정 ${esc(budget.max_repairs??'미등록')}회</dd></div><div><dt>동시 작업</dt><dd>최대 ${esc(summary.max_parallel??'미등록')}개 · ${summary.mode==='fixture'?'모의 설정':summary.mode==='live'?'실제 실행용 설정':'모드 미확인'}</dd></div></dl><h3>담당 후보</h3><p class="small muted">요청할 모델·추론 설정입니다. 실제 적용과 계정 적격성은 실행 기록에서 확인합니다.</p><dl class="detail-grid execution-facts">${candidateRows}${Object.entries(rolePools).map(([role,ids])=>`<div><dt>역할 ${esc(names[role]||role)}</dt><dd>${list(Array.isArray(ids)?ids.map(agent):[])}</dd></div>`).join('')}</dl><h3>검사</h3><dl class="detail-grid execution-facts">${Object.entries(summary.checks||{}).map(([name,command])=>`<div><dt>${esc(name)}</dt><dd class="mono">${esc(JSON.stringify(command))}</dd></div>`).join('')}</dl><details><summary>원격 CI</summary><pre class="verification">${esc(JSON.stringify(summary.ci||{},null,2))}</pre></details>${[...new Set([...(summary.limitations||[]),...(costWarning?[costWarning]:[])])].map(text=>`<p class="small muted">${esc(text)}</p>`).join('')}`;
  }
  function render(overview,{entries=[],specs=[],error='',loaded=false,connected=true,intent=null,planning=false}={}) {
    const plan=currentExecutionProposal(overview),proposal=intent?.body?.selection||plan?.content.execution_spec_proposal;
    const liveEntry=proposal&&entries.find(item=>item.catalog_id===proposal.catalog_id&&item.catalog_digest===proposal.catalog_digest);
    const savedEntry=intent?.catalog&&proposal&&intent.catalog.catalog_id===proposal?.catalog_id&&intent?.catalog?.catalog_digest===proposal?.catalog_digest?intent.catalog:null;
    const entry=liveEntry||savedEntry;
    const current=specs.find(item=>item.digest===overview.project.execution_spec?.digest)||null;
    const names=intent?{}:Object.fromEntries((plan?.content.roles||(overview.plans||[]).at(-1)?.content?.roles||[]).map(role=>[role.key,role.name]));
    const reference=overview.project.execution_spec,request=(overview.pm_requests||[]).at(-1);
    const needsPlan=current&&request?.execution_spec?.digest!==current.digest;
    const saved=current?`<section aria-label="등록된 실행 명세"><h3>등록된 범위 · ${esc(current.version)}차</h3><p class="small muted">${stamp(current.created_at)} 저장 · 실제 실행 여부는 진행에서 확인합니다.</p>${detail(current.resolved||{},null,names)}<details><summary>식별값</summary><p class="mono">${esc(current.digest)}</p><p>카탈로그 · ${esc(current.catalog_id)}</p><p class="mono">${esc(current.catalog_digest)}</p></details></section>`:reference?'<p class="error-text">현재 등록된 명세를 읽지 못했습니다. 새로고침 후 다시 확인하세요.</p>':'';
    const proposalPanel=proposal?`<section aria-label="${intent?'이전 명세 요청':'PM 실행 제안'}"><h3>${intent?'이전 요청 확인':'PM 제안'}</h3>${intent?'<p class="role-note">이전 저장 요청의 결과가 미확인입니다. 아래는 이전에 전송한 범위입니다. 새 PM 제안이 있어도 먼저 이 요청의 결과를 확인합니다.</p>':''}${entry?detail(entry,proposal,names):`<p class="error-text">참조한 카탈로그 버전을 확인할 수 없습니다.</p><pre class="verification">${esc(JSON.stringify(proposal,null,2))}</pre>`}${intent&&!liveEntry?'<p class="small muted">저장 당시의 범위입니다. 현재 카탈로그와 결과는 서버에서 다시 확인합니다.</p>':''}<p class="role-note">저장할 때 카탈로그 범위와 한도를 검사합니다. 저장 후 새 계획을 받아야 합니다.</p><button type="button" data-action="save-execution-spec" ${!connected||(!intent&&(!liveEntry||!loaded))?'disabled':''}>${intent?'저장 결과 확인':'명세 저장'}</button></section>`:'';
    return `<details class="panel execution-panel" data-persist-key="execution" id="execution"><summary>${planning?'범위':'실행'}</summary><p class="small muted">PM과 정한 범위를 저장합니다. 명세 저장은 계획 확정이나 실행 승인이 아닙니다.</p>${error?`<p class="error-text" role="alert">${esc(error)}</p>`:''}${!loaded&&!error?'<p>실행 범위를 확인하고 있습니다.</p>':''}${!entries.length&&loaded&&!error?'<p class="role-note">현재는 상태 집계 기능과 독립 테스트 두 파일을 사용하는 검증 프로젝트만 실행합니다. 추가 카탈로그가 등록되지 않았습니다.</p>':''}${saved}${proposalPanel}${needsPlan&&!intent?`<div class="execution-next"><p>명세가 저장됐습니다. 이 범위에 맞는 역할과 완료 조건을 새로 제안받으세요.</p><button type="button" data-action="execution-plan-message" ${connected?'':'disabled'}>PM에게 새 계획 요청</button><p class="small muted">버튼을 누르면 PM에게 요청을 전송합니다. 계획은 자동 확정하지 않습니다.</p></div>`:!plan&&!current&&entries.length?'<p>이름과 목표를 PM에게 전달하면 등록된 카탈로그 안에서 실행 범위를 제안합니다.</p>':''}<p id="execution-error" class="error-text" role="alert"></p></details>`;
  }
  function confirmation(plan,specs=[]) {
    if(!plan.execution_spec)return '';
    const spec=specs.find(item=>item.digest===plan.execution_spec.digest&&item.version===plan.execution_spec.version);
    return `<section class="execution-confirmation" aria-label="확정할 실행 범위"><h3>실행 범위 · ${esc(plan.execution_spec.version)}차</h3>${spec?detail(spec.resolved||{},null,Object.fromEntries((plan.content?.roles||[]).map(role=>[role.key,role.name]))):'<p class="error-text">등록된 실행 명세를 읽지 못했습니다. 프로젝트 설정에서 먼저 확인하세요.</p>'}<p class="mono">${esc(plan.execution_spec.digest)}</p></section>`;
  }
  return {render,confirmation};
}
