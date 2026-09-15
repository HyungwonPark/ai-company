/* Read-only overview. No inferred completion, translated authority, or write calls. */
const roleNames = {implementation:'개발',developer:'개발',tests:'테스트',test:'테스트',api:'서버 개발',web:'화면 개발',reviewer:'독립 검수',final_reviewer:'최종 검수'};
export function managerSnapshot(overview) {
  const {project={}, plans=[], pm_requests:requests=[], runs=[]} = overview;
  const current = plans.filter(plan => plan.status !== 'stale' && (project.request_revision == null || plan.request_revision === project.request_revision));
  const plan = current.at(-1);
  const request = requests.filter(item => project.request_revision == null || item.request_revision === project.request_revision).at(-1);
  const run = plan ? runs.filter(item => item.plan_id === plan.id && item.plan_digest === plan.digest).at(-1) : undefined;
  const roles = plan?.content?.roles || [];
  return {plan, request, run, roles, independent:roles.filter(role => !role.depends_on?.length).length};
}

export function workspaceIcon(name) {
  const shapes = {
    account:'<circle cx="12" cy="8" r="3"/><path d="M5 21v-2a7 7 0 0 1 14 0v2"/>',
    manager:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    progress:'<path d="M5 4v16M5 7h7m-7 10h7M12 7v10"/><circle cx="17" cy="7" r="3"/><circle cx="17" cy="17" r="3"/>',
    approvals:'<path d="M9 4H5v17h14V4h-4M9 3h6v4H9zM8 14l3 3 5-6"/>',
    project:'<path d="M3 7V4h7l3 3h8v13H3z"/>',
    arrow:'<path d="M5 12h14m-6-6 6 6-6 6"/>',
    plus:'<path d="M12 5v14M5 12h14"/>',
    check:'<path d="m5 12 4 4L19 6"/>',
    developer:'<path d="m8 7-5 5 5 5m8-10 5 5-5 5m-3-12-2 20"/>',
    tests:'<path d="M8 3h8M10 3v6l-5 9a2 2 0 0 0 2 3h10a2 2 0 0 0 2-3l-5-9V3M8 15h8"/>',
  };
  return `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${shapes[name]||shapes.progress}</svg>`;
}

export function createManagerUI({esc,badge,label,stamp,documents,planContent}) {
  // An excerpt is only navigation help. Full source and confirmation remain available.
  const preview = value => String(value||'').split(/\n+/).find(line => /[가-힣]/.test(line))?.trim() || '';
  function render(overview, {connected, composer, history, technical, archive}) {
    const {project={},approvals=[],reports=[],messages=[]} = overview;
    const {plan,request,run,roles,independent}=managerSnapshot(overview);
    const pending=approvals.filter(item=>['pending','PENDING','WAITING_APPROVAL'].includes(item.status));
    const candidate=run?.integration?.candidate_sha||run?.candidate_sha;
    const candidatePending=Boolean(candidate&&pending.some(item=>item.artifact_sha===candidate));
    const href=view=>`#${view}?project=${encodeURIComponent(project.id)}`;
    const canReview=plan?.status==='proposed';
    const received=Boolean(messages.some(message=>message.role==='user')||request||plan);
    const status=candidatePending?'후보 승인 대기':run?label(run.state):canReview?'계획 확인 대기':plan?label(plan.status):request?label(request.state):received?'PM 응답 대기':'목표 입력 전';
    const title=candidatePending?'확인을 기다리는 요청이 있습니다':canReview?'계획을 확인해 주세요':run?'역할별 작업이 연결됐습니다':plan?'실행 준비 상태를 확인해 주세요':request?.state==='blocked'?'PM 요청을 확인해 주세요':received?'PM 제안을 기다리고 있습니다':'어떤 일을 맡길까요?';
    const summary=preview(plan?documents.text(`plan:${plan.id}`,'summary',plan.content?.summary):documents.text(`project:${project.id}`,'goal',project.goal));
    const roleRows=roles.map((role,index)=>{
      const saved=(overview.roles||[]).find(item=>item.plan_id===plan.id&&item.key===role.key);
      const current=run?.roles?.[role.key];
      const node=saved&&(overview.collaboration?.nodes||[]).find(item=>item.id===saved.id);
      const model=node?.assignment?.observed?.status==='observed'?node.assignment.observed.model:null;
      const requested=node?.assignment?.requested?.model||saved?.assigned_model;
      const roleName=preview(documents.text(`plan:${plan.id}`,`role:${index}:name`,role.name))||roleNames[role.key]||`역할 ${index+1}`;
      const roleStatus=current?.status||(run?'IDLE':canReview?'확정 전':'배정 대기');
      return `<li class="manager-agent"><span class="manager-agent-icon">${workspaceIcon(/test/.test(role.key)?'tests':'developer')}</span><div class="manager-agent-copy"><strong>${esc(roleName)}</strong><span>${model?esc(model):requested?`요청 모델 · ${esc(requested)}`:'담당 모델 미배정'}</span></div>${badge(roleStatus)}</li>`;
    }).join('');
    const action=candidatePending?`<a class="manager-primary-link" href="${href('approvals')}">승인 요청 확인 ${workspaceIcon('arrow')}</a>`:canReview?`<button class="primary" data-action="review-plan" data-id="${esc(plan.id)}" ${!connected||!plan.digest||project.source==='fixture'?'disabled':''}>계획 검토·확정 ${workspaceIcon('arrow')}</button>`:run?`<a class="manager-primary-link" href="${href('progress')}">작업 진행 보기 ${workspaceIcon('arrow')}</a>`:!received?`<button class="primary" data-action="focus-message">목표 입력하기 ${workspaceIcon('arrow')}</button>`:`<a class="manager-primary-link" href="${href('reports')}">기존 보고서 보기 ${workspaceIcon('arrow')}</a>`;
    return `<div class="manager-home"><header class="manager-heading"><div><p>매니저</p><h1>작업 한눈에</h1></div><button class="quiet" data-action="focus-message" aria-label="PM에게 새 목표 전달">${workspaceIcon('plus')}<span>목표 전달</span></button></header>
      <article class="manager-focus ${plan?'plan':''}" ${plan?`data-plan-id="${esc(plan.id)}"`:''} aria-label="현재 작업"><div class="manager-focus-label"><span class="manager-status-dot"></span>${esc(status)}</div><h2>${esc(summary||title)}</h2>${summary?`<p class="manager-goal">${esc(title)}</p>`:`<p class="manager-goal">${roles.length?`${roles.length}개 역할 · 완료 조건 ${plan.content.completion_criteria?.length||0}개`:'목표를 남기면 계획부터 확인할 수 있습니다.'}</p>`}
      <ol class="manager-stages" aria-label="계획과 실행 단계">${[['목표 접수',received],[plan?.status==='confirmed'?'계획 확정':'계획 제안',Boolean(plan)],['실행 생성',Boolean(run)]].map(([name,done],index)=>`<li class="${done?'has-record':''}"><span class="manager-stage-mark">${done?workspaceIcon('check'):index+1}</span><span>${name}</span><span class="sr-only"> · ${done?'기록 있음':'기록 없음'}</span></li>`).join('')}</ol>
      <div class="manager-next"><p>${canReview?'확정 전에는 이 계획의 작업을 시작하지 않습니다.':run?'세부 상태와 검수 결과는 진행 화면에서 확인하세요.':received?'응답 대기 중에도 보고서와 승인을 볼 수 있습니다.':'만들고 싶은 결과를 한글로 적어주세요.'}</p>${action}</div>
      ${plan?`<details class="manager-plan-detail" data-persist-key="manager-plan:${esc(plan.id)}"><summary>역할·완료 조건 자세히 보기</summary>${planContent(plan)}${documents.meta(`plan:${plan.id}`)}</details>`:`<details class="manager-plan-detail" data-persist-key="manager-goal"><summary>저장된 목표 보기</summary><div class="message-content">${esc(documents.text(`project:${project.id}`,'goal',project.goal))}</div>${documents.meta(`project:${project.id}`)}</details>`}</article>
      <div class="manager-columns"><section class="manager-team" aria-label="계획의 역할 배치"><header class="manager-section-heading"><h2>담당 에이전트 <span>${roles.length}</span></h2><a href="${href('progress')}">전체 보기 ${workspaceIcon('arrow')}</a></header>${roles.length?`<div class="manager-parallel">${workspaceIcon('progress')}<span>${independent>1?`${independent}개 역할은 서로 기다릴 필요 없이 시작할 수 있습니다.`:'계획에 지정된 순서로 진행합니다.'}</span></div><ul class="manager-agents">${roleRows}</ul>`:'<p class="manager-empty">PM이 계획을 제안하면 담당 역할이 여기에 표시됩니다.</p>'}</section>
      <section class="manager-inbox" aria-label="확인할 항목"><h2>확인할 항목</h2><a href="${href('approvals')}"><span class="manager-inbox-icon">${workspaceIcon('approvals')}</span><span><strong>승인 요청</strong><small>${pending.length?'검토를 기다리는 요청이 있습니다':'기다리는 요청이 없습니다'}</small></span><b>${pending.length}</b>${workspaceIcon('arrow')}</a><a href="${href('reports')}"><span class="manager-inbox-icon">${workspaceIcon('project')}</span><span><strong>작업 보고서</strong><small>검사와 검수 결과</small></span><b>${reports.length}</b>${workspaceIcon('arrow')}</a><p>계획 확정과 후보 승인은 각각 검토합니다.</p></section></div>
      <section class="manager-compose" aria-label="PM에게 전달"><header class="manager-section-heading"><h2>PM에게 전달</h2><span>Astra Ultra PM</span></header>${composer}</section>
      <details class="manager-history" data-persist-key="manager-history"><summary><span>대화 기록</span><span>${messages.length}개</span></summary>${history}</details>
      <details class="manager-history" data-persist-key="manager-technical"><summary><span>계획·설정·실행 기록</span>${workspaceIcon('plus')}</summary>${technical}${archive}</details></div>`;
  }
  return {render};
}
