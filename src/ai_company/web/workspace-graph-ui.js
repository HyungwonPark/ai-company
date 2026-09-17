/* Read-only graph. Server records own relationships, state, model observations and authority. */
const kindLabels={pm:'PM',role:'역할',task:'작업',check:'검사',reviewer:'독립 검수',final:'최종 검수',translator:'번역',document:'문서',artifact:'산출물'};
const relationLabels={specification:'명세',dependency:'의존',review_request:'검수 요청',revision_return:'수정 반환',result_report:'결과 보고',handoff:'이관',translation_request:'번역',planned_dependency:'예정 의존',planned_specification:'역할 제안'};
const statusLabels={READY:'준비',AVAILABLE:'확인 가능',CONTRIBUTION_READY:'산출물 준비',MERGE_READY:'검수 완료',COOLDOWN:'한도 대기',RUNNING:'진행',running:'진행',WAITING_QUOTA:'한도 대기',waiting_quota:'한도 대기',WAITING_RETRY:'재시도 대기',WAITING_DEPENDENCY:'선행 작업 대기',WAITING_DEPENDENCIES:'선행 작업 대기',WAITING_CAPACITY:'담당 대기',pending:'준비',planned:'예정',PLANNED:'예정',IDLE:'준비',completed:'완료',COMPLETE:'완료',DONE:'완료',APPROVED:'완료',BLOCKED:'차단',blocked:'차단',FAILED:'실패',received:'수신',sent:'보냄',started:'착수',awaiting_approval:'승인 대기'};
const clamp=(v,min,max)=>Math.max(min,Math.min(max,v));
const shortModel=value=>({'gpt-6-astra':'Astra','claude-opus-5':'Claude Opus','claude-haiku-4-5':'Claude Haiku'}[value]||value||'미배정');
const planned=item=>item.phase==='planned'||item.mode==='planned'||item.source==='planned'||item.status==='planned';
export function graphLayout(nodes,small=false){
  const width=small?132:206,height=156,gap=small?20:64;
  const lanes={pm:0,role:1,task:1,check:2,reviewer:3,final:4,translator:5,document:5,artifact:5};
  const groups=new Map();for(const node of nodes){const lane=lanes[node.kind]??1;if(!groups.has(lane))groups.set(lane,[]);groups.get(lane).push(node);}
  const positions={};let y=28;
  for(const [,items] of [...groups].sort((a,b)=>a[0]-b[0])){
    items.sort((a,b)=>a.id.localeCompare(b.id));
    for(let i=0;i<items.length;i++){const col=i%2,row=Math.floor(i/2);positions[items[i].id]={x:items.length===1?(width+gap)/2:col*(width+gap),y:y+row*(height+100)};}
    y+=Math.ceil(items.length/2)*(height+100);
  }
  return {positions,nodeWidth:width,nodeHeight:height,width:width*2+gap,height:Math.max(250,y)};
}
export function placeGraphNodes(nodes,layout,retained){
 const positions=structuredClone(retained),occupied=nodes.filter(n=>positions[n.id]).map(n=>positions[n.id]);
 for(const node of nodes){
  if(positions[node.id])continue;
  const candidate={...layout.positions[node.id]};
  while(occupied.some(p=>Math.abs(p.x-candidate.x)<layout.nodeWidth+8&&Math.abs(p.y-candidate.y)<layout.nodeHeight+8))candidate.y+=layout.nodeHeight+100;
  positions[node.id]=candidate;occupied.push(candidate);
 }
 return positions;
}
export function edgeGeometry(edge,positions,width,height,index=0){
 // Ports describe the relationship, not the momentary vertical order while dragging.
 const from=positions[edge.from],to=positions[edge.to],reverse=edge.kind==='revision_return';
 const x1=from.x+width/2,y1=from.y+(reverse?0:height),x2=to.x+width/2,y2=to.y+(reverse?height:0);
 const bend=Math.max(60,Math.abs(y2-y1)/2);
 return {path:reverse?`M ${x1} ${y1} C ${Math.max(x1,x2)+90} ${y1-60}, ${Math.max(x1,x2)+90} ${y2+60}, ${x2} ${y2}`:`M ${x1} ${y1} C ${x1} ${y1+bend}, ${x2} ${y2-bend}, ${x2} ${y2}`,x:(x1+x2)/2+9,y:(y1+y2)/2-8+index%2*16};
}
export function graphKey(snapshot){return [snapshot.project_id,snapshot.plan_id,snapshot.plan_digest,snapshot.run_id||'planned',snapshot.id].join(':');}
export function validateGraphEnvelope(envelope,project){
 if(!(envelope?.schema_version===1&&envelope.project_id===project&&Number.isSafeInteger(envelope.cursor)&&envelope.cursor>=0&&Array.isArray(envelope.snapshots)))return false;
 return envelope.snapshots.every(s=>{
  if(!(s.project_id===project&&typeof s.id==='string'&&Array.isArray(s.nodes)&&Array.isArray(s.edges)))return false;
  const bound=x=>['project_id','plan_id','plan_digest','run_id'].every(key=>x[key]===s[key]);
  return s.nodes.every(n=>typeof n.id==='string'&&bound(n))&&s.edges.every(e=>typeof e.id==='string'&&typeof e.from==='string'&&typeof e.to==='string'&&bound(e));
 });
}
export function createWorkspaceGraph({esc,diagramLinks=false,label=v=>statusLabels[v]||v,stamp=v=>v?new Date(v*1000).toLocaleString('ko-KR'):'미기록',onInteractionEnd=()=>{}}){
 const projects=new Map(),views=new Map();let activeProject='',resizeObserver,disposeMount=()=>{},interaction=null,pendingEnvelope=null;
 const status=v=>statusLabels[v]||label(v)||'미기록';
 const button=(text,action,attrs='')=>`<button type="button" data-rg-action="${action}" ${attrs}>${text}</button>`;
 function ingest(overview,project){
  const raw=overview.workspace_graph;if(!raw)return true;const envelope=structuredClone(raw);
  if(!validateGraphEnvelope(envelope,project))return false;
  for(const s of envelope.snapshots){s.nodes=[...new Map(s.nodes.map(n=>[n.id,n])).values()];s.edges=[...new Map(s.edges.map(e=>[e.id,e])).values()];}
  const prior=projects.get(project),latest=pendingEnvelope?.project===project?pendingEnvelope.data:prior?.data;
  if(latest&&(envelope.cursor<latest.cursor||envelope.cursor===latest.cursor&&envelope.observed_at<latest.observed_at))return false;
  // Keep the active gesture's snapshot stable; only the latest valid response is queued.
  if(interaction?.project===project){pendingEnvelope={project,data:envelope};return true;}
  const oldEdges=new Set((prior?.data.snapshots||[]).flatMap(s=>s.edges.map(e=>e.id)));
  const fresh=prior&&!prior.disconnected?envelope.snapshots.flatMap(s=>s.edges.filter(e=>!oldEdges.has(e.id)&&!planned(e)).map(e=>e.id)):[];
  const missing=Boolean(prior?.selected&&!envelope.snapshots.some(s=>s.id===prior.selected));
  projects.set(project,{data:envelope,selected:missing?envelope.default_snapshot_id:prior?.selected||envelope.default_snapshot_id||envelope.snapshots[0]?.id,missingSelection:missing,disconnected:false,fresh:new Set(fresh)});
  return true;
 }
 function scope(project){const p=projects.get(project);if(!p)return null;return p.data.snapshots.find(s=>s.id===p.selected)||p.data.snapshots[0];}
 function view(snapshot){const k=graphKey(snapshot);if(!views.has(k))views.set(k,{mode:'graph',selection:null,zoom:1,pan:{x:0,y:0},positions:{},positionsBySize:new Map(),camerasBySize:new Map(),adjustCamera:false,small:null,panMode:false,seen:new Set(),focus:null,detailScroll:0,detailOpen:[]});return views.get(k);}
 function refs(snapshot){return `<details class="rg-provenance"><summary>기록 기준</summary><dl><dt>프로젝트</dt><dd><code>${esc(snapshot.project_id)}</code></dd><dt>계획</dt><dd><code>${esc(snapshot.plan_id||'없음')}</code><code>${esc(snapshot.plan_digest||'없음')}</code></dd><dt>실행</dt><dd><code>${esc(snapshot.run_id||'확정 전 · 실행 없음')}</code></dd><dt>조회 시각 · 순서</dt><dd>${esc(stamp(snapshot.observed_at))} · ${esc(snapshot.cursor)}</dd></dl></details>`;}
 function nodeInfo(node){if(node.kind==='document')return `<p>${node.document_kind==='approval'?'후보 수용을 결정하는 승인 문서입니다.':'이 실행의 시스템 보고 문서입니다.'}</p><p>아래 실행별 링크에서 원문과 대상 식별자를 확인할 수 있습니다.</p><details><summary>문서 참조</summary><pre>${esc(JSON.stringify({reference:node.reference||{},configuration:node.assignment||{}},null,2))}</pre></details>`;const a=node.assignment||{},r=a.requested||{},o=a.observed||{};return `<dl class="rg-facts"><div><dt>종류</dt><dd>${esc(kindLabels[node.kind]||node.kind)} · ${planned(node)?'예정':'저장된 기록'}</dd></div><div><dt>담당 업무</dt><dd>${esc(node.current_task_title||node.task_title||node.current_work||node.responsibility||'연결된 작업 없음')}</dd></div><div><dt>요청</dt><dd>${esc(r.model||'미배정')} · ${esc(r.reasoning_effort||'추론 미설정')}<br>Ultracode ${r.ultracode_enabled===true?'요청함':r.ultracode_enabled===false?'요청 안 함':'미확인'}</dd></div><div><dt>실행에서 확인된 설정</dt><dd>${o.status==='observed'?`${esc(o.model||'모델 미확인')} · ${esc(o.reasoning_effort||'추론 미확인')}`:'미확인'}</dd></div><div><dt>확인 근거</dt><dd>${esc(o.source||'없음')} · ${esc(o.scope||'미확인')}<br>모델의 자기 설명을 근거로 사용하지 않습니다.</dd></div>${node.wait_reason?`<div><dt>대기 이유</dt><dd>${esc(node.wait_reason)}${node.resume_at?`<br>재개 예약 ${esc(stamp(node.resume_at))}`:''}</dd></div>`:''}${a.quota?.status?`<div><dt>공유 한도</dt><dd>${esc(status(a.quota.status))}${a.quota.reset_at?` · ${esc(stamp(a.quota.reset_at))}`:''}</dd></div>`:''}</dl>${node.handoffs?.length?`<details><summary>담당 이관 ${node.handoffs.length}건</summary><p>역할과 작업은 유지하고 담당 세션만 바뀝니다.</p><pre>${esc(JSON.stringify(node.handoffs,null,2))}</pre></details>`:''}<details><summary>원본 참조</summary><pre>${esc(JSON.stringify({reference:node.reference||{},configuration:node.assignment||{}},null,2))}</pre></details>`;}
 function edgeInfo(edge,snapshot){const name=id=>snapshot.nodes.find(n=>n.id===id)?.name||id;return `<p class="rg-direction">${esc(name(edge.from))} → ${esc(name(edge.to))}</p><p>${esc(edge.reason||edge.title||'관계의 상세 이유는 기록되지 않았습니다.')}</p><dl class="rg-facts"><div><dt>구분</dt><dd>${planned(edge)?'계획의 예정 관계 · 실제 전달 아님':'저장된 전달 사실'}</dd></div><div><dt>상태</dt><dd>${esc(status(edge.status))}</dd></div><div><dt>기록 시각</dt><dd>${esc(stamp(edge.created_at))}</dd></div></dl><h4>산출물</h4>${edge.artifact_refs?.length?edge.artifact_refs.map(a=>`<div class="rg-artifact"><strong>${esc(a.kind||'근거')}</strong><code>${esc(a.sha||a.path||a.uri||a.id||'식별자 미기록')}</code></div>`).join(''):'<p>연결된 산출물 근거가 없습니다.</p>'}<details><summary>원본 참조</summary><pre>${esc(JSON.stringify(edge.reference||edge.source_ref||{},null,2))}</pre></details>`;}
 function detail(snapshot,v){const selected=v.selection,item=selected&&(selected.kind==='node'?snapshot.nodes:snapshot.edges).find(n=>n.id===selected.id);const html=`<aside class="rg-detail ${item?'is-open':''}" aria-label="그래프 상세"><div class="rg-detail-top"><h3 id="rg-detail-heading" tabindex="-1">${item?esc(selected.kind==='node'?item.name:relationLabels[item.kind]||item.kind):'상세'}</h3>${item?button('닫기','clear','aria-label="상세 닫기"'):''}</div>${item?`${selected.kind==='node'?`<p class="rg-state">${esc(status(item.status))}</p>${nodeInfo(item)}`:edgeInfo(item,snapshot)}`:selected?'<p>선택한 기록은 이번 응답에 없습니다. 과거 기록이 없다는 뜻은 아닙니다.</p>':'<p>노드나 전달선을 누르면 담당 모델과 산출물·대기 이유를 읽을 수 있습니다.</p>'}${refs(snapshot)}<div class="rg-reference-links">${(snapshot.approval_refs||[]).length?`<a href="#approvals?project=${encodeURIComponent(snapshot.project_id)}&run=${encodeURIComponent(snapshot.run_id)}">이 실행의 승인 ${(snapshot.approval_refs||[]).length}건</a>`:''}${(snapshot.report_refs||[]).length?`<a href="#reports?project=${encodeURIComponent(snapshot.project_id)}&run=${encodeURIComponent(snapshot.run_id)}">이 실행의 보고 ${(snapshot.report_refs||[]).length}건</a>`:''}</div><p class="rg-small">그래프 선택·위치 이동은 계획·모델·실행 권한을 변경하지 않습니다.</p></aside>`;return html.replace(/<summary>(.*?)<\/summary>/g,(_,text)=>{const key=text.startsWith('담당 이관')?'handoff':({'원본 참조':'source','문서 참조':'source','기록 기준':'provenance'}[text]||'evidence');return `<summary id="rg-detail-summary-${key}" data-rg-detail-key="${key}">${text}</summary>`;});}
 function summaryNode(node){if(node.kind==='document')return node.document_kind==='approval'?'승인 원문':'시스템 보고';const a=node.assignment||{},o=a.observed||{},r=a.requested||{};return `${o.status==='observed'?'확인':'요청'} · ${shortModel(o.status==='observed'?o.model:r.model)}`;}
 function graph(snapshot,v){const small=innerWidth<=700,layout=graphLayout(snapshot.nodes,small);
  if(v.small!==small){
   if(v.small!==null){
    v.positionsBySize.set(v.small,v.positions);
    v.camerasBySize.set(v.small,{pan:{...v.pan},zoom:v.zoom});
   }
   // Restore each width's camera. A newly visited width keeps the zoom but
   // recentres horizontally, so a wide-screen offset cannot hide the graph.
   const camera=v.camerasBySize.get(small);
   v.adjustCamera=!camera&&v.small!==null;
   if(camera){v.pan={...camera.pan};v.zoom=camera.zoom;}
   v.positions=v.positionsBySize.get(small)||{};v.small=small;
  }
  v.positions=placeGraphNodes(snapshot.nodes,layout,v.positions);
  v.nodeWidth=layout.nodeWidth;v.nodeHeight=layout.nodeHeight;
  const positions=v.positions,width=Math.max(layout.width,...snapshot.nodes.map(n=>(positions[n.id]?.x||0)+layout.nodeWidth+10)),height=Math.max(layout.height,...snapshot.nodes.map(n=>(positions[n.id]?.y||0)+layout.nodeHeight+20));
  v.canvas={width,height};const selected=v.selection,edge=selected?.kind==='edge'?snapshot.edges.find(e=>e.id===selected.id):null;
  const related=new Set(edge?[edge.from,edge.to]:selected?.kind==='node'?snapshot.edges.filter(e=>e.from===selected.id||e.to===selected.id).flatMap(e=>[e.from,e.to]):[]);
  const paths=snapshot.edges.filter(e=>positions[e.from]&&positions[e.to]).map((e,index)=>{
   const route=edgeGeometry(e,positions,layout.nodeWidth,layout.nodeHeight,index),path=route.path;
   const selectedEdge=edge?.id===e.id||selected?.kind==='node'&&(e.from===selected.id||e.to===selected.id);
   return `<g class="rg-edge ${planned(e)?'is-planned':''} ${selectedEdge?'is-selected':''}" data-rg-edge-id="${esc(e.id)}"><path class="rg-edge-line" d="${path}" marker-end="url(#rg-arrow)"/><path class="rg-edge-hit" d="${path}" id="rg-edge-${esc(e.id)}" data-rg-edge="${esc(e.id)}" tabindex="0" role="button" aria-label="${esc(`${snapshot.nodes.find(n=>n.id===e.from)?.name}에서 ${snapshot.nodes.find(n=>n.id===e.to)?.name} · ${relationLabels[e.kind]||e.kind} · ${planned(e)?'예정':'기록'}`)}"/><text x="${route.x}" y="${route.y}" aria-hidden="true">${esc(relationLabels[e.kind]||e.kind)}</text></g>`;
  }).join('');
  return `<div class="rg-viewport ${v.panMode?'is-moving':''}" tabindex="0" role="group" aria-label="역할 그래프 · 방향키로 이동"><div class="rg-scene"><svg class="rg-lines" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" aria-label="역할 관계"><defs><marker id="rg-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z"/></marker></defs>${paths}</svg>${snapshot.nodes.map(node=>{const p=positions[node.id];return `<button type="button" class="rg-node ${selected?.kind==='node'&&selected.id===node.id?'is-selected':''} ${related.has(node.id)?'is-related':''} ${planned(node)?'is-planned':''}" id="rg-node-${esc(node.id)}" data-rg-node="${esc(node.id)}" aria-pressed="${selected?.kind==='node'&&selected.id===node.id}"><span class="rg-node-kind">${esc(kindLabels[node.kind]||node.kind)}${planned(node)?' · 예정':''}</span><strong>${esc(node.name)}</strong><span class="rg-node-state">${esc(status(node.status))}</span><span class="rg-node-model">${esc(summaryNode(node))}</span></button>`;}).join('')}</div></div>`;
 }
 function list(snapshot,v){return `<div class="rg-list"><h3>역할</h3>${snapshot.nodes.map(n=>`<button type="button" id="rg-list-node-${esc(n.id)}" data-rg-node="${esc(n.id)}" aria-pressed="${v.selection?.kind==='node'&&v.selection.id===n.id}"><strong>${esc(n.name)}</strong><span>${esc(status(n.status))} · ${planned(n)?'예정':'기록'}</span><span>${esc(summaryNode(n))}</span></button>`).join('')}<h3>관계</h3>${snapshot.edges.map(e=>`<button type="button" id="rg-list-edge-${esc(e.id)}" data-rg-edge="${esc(e.id)}" aria-pressed="${v.selection?.kind==='edge'&&v.selection.id===e.id}"><strong>${esc(snapshot.nodes.find(n=>n.id===e.from)?.name||e.from)} → ${esc(snapshot.nodes.find(n=>n.id===e.to)?.name||e.to)}</strong><span>${esc(e.title||relationLabels[e.kind]||e.kind)} · ${planned(e)?'예정':'기록'}</span></button>`).join('')||'<p>이 응답에 연결된 관계가 없습니다.</p>'}</div>`;}
 function body(project){const record=projects.get(project),snapshot=scope(project);if(!record||!snapshot)return '<p class="rg-empty">그래프 기록이 없습니다. PM과 계획을 정하면 예정된 역할부터 확인할 수 있습니다.</p>';
  const v=view(snapshot),history=snapshot.history||{};
  const newCount=[...record.fresh].filter(id=>snapshot.edges.some(e=>e.id===id)&&!v.seen.has(id)).length;
  for(const id of record.fresh)v.seen.add(id);
  return `<header class="rg-header"><div><h2>진행</h2><p>${snapshot.source==='fixture'?'예시 기록 · 실제 실행 아님':snapshot.mode==='planned'||!snapshot.run_id?'계획 · 실제 실행 전':'저장된 실행 기록'}</p></div><label>대상<select id="rg-snapshot" data-rg-snapshot aria-label="그래프 대상">${record.data.snapshots.map(s=>`<option value="${esc(s.id)}" ${s.id===snapshot.id?'selected':''}>${esc(s.run_id?`실행 ${record.data.snapshots.filter(x=>x.run_id).findIndex(x=>x.id===s.id)+1}`:s.plan_id?`계획 ${record.data.snapshots.filter(x=>!x.run_id).findIndex(x=>x.id===s.id)+1}`:'준비')}${s.id===record.data.default_snapshot_id?' · 최근':''}</option>`).join('')}</select></label></header>${record.missingSelection?'<p class="rg-alert">선택한 실행이 이번 조회에 없어 최근 대상을 표시합니다. 실행 식별자를 확인하세요.</p>':''}${(snapshot.warnings||[]).map(w=>`<p class="rg-alert">${esc(w)}</p>`).join('')}${record.disconnected?'<p class="rg-alert" role="status">연결이 끊겼습니다. 마지막 조회 자료를 표시합니다.</p>':''}<div class="rg-tools">${diagramLinks&&snapshot.plan_id?`<a class="rg-export" href="/diagram-view.html?project=${encodeURIComponent(project)}&snapshot=${encodeURIComponent(snapshot.id)}&fingerprint=${encodeURIComponent(snapshot.fingerprint)}" target="_blank" rel="noopener">그림</a>`:''}<div role="group" aria-label="진행 보기">${button('그래프','graph',`aria-pressed="${v.mode==='graph'}"`)}${button('목록','list',`aria-pressed="${v.mode==='list'}"`)}</div>${v.mode==='graph'?`<div role="group" aria-label="그래프 탐색">${button('−','zoom-out','aria-label="축소"')}${button('+','zoom-in','aria-label="확대"')}${button('맞춤','fit')}${button('이동','pan',`aria-pressed="${v.panMode}"`)}</div><output class="rg-zoom" aria-label="확대 비율">${Math.round(v.zoom*100)}%</output>`:''}</div><div class="rg-layout"><section class="rg-map" aria-label="관계 보기">${snapshot.nodes.length?v.mode==='graph'?graph(snapshot,v):list(snapshot,v):`<p class="rg-empty">${esc(snapshot.empty_reason||'저장된 역할이 없습니다. PM의 역할 제안을 확인하세요.')}</p>`}<p class="rg-small">실선: 저장된 관계 · 점선: 예정 관계<br>표시 ${snapshot.edges.length}건${Number.isInteger(history.total)?` / 조회 대상 ${history.total}건`:''}${history.complete===false?' · 과거 이력 일부만 포함':''}. 위치는 실행 순서를 뜻하지 않습니다.</p><details class="rg-help"><summary>조작</summary><p>노드·선을 눌러 상세를 엽니다. 이동 모드를 켜면 화면과 노드를 끌 수 있습니다. 키보드는 방향키로 화면 이동, 노드에서 Alt+방향키로 배치 이동합니다. 목록에서도 같은 기록을 선택할 수 있습니다.</p></details><p class="rg-update" role="status">${newCount?`새 전달 ${newCount}건`:`조회 순서 ${record.data.cursor}`}</p></section>${detail(snapshot,v)}</div>`;
 }
 function render(overview,project){
  // A forced replacement (offline, navigation) must settle queued data before
  // creating markup; mount() must not apply a different snapshot to old markup.
  disposeMount();resizeObserver?.disconnect();
  if(!overview.workspace_graph)return '<p class="rg-empty">이 서버에는 실행별 그래프 조회가 연결되지 않았습니다. 기존 진행 기록을 이용하세요.</p>';
  if(!projects.has(project))ingest(overview,project);
  return `<section class="workspace-graph" data-rg-root data-project="${esc(project)}" data-snapshot="${esc(scope(project)?.id||'')}">${body(project)}</section>`;
 }
 function capture(container,project){const s=scope(project);if(!s)return;const graphRoot=container.matches?.('[data-rg-root]')?container:container.querySelector('[data-rg-root]');if(graphRoot?.dataset.project!==project||graphRoot?.dataset.snapshot!==s.id)return;const panel=graphRoot.querySelector('.rg-detail');if(!panel)return;const v=view(s);v.detailScroll=panel.scrollTop;v.detailOpen=[...panel.querySelectorAll('details')].filter(d=>d.open).map(d=>d.querySelector('summary')?.dataset.rgDetailKey).filter(Boolean);}
 function mount(container,overview,project){
  disposeMount();resizeObserver?.disconnect();const root=container.querySelector('[data-rg-root]');if(!root)return;activeProject=project;
  const controller=new AbortController(),signal=controller.signal;
  let dragging=null,finishedGesture=null,suppressClick=false,finishTimer=null,tabNavigation=false,tabTimer=null;
  function finishInteraction({cancelled=false,paint=true,notify=true}={}){
   clearTimeout(finishTimer);finishTimer=null;
   const gesture=dragging||finishedGesture;dragging=null;finishedGesture=null;
   if(interaction?.project!==project)return;
   interaction=null;
   if(gesture?.target.hasPointerCapture?.(gesture.pointer))gesture.target.releasePointerCapture(gesture.pointer);
   const queued=pendingEnvelope;pendingEnvelope=null;
   if(queued){
    const disconnected=projects.get(queued.project)?.disconnected;
    ingest({workspace_graph:queued.data},queued.project);
    if(disconnected)projects.get(queued.project).disconnected=true;
   }
   const current=scope(project),resized=current&&view(current).small!==(innerWidth<=700);
   if(paint&&root.isConnected&&(gesture?.moved||queued||resized))redraw(gesture?.moved&&gesture.id?{id:gesture.id,kind:'node'}:null);
   if(notify)onInteractionEnd({project,cancelled});
  }
  disposeMount=()=>{
   controller.abort();clearTimeout(tabTimer);tabNavigation=false;const active=interaction?.project===project;
   finishInteraction({cancelled:true,paint:false,notify:false});
   if(active)queueMicrotask(()=>onInteractionEnd({project,cancelled:true}));
  };
  function restoreFocus(id,kind){if(!id)return;const attr=kind==='edge'?'data-rg-edge':'data-rg-node';[...root.querySelectorAll(`[${attr}]`)].find(el=>el.getAttribute(attr)===id)?.focus({preventScroll:true});}
  function redraw(focus){
   const active=document.activeElement,focusId=root.contains(active)?active.id:null;
   root.innerHTML=body(project);root.dataset.snapshot=scope(project)?.id||'';geometry();
   if(focus)restoreFocus(focus.id,focus.kind);
   else if(focusId){const replacement=document.getElementById(focusId);if(root.contains(replacement))replacement.focus({preventScroll:true});}
  }
  function geometry(){
   if(signal.aborted||!root.isConnected)return;
   const s=scope(project),v=s&&view(s),scene=root.querySelector('.rg-scene');if(!v)return;
   const panel=root.querySelector('.rg-detail');
   if(panel){panel.querySelectorAll('details').forEach(d=>{d.open=v.detailOpen.includes(d.querySelector('summary')?.dataset.rgDetailKey);});panel.scrollTop=v.detailScroll;}
   if(!scene)return;
   scene.style.width=v.canvas.width+'px';scene.style.height=v.canvas.height+'px';
   for(const el of root.querySelectorAll('.rg-node')){
    const p=v.positions[el.dataset.rgNode];el.style.left=p.x+'px';el.style.top=p.y+'px';el.style.width=v.nodeWidth+'px';el.style.height=v.nodeHeight+'px';
   }
   if(v.adjustCamera){
    const viewport=root.querySelector('.rg-viewport');
    const selected=v.selection?.kind==='node'?v.positions[v.selection.id]:null,anchor=selected||v.positions[s.nodes[0]?.id];
    if(viewport?.clientWidth>0&&anchor){
     v.pan.x=viewport.clientWidth/2-(anchor.x+v.nodeWidth/2)*v.zoom;
     v.adjustCamera=false;
    }
   }
   transform();
  }
  function transform(){const s=scope(project),v=s&&view(s),scene=root.querySelector('.rg-scene');if(scene&&v){scene.style.transform=`translate(${v.pan.x}px,${v.pan.y}px) scale(${v.zoom})`;const o=root.querySelector('.rg-zoom');if(o)o.textContent=`${Math.round(v.zoom*100)}%`;}}
  root.addEventListener('change',event=>{capture(root,project);if(event.target.matches('[data-rg-snapshot]')){projects.get(project).selected=event.target.value;redraw();root.querySelector('[data-rg-snapshot]')?.focus();}},{signal});
  root.addEventListener('click',event=>{capture(root,project);if(suppressClick&&event.detail!==0){suppressClick=false;event.preventDefault();event.stopPropagation();return;}const n=event.target.closest('[data-rg-node]'),e=event.target.closest('[data-rg-edge]'),action=event.target.closest('[data-rg-action]')?.dataset.rgAction,s=scope(project);if(!s)return;const v=view(s);
   if(n||e){const next={kind:n?'node':'edge',id:n?n.dataset.rgNode:e.dataset.rgEdge};if(v.selection?.id!==next.id||v.selection?.kind!==next.kind){v.detailScroll=0;v.detailOpen=[];}v.selection=next;redraw(v.selection);if(innerWidth<=700)root.querySelector('#rg-detail-heading')?.focus({preventScroll:true});return;}
   if(!action)return;
   if(['graph','list'].includes(action)){v.mode=action;redraw();root.querySelector(`[data-rg-action=${action}]`)?.focus();return;}
   if(action==='clear'){const prior=v.selection;v.selection=null;v.detailScroll=0;v.detailOpen=[];redraw(prior);return;}
   if(action==='pan'){v.panMode=!v.panMode;redraw();root.querySelector('[data-rg-action=pan]')?.focus();return;}
   const viewport=root.querySelector('.rg-viewport');if(!viewport)return;
   if(action==='fit'){v.zoom=clamp(Math.min(viewport.clientWidth/v.canvas.width,viewport.clientHeight/v.canvas.height),.25,1);v.pan={x:(viewport.clientWidth-v.canvas.width*v.zoom)/2,y:8};}
   else {const old=v.zoom;v.zoom=clamp(old*(action==='zoom-in'?1.25:.8),.25,2.5);v.pan={x:viewport.clientWidth/2-(viewport.clientWidth/2-v.pan.x)*v.zoom/old,y:viewport.clientHeight/2-(viewport.clientHeight/2-v.pan.y)*v.zoom/old};}
   transform();
  },{signal});
  root.addEventListener('keydown',event=>{
   if(event.key!=='Tab'||event.altKey||event.ctrlKey||event.metaKey)return;
   tabNavigation=true;clearTimeout(tabTimer);
   tabTimer=setTimeout(()=>{tabNavigation=false;tabTimer=null;},0);
  },{signal});
  root.addEventListener('focusin',event=>{
   if(!tabNavigation)return;
   tabNavigation=false;clearTimeout(tabTimer);tabTimer=null;
   const target=event.target.closest('[data-rg-node],[data-rg-edge]'),viewport=target?.closest('.rg-viewport'),s=scope(project);
   if(!viewport||!s)return;
   viewport.scrollIntoView({block:'center',inline:'nearest'});
   const bounds=target.getBoundingClientRect(),frame=viewport.getBoundingClientRect();
   if(bounds.left>=frame.left+1&&bounds.right<=frame.right-1&&bounds.top>=frame.top+1&&bounds.bottom<=frame.bottom-1)return;
   // Only an actual Tab move reveals clipped targets. Polling focus restoration
   // leaves the camera untouched, and graph navigation never uses native scroll.
   const v=view(s);
   v.pan.x+=(frame.left+frame.right-bounds.left-bounds.right)/2;
   v.pan.y+=(frame.top+frame.bottom-bounds.top-bounds.bottom)/2;
   transform();
  },{signal});
  root.addEventListener('keydown',event=>{capture(root,project);const n=event.target.closest('[data-rg-node]'),e=event.target.closest('[data-rg-edge]'),s=scope(project);if(!s)return;const v=view(s);if(event.key==='Escape'&&v.selection){event.preventDefault();const prior=v.selection;v.selection=null;v.detailScroll=0;v.detailOpen=[];redraw(prior);return;}if(e&&['Enter',' '].includes(event.key)){event.preventDefault();e.dispatchEvent(new MouseEvent('click',{bubbles:true}));return;}if(!event.key.startsWith('Arrow')||!event.target.closest('.rg-viewport'))return;event.preventDefault();const delta={ArrowLeft:[-24,0],ArrowRight:[24,0],ArrowUp:[0,-24],ArrowDown:[0,24]}[event.key];if(n&&event.altKey){const p=v.positions[n.dataset.rgNode];p.x=Math.max(0,p.x+delta[0]);p.y=Math.max(0,p.y+delta[1]);redraw({id:n.dataset.rgNode,kind:'node'});}else {v.pan.x+=delta[0];v.pan.y+=delta[1];transform();}},{signal});
  root.addEventListener('pointerdown',event=>{
   // A new physical gesture must never inherit the previous drag's click suppression.
   suppressClick=false;
   const viewport=event.target.closest('.rg-viewport');
   if(event.button!==0||event.isPrimary===false||!viewport||interaction)return;
   const s=scope(project),v=s&&view(s);if(!v?.panMode)return;
   const node=event.target.closest('[data-rg-node]'),target=node||viewport;
   dragging={id:node?.dataset.rgNode,startX:event.clientX,startY:event.clientY,pan:{...v.pan},position:node?{...v.positions[node.dataset.rgNode]}:null,pointer:event.pointerId,target,moved:false};
   interaction={project};target.setPointerCapture(event.pointerId);
  },{signal});
  root.addEventListener('pointermove',event=>{
   if(!dragging||event.pointerId!==dragging.pointer)return;
   const dx=event.clientX-dragging.startX,dy=event.clientY-dragging.startY;
   if(!dragging.moved&&Math.hypot(dx,dy)<4)return;
   dragging.moved=true;const s=scope(project),v=view(s);
   if(dragging.id){
    v.positions[dragging.id]={x:Math.max(0,dragging.position.x+dx/v.zoom),y:Math.max(0,dragging.position.y+dy/v.zoom)};
    const el=[...root.querySelectorAll('[data-rg-node]')].find(n=>n.dataset.rgNode===dragging.id);
    if(el){el.style.left=v.positions[dragging.id].x+'px';el.style.top=v.positions[dragging.id].y+'px';}
    for(const [index,g] of [...root.querySelectorAll('[data-rg-edge-id]')].entries()){
     const edge=s.edges.find(e=>e.id===g.dataset.rgEdgeId),route=edgeGeometry(edge,v.positions,v.nodeWidth,v.nodeHeight,index);
     g.querySelectorAll('path').forEach(path=>path.setAttribute('d',route.path));
     const text=g.querySelector('text');text.setAttribute('x',route.x);text.setAttribute('y',route.y);
    }
   }else {v.pan={x:dragging.pan.x+dx,y:dragging.pan.y+dy};transform();}
  },{signal});
  function end(event){
   if(!dragging||event.pointerId!==dragging.pointer)return;
   finishedGesture=dragging;dragging=null;suppressClick=finishedGesture.moved;
   if(event.type==='pointerup'){
    // Let the native click select a tap, or suppress only this drag's click, before repainting.
    finishTimer=setTimeout(()=>finishInteraction(),0);
   }else finishInteraction({cancelled:true});
  }
  root.addEventListener('pointerup',end,{signal});root.addEventListener('pointercancel',end,{signal});root.addEventListener('lostpointercapture',end,{signal});
  geometry();
  let lastSmall=innerWidth<=700;
  resizeObserver=new ResizeObserver(()=>{
   if(signal.aborted||!root.isConnected)return;
   const small=innerWidth<=700,current=scope(project);
   if(small!==lastSmall){lastSmall=small;if(!interaction)redraw();}
   else if(!interaction&&current&&view(current).adjustCamera)geometry();
  });
  resizeObserver.observe(root);
 }
 return {ingest,render,mount,capture,isInteracting:project=>Boolean(interaction&&interaction.project===project),disconnect:project=>{const p=projects.get(project);if(p)p.disconnected=true;},reset:()=>{disposeMount();resizeObserver?.disconnect();projects.clear();views.clear();pendingEnvelope=null;interaction=null;activeProject='';}};
}
