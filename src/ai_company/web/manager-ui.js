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

export function modelLabel(model, effort) {
  const name={'gpt-6-astra':'Astra','claude-haiku-4-5':'Claude Haiku','claude-haiku-4-5-20251001':'Claude Haiku','claude-opus-5':'Claude Opus'}[model]||model||'설정 확인 중';
  return name+(effort?' · '+({'ultra':'Ultra','xhigh':'매우 높음','high':'높음','medium':'보통','low':'낮음'}[effort]||effort):'');
}

export function createManagerUI({esc,badge,label,stamp,documents,planContent}) {
  const korean = value => /[가-힣]/.test(String(value||'')) ? String(value).trim() : '';
  const preview = value => korean(value).split(/\n+/)[0] || '';
  function render(overview, {connected, composer, history, technical, archive, tab='conversation'}) {
    const {project={},approvals=[],reports=[],messages=[],workers={}} = overview;
    const {plan,request,run}=managerSnapshot(overview);
    // A previous proposal helps discussion; it can never supply the confirm action.
    const displayPlan=plan||overview.plans?.at(-1), roles=displayPlan?.content?.roles||[];
    const independent=roles.filter(role=>!role.depends_on?.length).length;
    const pending=approvals.filter(item=>['pending','PENDING','WAITING_APPROVAL'].includes(item.status));
    const candidate=run?.integration?.candidate_sha||run?.candidate_sha;
    const candidatePending=Boolean(candidate&&pending.some(item=>item.artifact_sha===candidate));
    const href=view=>`#${view}?project=${encodeURIComponent(project.id)}`;
    const canReview=plan?.status==='proposed';
    const received=Boolean(messages.some(message=>message.role==='user')||request||plan);
    const status=candidatePending?'후보 승인 대기':run?label(run.state):canReview?'계획 확인 대기':request?({pending:'PM 대기',running:'계획 작성 중'}[request.state]||label(request.state)):'PM과 시작';
    const summary=preview(plan?documents.text(`plan:${plan.id}`,'summary',plan.content?.summary):documents.text(`project:${project.id}`,'goal',project.goal));
    const config=workers.automation?.configuration;
    const pm=request?.requested_configuration||config?.agents?.find(agent=>agent.roles?.includes('pm'))||overview.readiness?.pm_requested;
    const pmName=pm?.model?modelLabel(pm.model,pm.reasoning_effort):'PM · 설정 확인 중';
    const live=worker=>['busy','idle'].includes(worker?.state);
    const roleRows=roles.map((role,index)=>{
      const saved=(overview.roles||[]).find(item=>item.plan_id===displayPlan.id&&item.key===role.key);
      const current=run?.roles?.[role.key];
      const node=saved&&(overview.collaboration?.nodes||[]).find(item=>item.id===saved.id);
      const observed=node?.assignment?.observed;
      const requested=node?.assignment?.requested;
      const roleName=preview(documents.text(`plan:${displayPlan.id}`,`role:${index}:name`,role.name))||roleNames[role.key]||`역할 ${index+1}`;
      const responsibility=preview(documents.text(`plan:${displayPlan.id}`,`role:${index}:responsibility`,role.responsibility));
      const roleStatus=(run&&saved?.status&&saved.status!=='IDLE'?saved.status:current?.status)||(run?'IDLE':!plan?'조정 중':'확정 전');
      const model=observed?.status==='observed'?`실행 확인 · ${modelLabel(observed.model,observed.reasoning_effort)}`:requested?.model?`요청 · ${modelLabel(requested.model,requested.reasoning_effort)}`:'모델은 실행 시 배정';
      return `<li class="manager-agent"><div class="manager-agent-top"><span class="manager-agent-icon">${workspaceIcon(/test/.test(role.key)?'tests':'developer')}</span><strong>${esc(roleName)}</strong>${badge(roleStatus)}</div><p>${esc(responsibility||'상세 계획에서 책임 범위를 확인하세요.')}</p><div class="manager-agent-bottom"><span>${esc(model)}</span>${!run?`<button class="quiet" data-action="suggest-message" data-suggestion="${esc(roleName)} 역할의 책임과 완료 조건을 함께 조정하고 싶어요." aria-label="${esc(roleName)} 역할 조정">조정 ${workspaceIcon('arrow')}</button>`:''}</div>${node?.wait_reason?`<p class="small muted">${esc(node.wait_reason)}</p>`:''}</li>`;
    }).join('');
    const action=candidatePending?`<a class="manager-primary-link" href="${href('approvals')}">승인 요청 확인 ${workspaceIcon('arrow')}</a>`:canReview?`<button class="primary" data-action="review-plan" data-id="${esc(plan.id)}" ${!connected||!plan.digest||project.source==='fixture'?'disabled':''}>계획 검토·확정 ${workspaceIcon('arrow')}</button>`:run?`<a class="manager-primary-link" href="${href('progress')}">작업 진행 보기 ${workspaceIcon('arrow')}</a>`:`<button data-action="focus-message">${received?'PM과 대화':'대화 시작'} ${workspaceIcon('arrow')}</button>`;
    const conversation=messages.slice(-2).map(message=>{
      const content=documents.text(`message:${message.id}`,'content',message.content), text=korean(content);
      return `<article class="discussion-message ${message.role==='user'?'from-master':'from-pm'}"><header><strong>${message.role==='user'?'나':message.role==='assistant'?'PM':'안내'}</strong><time>${stamp(message.created_at)}</time></header>${text?`<p>${esc(text.length<=260?text:text.slice(0,240)+'…')}</p>`:`<p class="muted">${message.role==='assistant'?(message.status==='blocked'?'PM이 추가 확인을 요청했습니다. 전문에서 원문을 확인하세요.':'계획이 도착했습니다. 팀과 완료 조건을 확인하세요.'):'저장된 원문이 있습니다.'}</p>`}${!text||text.length>260?`<details data-persist-key="discussion:${esc(message.id)}"><summary>전문</summary><div class="discussion-source">${esc(content)}</div>${documents.meta(`message:${message.id}`)}</details>`:''}</article>`;
    }).join('');
    const awaiting=request&&!['completed','stale','blocked'].includes(request.state);
    const scope=config?.allowed_paths||[];
    const pilot=scope.length===2&&scope.includes('src/ai_company/pilot_status.py')&&scope.includes('tests/test_pilot_status.py');
    const connection=`<details class="manager-connections" data-persist-key="connections"><summary>연결 <span>${live(workers.automation)?'PM 가동':'PM 상태 확인'} · ${live(workers.translation)?'번역 가동':'번역 상태 확인'}</span></summary><dl><div><dt>PM</dt><dd>${esc(pmName)} <small>요청 설정 · 실제 적용 근거는 상세에서 확인</small></dd></div><div><dt>번역</dt><dd>${esc(modelLabel(workers.translation?.configuration?.agents?.[0]?.model||overview.translation_summary?.requested_configuration?.model))}<small>번역 역할과 개발 역할의 적격성은 별도입니다.</small></dd></div><div><dt>개발</dt><dd>${config?esc([...new Set((config.agents||[]).filter(agent=>agent.roles?.includes('developer')).map(agent=>modelLabel(agent.model,agent.reasoning_effort)))].join(' / ')||'등록된 후보 없음'):'설정 정보 대기'}<small>등록된 요청 후보 · 실행 가능 여부와 실제 배정은 별도 확인</small></dd></div></dl>${pilot?'<p>현재 실행 범위는 역할 상태 집계의 개발·검사 두 파일입니다. 다른 목표는 실행 범위를 먼저 준비해야 합니다.</p>':scope.length?`<details><summary>허용 범위</summary><ul>${scope.map(path=>`<li class="mono">${esc(path)}</li>`).join('')}</ul></details>`:'<p>실행 허용 범위는 아직 이 화면에 연결되지 않았습니다. 계획 확정 전에 상세에서 확인하세요.</p>'}</details>`;
    return `<div class="manager-home"><header class="manager-heading"><h1>매니저</h1><button class="quiet" data-action="create-project">${workspaceIcon('plus')}<span>새 프로젝트</span></button></header>
      <article class="manager-focus ${plan?'plan':''}" ${plan?`data-plan-id="${esc(plan.id)}"`:''} aria-label="현재 작업"><div class="manager-focus-top"><h2>목표</h2><div class="manager-focus-label"><span class="manager-status-dot"></span>${esc(status)}</div></div><p class="manager-goal">${esc(summary||(roles.length?`${roles.length}개 역할 · 완료 조건 ${displayPlan.content.completion_criteria?.length||0}개`:'만들고 싶은 것을 PM과 함께 구체화하세요.'))}</p>
      <ol class="manager-stages" aria-label="계획과 실행 단계">${[['목표',received],['팀',Boolean(plan)],['시작',Boolean(run)]].map(([name,done],index)=>`<li class="${done?'has-record':''}"><span class="manager-stage-mark">${done?workspaceIcon('check'):index+1}</span><span>${name}</span><span class="sr-only"> · ${done?'기록 있음':'기록 없음'}</span></li>`).join('')}</ol>
      <div class="manager-next"><p>${canReview?'확정 전에는 이 계획의 작업을 시작하지 않습니다.':run?'진행과 검수 결과를 확인하세요.':'역할과 범위는 대화하며 조정할 수 있습니다.'}</p>${action}</div>
      ${displayPlan?`<details class="manager-plan-detail" data-persist-key="manager-plan:${esc(displayPlan.id)}"><summary>계획${!plan?' · 이전 제안':''}</summary>${planContent(displayPlan)}</details>`:`<details class="manager-plan-detail" data-persist-key="manager-goal"><summary>원문</summary><div class="message-content">${esc(project.goal)}</div>${documents.meta(`project:${project.id}`)}</details>`}</article>
      <div class="planning-switch" role="group" aria-label="계획 보기">${[['conversation','대화'],['team','팀']].map(([value,name])=>`<button id="planning-${value}" data-action="planning-tab" data-tab="${value}" aria-pressed="${tab===value}">${name}${value==='team'&&roles.length?` <span>${roles.length}</span>`:''}</button>`).join('')}</div>
      <div class="planning-workspace" data-planning-tab="${esc(tab)}"><section class="manager-compose" aria-label="PM과 대화"><header class="manager-section-heading"><h2>대화</h2><span>${esc(pmName)}</span></header><div class="discussion">${conversation||'<div class="discussion-welcome"><span class="manager-agent-icon">'+workspaceIcon('manager')+'</span><h3>어떤 것을 만들까요?</h3><p>목표를 이야기하면 PM이 필요한 역할과 계획을 제안합니다.</p></div>'}${awaiting?`<div class="discussion-status" role="status">${badge(request.state==='pending'?'WAITING_PM':request.state)}<span>${['waiting_quota','waiting_retry','waiting_capacity'].includes(request.state)?'요청은 저장되어 있습니다. 예약된 재개를 기다립니다.':'PM의 제안을 기다리고 있습니다.'}</span>${request.resume_at?`<time>${stamp(request.resume_at)}</time>`:''}</div>`:request?.state==='blocked'?'<div class="discussion-status"><strong>확인이 필요합니다</strong><span>자동 계획을 준비하지 못했습니다. 상세의 요청 근거를 확인하세요.</span></div>':''}</div><div class="conversation-suggestions" aria-label="대화 예시">${[['역할 추천','이 목표에 필요한 역할과 각 역할의 책임을 추천해주세요.'],['역할 추가','팀에 역할을 추가하고 싶어요. '],['더 단순하게','역할과 계획을 더 단순하게 정리해주세요.']].map(([name,text])=>`<button data-action="suggest-message" data-suggestion="${text}">${name}</button>`).join('')}</div>${composer}</section>
      <section class="manager-team" aria-label="계획의 역할 배치"><header class="manager-section-heading"><h2>팀 <span>${roles.length}</span></h2>${!plan&&displayPlan?'<span>이전 제안 · 조정 중</span>':run?`<a href="${href('progress')}">진행 ${workspaceIcon('arrow')}</a>`:'<span>PM 제안</span>'}</header>${roles.length?`<div class="manager-parallel">${workspaceIcon('progress')}<span>${independent>1?`${independent}개 역할 · 병렬 진행`:'선행 작업 후 진행'}</span></div><ul class="manager-agents">${roleRows}</ul>`:'<div class="team-placeholder"><span>'+workspaceIcon('progress')+'</span><h3>함께 정할 팀</h3><p>PM의 역할 제안이 여기에 모입니다.<br>미리 역할을 작성할 필요가 없어요.</p></div>'}${pilot?'<p class="workspace-scope">현재는 상태 집계 기능의 개발·검사만 실행할 수 있습니다.</p>':''}${connection}</section></div>
      <section class="manager-inbox" aria-label="확인할 항목"><h2>알림</h2><div class="manager-inbox-links"><a href="${href('approvals')}"><span class="manager-inbox-icon">${workspaceIcon('approvals')}</span><span><strong>승인</strong><small>${pending.length?'검토를 기다리는 요청이 있습니다':'기다리는 요청이 없습니다'}</small></span><b>${pending.length}</b>${workspaceIcon('arrow')}</a><a href="${href('reports')}"><span class="manager-inbox-icon">${workspaceIcon('project')}</span><span><strong>보고서</strong><small>검사와 검수 결과</small></span><b>${reports.length}</b>${workspaceIcon('arrow')}</a></div></section>
      <details class="manager-history" data-persist-key="manager-history"><summary><span>기록</span><span>${messages.length}개</span></summary>${history}</details>
      <details class="manager-history" data-persist-key="manager-technical"><summary><span>상세</span>${workspaceIcon('plus')}</summary>${technical}${archive}</details></div>`;
  }
  return {render};
}
