/* Server snapshots own role state. Transfer facts only describe relationships. */
export function createCollaborationUI({esc,badge,stamp,readable,evidenceLinks,label,documents}) {
  const history=new Map();
  const selections=new Map();
  let observer;
  let activeProject='';
  const kinds={pm:'PM',role:'프로젝트 역할',check:'검사',reviewer:'독립 검수',final:'최종 검수',translator:'한국어 번역',document:'문서 원본'};
  const transfers={specification:'명세 전달',dependency:'산출물 의존',review_request:'검수 요청',revision_return:'수정 반환',result_report:'결과 보고',handoff:'담당자 이관',translation_request:'번역 요청'};
  const statuses={sent:'보냄',received:'수신',started:'착수',completed:'완료',waiting:'대기',failed:'실패'};
  function reset(){history.clear();selections.clear();activeProject='';observer?.disconnect();}
  function disconnect(project){const old=history.get(project);if(old){old.baseline=true;old.fresh.clear();}}
  function ingest(overview,project){
    const data=overview.collaboration;if(!data)return true;
    const cursor=Number(data.cursor);if(!Number.isSafeInteger(cursor)||cursor<0)return true;
    if(activeProject!==project){activeProject=project;history.delete(project);}
    const old=history.get(project);
    if(old&&cursor<old.cursor)return false;
    const unique=new Map();
    for(const item of data.transfers||[]){const existing=unique.get(item.id);if(!existing||Number(item.cursor)>Number(existing.cursor))unique.set(item.id,item);}
    data.transfers=[...unique.values()].sort((a,b)=>Number(b.cursor)-Number(a.cursor)||String(a.id).localeCompare(String(b.id)));
    const fresh=new Map();
    if(old&&!old.baseline){for(const item of data.transfers){if(Number(item.cursor)>old.cursor&&!old.seen.has(item.id))fresh.set(item.id,Date.now()+2000);}}
    history.set(project,{cursor,seen:new Set([...(old?.seen||[]),...unique.keys()]),baseline:false,fresh});
    return true;
  }
  function select(project,kind,id){selections.set(project,{kind,id});}
  function assignment(node){const a=node.assignment||{},req=a.requested||{},obs=a.observed||{};return `<dl class="detail-grid"><div><dt>요청 모델 / effort</dt><dd>${esc(req.model||'미설정')} / ${esc(req.reasoning_effort||'미설정')}</dd></div><div><dt>요청 Ultracode</dt><dd>${req.ultracode_enabled===true?'요청함':req.ultracode_enabled===false?'요청하지 않음':'미설정'}</dd></div><div><dt>실행에서 관측한 모델 / effort</dt><dd>${obs.status==='observed'?`${esc(obs.model||'미확인')} / ${esc(obs.reasoning_effort||'미확인')}`:'적용 미확인'}</dd></div><div><dt>관측 출처 / 범위</dt><dd>${esc(obs.source||'근거 없음')} / ${esc(obs.scope||'미확인')}</dd></div><div><dt>제공자 / 에이전트</dt><dd>${esc(a.provider||'미배정')} / ${esc(a.agent_id||'미배정')}</dd></div><div><dt>세션</dt><dd class="mono">${esc(a.session_id||'미시작')}</dd></div><div><dt>현재 실행 / generation</dt><dd class="mono">${esc(a.execution_id||'미시작')} / ${esc(a.generation??'미확인')}</dd></div><div><dt>하네스 버전</dt><dd>${esc(a.harness_version??'미확인')}</dd></div><div><dt>공유 한도 그룹</dt><dd>${esc(a.quota_group||'미확인')}</dd></div><div><dt>관측한 한도 상태</dt><dd>${esc(label(a.quota?.status||'미확인'))}${a.quota?.reset_at?` · ${stamp(a.quota.reset_at)}`:''}</dd></div></dl><p class="small muted">관측값은 기록된 출처의 범위에 한정됩니다. 요청 모델명만으로 실제 백엔드 모델이나 잔여량을 확인할 수 없습니다.</p>${obs.evidence?`<details><summary>모델 적용 근거</summary><div class="verification">${esc(readable(obs.evidence))}</div></details>`:''}`;}
  function nodeCard(node,overview,selected,linked){
    const task=(overview.tasks||[]).find(t=>t.id===node.current_task_id),a=node.assignment||{};
    const taskId=node.current_task_id;
    return `<article class="${node.kind==='role'?'role ':''}collab-node ${selected?'is-selected':''} ${linked?'is-linked':''}" data-node-id="${esc(node.id)}"><button type="button" class="collab-node-select" id="node-${esc(node.id)}" data-collaboration-node="${esc(node.id)}" aria-pressed="${selected}"><span class="collab-kind">${esc(kinds[node.kind]||node.kind)}</span><span class="collab-node-title">${esc(node.name)}</span>${badge(node.status)}</button><p class="collab-responsibility">${esc(node.responsibility||'책임 범위 미정')}</p><div class="collab-node-body">${node.kind==='document'?'<p class="small muted">시스템 원문 · 모델 역할 아님</p>':`<p class="small"><strong>${esc(a.requested?.model||'요청 모델 미설정')}</strong><br><span class="muted">effort ${esc(a.requested?.reasoning_effort||'미설정')} · ${a.observed?.status==='observed'&&a.observed?.model?'모델 관측 근거 있음':'적용 미확인'}</span></p>`}<div class="task-row"><div class="eyebrow">현재 작업</div><p>${esc(task?.title||taskId||'배정된 작업 없음')}</p></div>${node.wait_reason||node.resume_at?`<div class="role-note">${esc(documents.text(`task:${taskId}:reason`,'reason',node.wait_reason||label(node.status)))}${node.resume_at?`<br>재확인 예약 · ${stamp(node.resume_at)}`:''}</div>`:''}${node.handoffs?.length?`<p class="small muted">담당자 이관 이력 ${node.handoffs.length}건</p>`:''}</div></article>`;
  }
  function detail(data,overview,selection){
    if(!selection)return '<div class="collaboration-detail-empty"><h3>연결된 업무 살펴보기</h3><p>역할 또는 전달 행을 선택하면 담당 설정·작업·근거를 확인할 수 있습니다.</p></div>';
    const nodes=new Map(data.nodes.map(n=>[n.id,n]));
    if(selection.kind==='node'){
      const n=nodes.get(selection.id);if(!n)return '<p>선택한 역할의 현재 기록이 없습니다.</p>';
      const own=(overview.tasks||[]).filter(t=>n.task_ids?.includes(t.id)||t.id===n.current_task_id);
      return `<div class="section-line"><h3>${esc(n.name)}</h3>${badge(n.status)}</div><p class="small">${esc(n.responsibility)}</p>${n.kind==='document'?'<p class="role-note">시스템이 관리하는 원문입니다. 모델 배정·개발·검수 권한을 뜻하지 않습니다. 번역은 별도 산출물로 연결됩니다.</p>':assignment(n)}${n.kind==='translator'?`<p class="role-note">번역 전용 역할 · 코드 변경·검수 판단·실행 승인 권한 없음</p><div class="verification">${esc(readable(overview.translation_summary||{status:'미설정',candidate_model:'gpt-5.6-luna',reason:'도입 후보 / 호출 검증 전'}))}</div>`:''}${own.length?`<h3 class="mt-20">연결된 작업</h3>${own.map(t=>`<section class="collab-task-detail"><strong>${esc(t.title)}</strong><dl class="detail-grid"><div><dt>작업 ID / 현재 단계</dt><dd class="mono">${esc(t.id)} / ${esc(label(t.stage))}</dd></div><div><dt>하네스 / 작업 공간</dt><dd class="mono">${esc(t.harness_version??'미확인')} / ${esc(t.worktree||'미배정')}</dd></div></dl>${documents.meta(`task:${t.id}:reason`)}${t.wait_reason?`<p class="role-note">${esc(documents.text(`task:${t.id}:reason`,'reason',t.wait_reason))}</p>`:''}${t.evidence?.length?`<div class="evidence-links">${evidenceLinks(t.evidence)}</div>`:''}${t.checkpoint?`<details><summary>체크포인트</summary><div class="verification">${esc(readable(t.checkpoint))}</div></details>`:''}</section>`).join('')}`:''}${n.handoffs?.length?`<details class="mt-20" data-persist-key="handoffs:${esc(n.id)}"><summary>담당자 이관 이력 ${n.handoffs.length}건</summary><div class="verification">${esc(n.handoffs.map(readable).join('\n'))}</div></details>`:''}`;
    }
    const t=data.transfers.find(t=>t.id===selection.id);if(!t)return '<p>선택한 전달은 현재 응답에 없습니다. 역할 상태는 최신 서버 기록을 표시합니다.</p>';
    const name=id=>nodes.get(id)?.name||id;
    return `<div class="section-line"><h3>${esc(transfers[t.kind]||t.kind)}</h3><span class="badge">${esc(statuses[t.status]||t.status)}</span></div><p class="transfer-direction">${esc(name(t.from))} <span aria-hidden="true">→</span><span class="sr-only">에서</span> ${esc(name(t.to))}</p><p><strong>${esc(t.title)}</strong></p><p class="report-summary">${esc(t.reason||'이유 미기록')}</p><dl class="detail-grid"><div><dt>보냄 / 착수 / 완료</dt><dd>${stamp(t.created_at)} / ${stamp(t.started_at)} / ${stamp(t.completed_at)}</dd></div><div><dt>다음 담당자</dt><dd>${esc(name(t.to))}</dd></div><div><dt>작업 / 실행</dt><dd class="mono">${esc(t.task_id||'없음')} / ${esc(t.execution_id||t.run_id||'없음')}</dd></div><div><dt>generation / 서버 순서</dt><dd>${esc(t.generation??'미확인')} / ${esc(t.cursor)}</dd></div><div><dt>원본 이벤트</dt><dd class="mono">${esc(readable(t.source))}</dd></div><div><dt>인과 참조</dt><dd class="mono">${esc(readable(t.caused_by))}</dd></div></dl>${t.artifact_refs?.length?`<h3>산출물·검사 근거</h3><div class="evidence-links">${evidenceLinks(t.artifact_refs)}</div>${t.artifact_refs.map(r=>`<p class="mono">${esc(r.sha||r.path||'')}</p>`).join('')}`:'<p class="small muted">연결된 산출물 근거 없음</p>'}<p class="small muted">전달 기록의 시도와 현재 역할의 시도가 다를 수 있습니다. 과거 전달로 현재 담당자를 변경하지 않습니다.</p>`;
  }
  function render(overview,project){
    const data=overview.collaboration;if(!data?.nodes)return '';
    const nodes=data.nodes.filter(n=>n.active!==false),selection=selections.get(project);
    const chosen=selection?.kind==='transfer'?data.transfers.find(t=>t.id===selection.id):null;
    const selectedNode=selection?.kind==='node'?selection.id:null;
    const related=t=>!selection||t.id===chosen?.id||t.from===selectedNode||t.to===selectedNode;
    const linked=new Set(chosen?[chosen.from,chosen.to]:selectedNode?data.transfers.filter(related).flatMap(t=>[t.from,t.to]):[]);
    const name=id=>data.nodes.find(n=>n.id===id)?.name||id;
    const groups=[['프로젝트 매니저',nodes.filter(n=>n.kind==='pm')],['병렬 프로젝트 역할',nodes.filter(n=>n.kind==='role')],['검사·검수·번역 역할',nodes.filter(n=>!['pm','role','document'].includes(n.kind))],['문서 원본',nodes.filter(n=>n.kind==='document')]];
    return `<section class="collaboration" aria-label="역할별 협업"><div class="section-line"><h2>역할과 실제 전달</h2><span>서버 순서 ${esc(data.cursor)} · ${data.source==='fixture'?'모의 응답 기록':'저장된 실행 기록'}</span></div>${data.source==='fixture'?'<p class="small muted collaboration-fixture">네트워크 응답 fixture · 실제 협업 실행을 뜻하지 않습니다.</p>':''}<div class="collaboration-board" data-collaboration-board><svg class="collaboration-wires" aria-hidden="true"></svg>${groups.filter(([,items])=>items.length).map(([title,items])=>`<section class="collab-group"><h3>${title}</h3><div class="collab-node-grid ${title==='프로젝트 매니저'?'pm-grid':''}">${items.map(n=>nodeCard(n,overview,selectedNode===n.id,linked.has(n.id))).join('')}</div></section>`).join('')}</div><p class="small muted diagram-caption">화살표는 실제 발신·수신 역할을 연결합니다. ${data.transfers.length>12?'도식에는 최근 12건, 목록에는 전체 기록을 표시합니다.':''}모바일에서는 아래 방향 행으로 확인하세요.</p><div class="collaboration-bottom"><section aria-label="전달 목록"><div class="section-line"><h2>업무 전달 <span>${data.transfers.length}</span></h2>${selection?'<button type="button" class="quiet" data-collaboration-clear>선택 해제</button>':''}</div><div class="transfer-list">${data.transfers.length?data.transfers.map(t=>`<button type="button" id="transfer-${esc(t.id)}" class="transfer-row ${related(t)?'is-related':'is-unrelated'}" data-collaboration-transfer="${esc(t.id)}" aria-pressed="${selection?.kind==='transfer'&&selection.id===t.id}"><span class="transfer-route"><span>${esc(name(t.from))}</span><span class="transfer-arrow" aria-hidden="true">→</span><span>${esc(name(t.to))}</span></span><span class="transfer-description"><strong>${esc(transfers[t.kind]||t.kind)}</strong><span>${esc(t.title)}</span></span><span class="transfer-status">${esc(statuses[t.status]||t.status)}<time>${stamp(t.created_at)}</time></span></button>`).join(''):'<p class="muted small">저장된 전달이 없습니다. 역할 사이의 의존성을 임의로 만들지 않습니다.</p>'}</div></section><aside class="panel collaboration-detail" aria-label="선택한 협업 상세">${detail(data,overview,selection)}</aside></div></section>`;
  }
  function mount(container,overview,project){
    observer?.disconnect();const board=container.querySelector('[data-collaboration-board]');if(!board)return;
    const data=overview.collaboration,selected=selections.get(project),fresh=history.get(project)?.fresh||new Map();
    const newIds=new Set([...fresh].filter(([,expiry])=>expiry>Date.now()).map(([id])=>id));fresh.clear();
    for(const id of newIds){const row=[...container.querySelectorAll('[data-collaboration-transfer]')].find(e=>e.dataset.collaborationTransfer===id);row?.classList.add('fresh-fact');setTimeout(()=>row?.classList.remove('fresh-fact'),1800);const fact=data.transfers.find(t=>t.id===id);const receiver=[...board.querySelectorAll('[data-node-id]')].find(e=>e.dataset.nodeId===fact?.to);receiver?.classList.add('fresh-fact');setTimeout(()=>receiver?.classList.remove('fresh-fact'),1800);}
    let animateOnce=true,lastGeometry='';
    function draw(){
      const svg=board.querySelector('svg'),rect=board.getBoundingClientRect();svg.setAttribute('viewBox',`0 0 ${rect.width} ${rect.height}`);
      const points=new Map([...board.querySelectorAll('[data-node-id]')].map(el=>[el.dataset.nodeId,el.getBoundingClientRect()]));
      const geometry=JSON.stringify([...points].map(([id,r])=>[id,r.left-rect.left,r.top-rect.top,r.width,r.height]));if(geometry===lastGeometry)return;lastGeometry=geometry;
      const subset=selected?data.transfers.filter(t=>selected.kind==='transfer'?t.id===selected.id:t.from===selected.id||t.to===selected.id):data.transfers.slice(0,12);
      svg.innerHTML='<defs><marker id="collaboration-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" class="wire-arrow"/></marker></defs>';
      for(const t of subset){const a=points.get(t.from),b=points.get(t.to);if(!a||!b||t.from===t.to)continue;let x1=a.left+a.width/2-rect.left,y1=a.bottom-rect.top,x2=b.left+b.width/2-rect.left,y2=b.top-rect.top;if(Math.abs(a.top-b.top)<20){const right=a.left<b.left;x1=(right?a.right:a.left)-rect.left;y1=a.top+a.height/2-rect.top;x2=(right?b.left:b.right)-rect.left;y2=b.top+b.height/2-rect.top;}else if(b.top<a.top){y1=a.top-rect.top;y2=b.bottom-rect.top;}const path=`M ${x1} ${y1} C ${x1} ${(y1+y2)/2}, ${x2} ${(y1+y2)/2}, ${x2} ${y2}`;const edge=document.createElementNS('http://www.w3.org/2000/svg','path');edge.setAttribute('d',path);edge.setAttribute('class','collaboration-wire');edge.setAttribute('marker-end','url(#collaboration-arrow)');edge.dataset.collaborationTransfer=t.id;svg.append(edge);if(animateOnce&&newIds.has(t.id)&&!matchMedia('(prefers-reduced-motion: reduce)').matches){const dot=document.createElementNS(svg.namespaceURI,'circle');dot.setAttribute('r','3');dot.setAttribute('class','transfer-pulse');const motion=document.createElementNS(svg.namespaceURI,'animateMotion');motion.setAttribute('path',path);motion.setAttribute('dur','1.2s');motion.setAttribute('repeatCount','1');motion.setAttribute('fill','freeze');dot.append(motion);svg.append(dot);setTimeout(()=>dot.remove(),1300);}}
      animateOnce=false;
    }
    draw();observer=new ResizeObserver(draw);observer.observe(board);
  }
  return {ingest,render,mount,select,disconnect,reset,clear:project=>selections.delete(project)};
}
