import {createWorkspaceGraph} from '../workspace-graph-ui.js';
const fixture=window.GRAPH_FIXTURE||await(await fetch('./fixture.json')).json();
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const stamp=value=>value?new Date(value*1000).toLocaleString('ko-KR'):'미기록';
const graph=createWorkspaceGraph({esc,stamp,label:String}),pid=fixture.project.id;
graph.ingest(fixture,pid);
const content=document.querySelector('#content');
function render(){
 const route=location.hash.slice(1).split('?')[0]||'progress',params=new URLSearchParams(location.hash.split('?')[1]||'');
 document.querySelectorAll('.preview-header nav a').forEach(a=>a.setAttribute('aria-current',a.hash==='#'+route?'page':'false'));
 if(route==='progress'){content.innerHTML=graph.render(fixture,pid);graph.mount(content,fixture,pid);return;}
 if(route==='manager'){content.innerHTML=`<section class="preview-document"><small>${esc(fixture.project.name)}</small><h1>역할별 진행을 한눈에 보고 싶어요.</h1><article><small>마스터 · 저장된 예시</small><p>개발과 검사를 나누고 전달한 결과와 대기 이유를 보여 주세요.</p></article><article><small>Astra Ultra PM · 예시 제안</small><p>함수 개발과 독립 검사를 나눕니다. 각 역할은 같은 계약으로 병렬 작업하고, 결과가 모이면 검사와 독립 검수로 이어집니다.</p><p>한 역할이 한도를 기다려도 다른 역할은 계속합니다. 최종 검수와 후보 수용은 별도로 남깁니다.</p><a href="#progress">팀의 진행 보기 →</a></article><p>이 미리보기는 임시 저장소의 예시를 읽습니다. 목표 입력·계획 확정·모델 호출 기능은 연결하지 않았습니다.</p></section>`;return;}
 const snapshot=fixture.workspace_graph.snapshots.find(s=>s.run_id===params.get('run'));
 const ids=snapshot?new Set((route==='approvals'?snapshot.approval_refs:snapshot.report_refs).map(r=>r.id)):null;
 if(route==='approvals'){
  const items=fixture.approvals.filter(a=>!ids||ids.has(a.id));
  content.innerHTML=`<section class="preview-document"><small>승인 · 예시 기록</small><h1>결정할 내용</h1>${items.map(a=>`<article><small>미결 · ${esc(a.id)}</small><h2>${esc(a.title)}</h2><p>${esc(a.action)}</p><h3>영향</h3><p>${esc(a.impact)}</p><h3>되돌리기</h3><p>${esc(a.rollback)}</p><details><summary>원문과 대상</summary><pre>${esc(JSON.stringify(a,null,2))}</pre></details></article>`).join('')||'<p>이 실행에 연결된 승인 요청이 없습니다.</p>'}<p>읽기 전용 예시입니다. 승인 버튼을 제공하지 않으며 운영 승인 기록을 변경하지 않습니다.</p><a href="#progress">진행으로 →</a></section>`;
 }else content.innerHTML=`<section class="preview-document"><h1>보고</h1><p>선택한 예시 실행 ${esc(snapshot?.run_id||'없음')}</p>${(snapshot?.report_refs||[]).map(r=>`<article><h2>${esc(r.title)}</h2><p>저장된 실행의 시스템 보고 참조입니다. 실제 모델이나 원격 CI 검증 결과가 아닙니다.</p><details><summary>기록 참조</summary><pre>${esc(JSON.stringify(r,null,2))}</pre></details></article>`).join('')}<a href="#progress">진행으로 →</a></section>`;
}
document.querySelector('#theme').addEventListener('click',()=>{const black=document.documentElement.dataset.theme!=='black';document.documentElement.dataset.theme=black?'black':'light';document.querySelector('#theme').textContent=black?'Light':'Black';});
window.addEventListener('hashchange',()=>{render();window.scrollTo(0,0);});render();
