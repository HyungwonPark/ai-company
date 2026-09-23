/* Isolated design fixture. No fetch, API, workers, approval writes or model calls. */
'use strict';
(() => {
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const key = 'ai-company:task-design:1';
const exampleGoal = '휴대폰에서 편하게 로그인하고, 오류가 나도 다음 행동을 알 수 있게 해 주세요.';
const defaultScope = '로그인 화면의 한국어 안내와 오류 다음 행동을 정리합니다. 화면 개발과 독립 검사는 병렬로 진행합니다.';
const defaultCriteria = '휴대폰에서 내용을 읽고 오류 다음 행동을 찾습니다. 같은 후보의 격리 검사·원격 CI·독립 검수·Astra Ultra 최종 검수 근거를 연결합니다.';
function freshState(){return {layout:'journey',theme:'light',stage:'projects',tab:'work',scenario:'normal',project:'sample',run:'current',draft:{name:'',goal:'',reply:'',sent:false,example:false},created:null,spec:false,plan:false,confirmed:false,handoff:false,decision:null,decisions:{},events:[],legacyPreview:null,flow:{status:'draft',version:0,scope:defaultScope,criteria:defaultCriteria,run:null,playback:'idle'}};}
let state;
try {
 const saved=JSON.parse(sessionStorage.getItem(key)||'{}'), base=freshState();
 state={...base,...saved,draft:{...base.draft,...saved?.draft},flow:{...base.flow,...saved?.flow},decisions:{...saved?.decisions},events:Array.isArray(saved?.events)?saved.events:[]};
 // Earlier previews only stored a confirmation marker, never a playback run.
 if(!saved?.flow){
  if(saved?.spec||saved?.plan||saved?.confirmed||saved?.decision)state.legacyPreview={spec:saved.spec,plan:saved.plan,confirmed:saved.confirmed,decision:saved.decision,created:saved.created};
  state.spec=false;state.plan=false;state.confirmed=false;state.decision=null;
 }
} catch {state=freshState();}
let opener = null;
const steps = [['projects','프로젝트'],['plan','계획'],['progress','진행'],['approval','승인'],['result','결과']];
const isNew = () => state.project === 'new';
const runId = () => isNew() ? (state.flow.run?.id || null) : state.run === 'current' ? 'example-run-02' : 'example-run-01';
const planId = () => isNew() ? (state.flow.run?.plan || `example-plan-new-v${state.flow.version}`) : state.run === 'current' ? 'example-plan-02' : 'example-plan-01';
const digest = () => isNew() ? (state.flow.run?.planDigest || state.flow.version.toString(16).padStart(64,'c')) : (state.run === 'current'?'b':'a').repeat(64);
const oldRun = () => !isNew() && state.run === 'previous';
const projectId = () => isNew()?'example-project-new':'example-project-login';
const complete = () => oldRun() || (isNew() && state.flow.playback==='review');
const transferred = () => isNew()?['handoff','checks','review'].includes(state.flow.playback):state.handoff&&!oldRun();
const candidateId = () => isNew()?'example-candidate-new-01':'example-candidate-01';
const decisionLabels = {approve:'수용',request_changes:'수정 요청',hold:'보류'};
const decisionNext = {approve:'예시 후보를 수용했습니다. 결과를 확인하세요. 배포·병합은 시작하지 않습니다.',request_changes:'수정 의견을 남겼습니다. 새 범위·계획을 검토하기 전에는 다시 실행하지 않습니다.',hold:'이 탭에서 나중에 보기로 표시했습니다. 원래 요청은 계속 pending이며 자동 실행하지 않습니다.'};
function record(type,extra={}){state.events.push({type,project:projectId(),plan:planId(),planDigest:digest(),specVersion:isNew()?state.flow.version:1,run:runId(),...extra});}
function requests(){
 const all=[{project:'example-project-login',projectName:'모바일 로그인 정리',specVersion:1,plan:'example-plan-01',planDigest:'a'.repeat(64),run:'example-run-01',candidate:'example-candidate-01',approval:'example-approval-01',digest:'d'.repeat(64),status:'pending'}];
 if(state.created&&state.flow.playback==='review')all.push({project:'example-project-new',projectName:state.created.name,specVersion:state.flow.run.specVersion,plan:state.flow.run.plan,planDigest:state.flow.run.planDigest,run:state.flow.run.id,candidate:'example-candidate-new-01',approval:'example-approval-new-01',digest:'e'.repeat(64),status:'pending'});
 return all;
}
const currentRequest = () => requests().find(r=>r.project===projectId()&&r.run===runId());
const projectRequests = () => requests().filter(r=>r.project===projectId());
function requestLink(r){return `<button data-approval="${r.approval}"><span>${esc(r.projectName)} · ${r.run==='example-run-01'?'이전 1차':'이번'} 실행</span><small>확인할 일 1건${state.decisions[r.approval]?` · 예시 ${decisionLabels[state.decisions[r.approval].choice]}`:''}</small></button>`;}
function openRequest(id){const r=requests().find(r=>r.approval===id);if(!r)return;state.project=r.project==='example-project-new'?'new':'sample';state.run='previous';setStage('approval');}

const projectName = () => isNew() ? state.created?.name || '새 프로젝트' : state.scenario === 'long' ? '모바일 로그인 화면을 더 쉽고 읽기 편하게 정리하고 한국어 안내와 오류 복구를 확인하는 프로젝트' : '모바일 로그인 정리';
const goal = () => isNew() ? state.created?.goal || '' : '휴대폰에서 편하게 로그인하고, 오류가 나도 다음 행동을 알 수 있게 해 주세요.';
const tasks = () => {
 const handoff=transferred();
 const done=complete(), quota=state.scenario==='quota', failure=state.scenario==='failure';
 const parallel=isNew()&&state.flow.playback==='parallel', checks=isNew()&&state.flow.playback==='checks';
 return [
 {id:runId()+'-task-screen',title:state.scenario==='long'?'긴 한국어와 오류 안내가 잘리는 문제를 고치고 로그인 화면을 읽기 편하게 정리하기':'로그인 화면 정리',role:'화면 개발',model:handoff?'Claude':'Astra',status:done||checks?'완료':failure?'판단 필요':(handoff||parallel)&&!quota?'진행':'한도 대기',kind:done||checks?'done':failure?'blocked':(handoff||parallel)&&!quota?'active':'waiting',reason:done||checks?'예시 후보에 반영됨':parallel?'같은 계획의 화면 개발을 시작했습니다.':failure?'예시 실패 상한 3회에 도달했습니다.':handoff&&!quota?'승인된 대체 후보로 같은 일을 이어갑니다.':'개발 계정의 사용 가능 시각을 기다립니다.'},
 {id:runId()+'-task-tests',title:'오류 상황 검사 작성',role:'독립 검사',model:'Claude',status:done||checks?'완료':quota?'한도 대기':'진행',kind:done||checks?'done':quota?'waiting':'active',reason:done||checks?'검사 결과 전달됨':quota?'모든 적격 후보가 대기 중입니다.':'개발과 독립적으로 검사 코드를 작성합니다.'},
 {id:runId()+'-task-integration',title:'같은 후보 검수',role:'검사 · 독립 검수 · 최종 검수',model:'단계별 담당',status:done?'완료':checks?'검수 중':'선행 대기',kind:done?'done':checks?'active':'waiting',reason:done?'검사와 두 검수의 예시 기록이 있습니다.':checks?'같은 예시 후보의 검사·독립 검수·최종 검수를 재생합니다.':'화면 변경과 검사 코드가 모두 필요합니다.'}
 ];
};
function save(){try{sessionStorage.setItem(key,JSON.stringify(state));}catch{}}
function notify(message){$('#notice').textContent=message;}
function setStage(stage){state.stage=stage;save();render();$('#main')?.focus();}
function open(title,body){if(!$('#detail').open)opener=document.activeElement;$('#detail-body').innerHTML=`<h2 id="detail-title">${esc(title)}</h2>${body}`;$('#detail').showModal();}
$('#detail').addEventListener('close',()=>{
 const active=document.activeElement;
 // Native close restores focus. A deferred close event must not steal a newer choice.
 if(!$('#detail').open&&(active===document.body||$('#detail').contains(active))&&opener?.isConnected)opener.focus();
});
function binding(){return `<details class="binding"><summary>근거</summary><dl><dt>프로젝트</dt><dd>${projectId()}</dd><dt>실행</dt><dd>${runId()||'없음 · 아직 시작하지 않음'}</dd><dt>계획</dt><dd>${planId()}</dd><dt>계획 digest</dt><dd><code>${digest()}</code></dd></dl><p>식별자와 기록은 모두 예시입니다. 요약·번역·과거 기록은 현재의 실행 승인으로 쓰지 않습니다.</p></details>`;}
function nav(){return `<nav class="stage-nav" aria-label="작업 단계">${steps.map(([id,label],i)=>`<button data-stage="${id}" ${state.stage===id?'aria-current="page"':''}><span>0${i+1}</span>${label}${id==='approval'&&projectRequests().length?` <span class="approval-count">${projectRequests().length}</span>`:''}</button>`).join('')}</nav>`;}
function next(){
 const selected=currentRequest(),decision=selected&&state.decisions[selected.approval];
 if(decision)return [`예시 ${decisionLabels[decision.choice]}`,decisionNext[decision.choice],'result','결과 보기'];
 if(isNew()){
  if(!state.created?.example)return ['체험 목표 선택','현재는 고정된 로그인 예시 한 가지를 체험할 수 있습니다.','plan','예시 보기'];
  const f=state.flow;
  if(f.status==='preparing')return ['계획 준비 중','검수용 버튼으로 응답이나 실패 상태를 재생하세요.','plan','계획 보기'];
  if(f.status==='failed')return ['계획 다시 준비','아직 실행은 없습니다. 같은 명세로 다시 시도하세요.','plan','다시 시도'];
  if(!state.confirmed)return [f.status==='ready'?'계획 확인':'내용 정리','범위와 완료 조건을 읽고 직접 시작하세요.','plan','계획 보기'];
  if(f.playback==='review')return ['후보 검토','같은 실행의 보고와 검사 근거를 확인하세요.','approval','후보 보기'];
  return [f.playback==='waiting'?'개발 재개 대기':f.playback==='checks'?'같은 후보 검수':'병렬 진행','검수용 단계 재생이며 실제 작업은 실행하지 않습니다.','progress','진행 보기'];
 }
 if(oldRun())return ['후보를 검토해 주세요','같은 실행의 검사 근거와 변경 범위를 읽어 보세요.','approval','후보 보기'];
 if(state.scenario==='failure')return ['실패 원인 검토','추가 개발 범위와 재시도는 별도 결정이 필요합니다.','progress','업무 보기'];
 if(state.scenario==='quota')return ['예약 재개 대기','모든 적격 후보가 대기 중입니다. 보고와 승인은 계속 읽을 수 있습니다.','progress','대기 보기'];
 return [state.handoff?'개발과 검사 진행 중':'개발 재개 대기',state.handoff?'같은 업무를 대체 담당자가 이어갑니다.':'독립 검사는 계속 진행 중입니다.','progress','업무 보기'];
}
function rail(){const n=next(),pending=projectRequests(),planning=isNew()&&state.plan&&!state.confirmed;return `<aside class="rail" aria-label="요약"><section><div class="next-label">다음</div><div class="next">${n[0]}</div><p>${n[1]}</p><button data-stage="${n[2]}">${n[3]}</button></section><section><h3>내 결정</h3><p>${planning?'계획 확인 1건':pending.length?`확인할 일 ${pending.length}건`:'현재 실행의 요청 없음'}</p>${pending.map(requestLink).join('')}${!pending.length?`<button data-stage="${planning?'plan':'approval'}">${planning?'계획 확인':'승인 보기'}</button>`:''}</section><section><h3>기록</h3><p>PM의 설명과 시스템 사실을 나눠 읽습니다.</p><button data-stage="result">결과 보기</button></section></aside>`;}
function runPicker(){return isNew()?'':`<label class="run-picker">실행 <select id="run"><option value="current" ${state.run==='current'?'selected':''}>현재 · 2차 작업</option><option value="previous" ${state.run==='previous'?'selected':''}>이전 · 1차 작업</option></select></label>`;}
function head(){return `<div class="project-head"><div><button class="back" data-stage="projects">← 프로젝트</button><h1>${esc(projectName())}</h1><p class="subtle">${esc(goal())}</p></div></div>`;}
function banner(){const legacy=state.legacyPreview?'<p class="banner">이전 시안의 입력·확정·선택 기록을 보관했습니다. 이전 확정으로 새 예시 실행을 만들지는 않습니다. <button data-action="legacy-record">이전 기록</button></p>':'';return legacy+(state.scenario==='offline'?'<div class="banner" role="alert"><strong>연결 끊김</strong><p>마지막 예시 기록입니다. 현재 상태를 확인하지 못했어요. 확인·저장 조작은 잠깐 멈춥니다.</p><button data-action="reconnect">다시 연결</button></div>':'');}
function projects(){return `<main id="main" class="projects" tabindex="-1"><div class="projects-top"><h1>프로젝트</h1><button class="primary" data-action="new">새 프로젝트</button></div><p>이름과 목표로 PM과 시작하세요.</p>${state.scenario==='empty'&&!state.created?'<div class="empty"><h2>아직 없어요</h2><p>해 보고 싶은 일을 편하게 적어 주세요.</p></div>':`<ul class="project-list">${state.scenario!=='empty'?`<li><button data-project="sample"><span><strong>모바일 로그인 정리</strong><small>${state.scenario==='quota'?'모든 적격 후보 대기':state.scenario==='failure'?'개발 실패 원인 검토 · 독립 검사 진행 중':state.handoff?'개발과 독립 검사 진행 중':'개발 재개 대기 · 독립 검사 진행 중'}</small></span><span class="arrow" aria-hidden="true">↗</span></button>${requestLink(requests()[0])}</li>`:''}${state.created?`<li><button data-project="new"><span><strong>${esc(state.created.name)}</strong><small>${state.confirmed?'예시 실행 · 단계 재생':'PM 제안 확인'}</small></span><span class="arrow" aria-hidden="true">↗</span></button>${requests().filter(r=>r.project==='example-project-new').map(requestLink).join('')}</li>`:''}</ul>`}<p class="readonly">시안의 가상 프로젝트입니다. 운영 프로젝트나 승인 기록을 읽지 않습니다.</p></main>`;}
function planDetails(){return `<h3>범위</h3><p>${esc(isNew()?state.flow.scope:defaultScope)}</p><h3>완료 조건</h3><p>${esc(isNew()?state.flow.criteria:defaultCriteria)}</p><h3>한도</h3><p>실제 모델 호출 0회 · 운영 비용 0원 · 예시 실행 1건. 배포·병합·추가 업무는 포함하지 않습니다.</p><details><summary>실행 명세</summary><p>로그인 예시의 화면·검사 두 역할만 재생합니다. 운영의 pilot 두 파일 제한·계정·예산은 변경하지 않습니다.</p><p>예시 명세 ${isNew()?`v${state.flow.version}`:'v1'} · 명세 저장, 버전 고정, 계획 요청, 직접 확정은 별도 기록입니다.</p></details>`;}
function plan(){
 const fresh=isNew(),f=state.flow;
 if(fresh&&!state.created.example)return `<h2>계획</h2><p class="meta">PM 예시 · 모델 호출 없음</p><p class="pm-quote">목표를 보관했어요.</p><p>이 시안은 임의 목표의 계획을 만들지 않습니다. 한 사이클을 보려면 준비된 고정 로그인 예시 목표를 직접 선택해 주세요.</p><form id="pm-form"><label for="pm-reply">PM에게</label><textarea id="pm-reply" maxlength="2000" placeholder="추가로 남길 내용을 적어 주세요.">${esc(state.draft.reply)}</textarea><button type="submit" ${offline()}>답변 보관</button></form><p class="subtle">입력만 보관하며 실제 PM 응답이나 계획을 만들지 않습니다.</p><button class="primary" data-action="use-example" ${offline()}>로그인 예시로 체험</button>${binding()}`;
 return `<h2>계획</h2><p class="meta">PM 제안 · 고정 예시, 모델 호출 없음</p><p class="pm-quote">${fresh&&!state.draft.sent?'오류가 났을 때 어떤 안내가 필요할까요?':'로그인 화면과 오류 검사를 나눠 진행하겠습니다. 두 결과가 모이면 같은 후보를 검사하고 검수합니다.'}</p>
 ${fresh&&!state.confirmed?`<form id="pm-form"><label for="pm-reply">PM에게</label><textarea id="pm-reply" maxlength="2000" placeholder="예: 입력을 확인하고 다시 시도할 위치를 알려 주세요.">${esc(state.draft.reply)}</textarea><button type="submit" ${offline()}>답변 보내기</button></form>${state.draft.sent?'<p class="subtle">답변을 보관했습니다. 아래 내용은 고정 예시이며 자유 답변을 해석한 결과가 아닙니다. 필요한 범위는 직접 고칠 수 있습니다.</p>':''}`:''}
 <hr class="divider"><h3>할 일</h3><ul class="plain-list"><li>화면 개발 — 로그인 화면 정리</li><li>독립 검사 — 오류 상황 확인, 개발과 병렬 진행</li><li>통합 검수 — 같은 후보의 검사와 두 검수</li></ul>${planDetails()}
 ${fresh&&!state.confirmed?`<button data-action="edit-plan" ${offline()}>내용 수정</button>`:''}
 ${fresh?f.status==='preparing'?`<div class="playback"><h3>계획 준비 중</h3><p>아직 개발은 시작하지 않았습니다.</p><p class="subtle">검수용 응답 재생</p><div class="actions"><button data-action="plan-ready" ${offline()}>제안 도착</button><button data-action="plan-fail" ${offline()}>실패 재생</button></div></div>`:f.status==='failed'?`<div class="banner"><strong>계획을 준비하지 못했어요</strong><p>예시 실패 · 실행 0건. 저장한 명세로 다시 시도할 수 있습니다.</p><button data-action="retry-plan" ${offline()}>다시 시도</button></div>`:f.status==='invalidated'?'<p class="banner">내용이 바뀌었습니다. 다시 정리한 계획을 확인해 주세요. 이전 검토로 시작할 수 없습니다.</p>':'':''}
 <div class="actions">${!fresh||state.confirmed?'<span class="status">예시 계획 확정됨</span>':f.status==='ready'?`<button class="primary" data-action="confirm" ${offline()}>계획 확인</button>`:['draft','invalidated'].includes(f.status)?`<button class="primary" data-action="organize" ${offline()}>내용 정리</button>`:''}</div>${fresh?`<p class="subtle">예시 명세 ${state.spec?`v${f.version} 저장`:'미저장'} · 예시 실행 ${state.flow.run?1:0}건</p>`:''}${binding()}`;
}
function offline(){return state.scenario==='offline'?'disabled':'';}
function taskButton(t){return `<button class="task" data-task="${t.id}"><span class="status ${t.kind}">${t.status}</span><strong>${esc(t.title)}</strong><span class="owner">${esc(t.role)} · ${esc(t.model)}</span></button>`;}
function work(){const t=tasks();return `<div class="parallel">${complete()?'두 역할의 결과가 모였습니다':'서로 기다리지 않는 두 업무'}</div><ol class="task-flow"><li class="parallel-item">${taskButton(t[0])}</li><li class="parallel-item">${taskButton(t[1])}</li><li class="merge"><span class="dependency"><button data-action="dependency">${complete()||isNew()&&state.flow.playback==='checks'?'두 결과 전달 완료':'선행 조건 · 화면 변경 + 검사 코드'}</button></span>${taskButton(t[2])}</li></ol>${!complete()&&state.scenario==='quota'?'<div class="banner"><strong>모든 후보 대기</strong><p>예약은 유지합니다. 새 모델 호출 없이 기다리며 다른 실행의 보고·승인은 읽을 수 있습니다.</p></div>':''}${!complete()&&state.scenario==='failure'?'<div class="banner"><strong>자동 재시도 중단</strong><p>예시 실패 상한 3회. 원인 조사는 별도 항목이며 추가 개발 승인으로 간주하지 않습니다.</p><button data-action="investigation">조사 보기</button></div>':''}`;}
function team(){return `<ul class="team-list">${tasks().slice(0,2).map(t=>`<li><div class="team-line"><h3>${esc(t.role)}</h3><span class="status ${t.kind}">${t.status}</span></div><p>${esc(t.title)}</p><strong>${esc(t.model)}</strong><p>요청 설정 · ${t.model==='Astra'?'High':'후보 기본값'} / 실제 적용 미확인</p><button data-task="${t.id}">업무·담당 보기</button></li>`).join('')}<li><h3>검수</h3><p>통합 검사 → 독립 검수 → Astra Ultra 최종 검수</p><p>같은 통합 업무 안의 단계입니다. 새 업무 세 개로 세지 않습니다.</p></li></ul>`;}
function history(){
 if(isNew())return `<ol class="events">${state.events.filter(e=>e.project===projectId()).map(e=>`<li><h3>${esc(({pm_reply:'PM 답변 보관',spec_saved:'명세 저장',plan_requested:'계획 요청',plan_ready:'계획 도착',plan_failed:'계획 준비 실패',plan_review_invalidated:'내용 변경 · 다시 검토',plan_confirmed:'직접 계획 확정',run_created:'예시 실행 생성',play_wait:'개발 한도 대기',handoff:'개발 담당 변경',play_checks:'결과 전달 · 검사',play_review:'검수 완료',decision:'예시 후보 결정'})[e.type]||e.type)}</h3><p>명세 v${e.specVersion} · ${esc(e.run||'실행 없음')}</p>${e.type==='handoff'?'<p>Astra → Claude · 업무 ID, 공유 한도·누적 사용량 유지</p>':''}</li>`).join('')}</ol>`;
 return `<ol class="events">${state.handoff&&!oldRun()?`<li><time>예시 10:20</time><h3>개발 담당 변경</h3><p>Astra → Claude · 같은 업무 ID 유지. 공유 계정 한도와 누적 사용량은 유지합니다.</p><button data-task="${runId()}-task-screen">이관 근거</button></li>`:''}<li><time>예시 10:12</time><h3>${oldRun()?'검수 기록 도착':state.scenario==='quota'?'검사도 한도 대기':'검사 코드 작성 중'}</h3><p>${oldRun()?'검사·독립 검수·최종 검수의 예시 결과를 연결했습니다.':state.scenario==='quota'?'모든 적격 후보 대기. 사용 가능할 때 예약 재개합니다.':'개발 계정 대기와 무관하게 독립 검사는 계속합니다.'}</p></li><li><time>예시 10:00</time><h3>계획 확정</h3><p>고정 계획에 예시 실행 1건을 연결했습니다.</p><button data-action="plan-record">원문·근거</button></li></ol>`;
}
function playback(){
 const p=state.flow.playback,step={parallel:['play-wait','한도 대기 재생'],waiting:['handoff','담당 이관 재생'],handoff:['play-checks','결과 전달 재생'],checks:['play-review','검수 완료 재생']}[p];
 return `<section class="playback" aria-label="예시 진행"><h3>단계 재생</h3><p>검수용 모의 진행입니다. 실제 개발·모델 호출·CI는 실행하지 않습니다.</p>${step?`<button data-action="${step[0]}" ${offline()}>${step[1]}</button>`:'<button data-stage="approval">후보 검토</button>'}</section>`;
}
function progress(){
 if(isNew()&&!state.confirmed)return `<h2>진행</h2><div class="empty"><h3>아직 시작하지 않았어요</h3><p>PM의 제안을 검토하고 직접 계획을 확정해야 합니다.</p><button data-stage="plan">계획 보기</button></div>${binding()}`;
 return `<h2>진행</h2><div class="run-meta"><span>${isNew()?'이번 예시 실행':oldRun()?'1차 · 검수 완료, 후보 결정 대기':'2차 · 개발과 검사'}</span></div><nav class="tabs" aria-label="진행 보기">${[['work','업무'],['team','팀'],['history','기록']].map(([id,label])=>`<button data-tab="${id}" aria-pressed="${state.tab===id}">${label}</button>`).join('')}</nav>${state.tab==='team'?team():state.tab==='history'?history():work()}${isNew()?playback():''}${binding()}`;
}
function approval(){
 const r=currentRequest(),others=projectRequests().filter(r=>r.run!==runId()),decision=r&&state.decisions[r.approval];
 return `<h2>승인</h2>${r?`<p class="meta">후보 · ${r.approval} · pending</p><p class="summary-line">로그인 화면 변경을<br>검토해 주세요.</p><p>같은 후보의 검사·검수 예시 기록을 읽은 뒤 판단합니다. 계획 확정과 후보 수용은 별도 결정입니다.</p><div class="actions"><button class="primary" data-action="decision">후보 검토</button><button data-stage="result">검수 근거</button></div>${decision?`<div class="banner"><strong>예시 선택 · ${decisionLabels[decision.choice]}</strong><p>${decisionNext[decision.choice]}</p>${decision.reason?`<p>의견: ${esc(decision.reason)}</p>`:''}<p>원래 후보 pending 유지</p></div>`:''}`:'<div class="empty"><h3>이 실행의 요청은 없어요</h3><p>아직 이 실행의 후보 승인 요청이 없습니다.</p><button data-stage="progress">진행 보기</button></div>'}${others.length?`<section class="pending-requests"><h3>확인할 일 ${others.length}건</h3><p>이 프로젝트의 다른 실행에 요청이 남아 있습니다.</p>${others.map(requestLink).join('')}</section>`:''}${binding()}`;
}
function result(){
 const done=complete(),started=!isNew()||state.confirmed,r=currentRequest(),decision=r&&state.decisions[r.approval];
 return `<h2>결과</h2><p class="meta">시스템 집계 · 예시 저장 기록</p><p class="summary-line">${!started?'아직 결과가 없어요.':decision?`예시 ${decisionLabels[decision.choice]}을 기록했습니다.`:done?'검수는 끝났고,<br>후보 결정을 기다립니다.':'진행 중입니다.<br>완료로 보고하지 않습니다.'}</p><div class="report-grid"><section><h3>완료</h3><p>${!started?'없음':done?'두 업무의 결과와 검사·독립 검수·최종 검수 예시 기록':'계획 확정 · 역할별 업무 배정'}</p>${done?'<button data-action="artifact">전달물 보기</button>':''}</section><section><h3>남은 일</h3><p>${decision?decisionNext[decision.choice]:!started?'계획 확인 후 직접 시작':done?'마스터의 후보 검토. 배포·병합은 포함하지 않습니다.':'개발·검사 결과 수집과 같은 후보 검수'}</p></section><section><h3>차단</h3><p>${!started?'아직 실행하지 않음':done?'자동 진행 없음':state.scenario==='failure'?'자동 재시도 상한에 도달':state.scenario==='quota'?'모든 적격 후보 한도 대기':transferred()?'개발 이관 후 진행 중':isNew()&&state.flow.playback==='parallel'?'없음 · 두 역할 진행':'개발 계정 한도 대기'}</p></section><section><h3>내 결정</h3><p>${r?decision?`예시 ${decisionLabels[decision.choice]} · 원래 요청 pending`:'후보 검토 1건':'이 실행의 후보 요청 없음'}</p><button data-stage="approval">승인 보기</button></section></div><hr class="divider"><p class="meta">PM 작성 · 예시 설명</p><p>${done?'목표를 충족했는지 근거를 확인해 주세요. 검수 통과가 배포 승인을 대신하지 않습니다.':'독립적으로 할 수 있는 일은 계속합니다. 기다리는 업무는 범위와 예산을 유지합니다.'}</p><details><summary>원문</summary><p>처음부터 한국어로 작성한 고정 예시입니다. 번역이나 실제 PM의 결과가 아닙니다.</p></details>${binding()}`;
}
function render(){
 const before=document.activeElement;
 const id=before?.id;
 const attribute=['data-task','data-action','data-tab','data-stage','data-approval'].find(a=>before?.hasAttribute(a));
 const value=attribute?before.getAttribute(attribute):null;
 draw();
 if(before&&!before.isConnected){
   const restored=id?document.getElementById(id):attribute?Array.from(document.querySelectorAll(`[${attribute}]`)).find(e=>e.getAttribute(attribute)===value):null;
   (restored||$('#main'))?.focus();
 }
}
function draw(){
 document.documentElement.dataset.theme=state.theme;$('#theme').textContent=state.theme==='light'?'Black':'Light';$('#theme').ariaLabel=`${state.theme==='light'?'Black':'Light'} 테마로 전환`;
 document.querySelectorAll('[data-layout]').forEach(b=>b.setAttribute('aria-pressed',b.dataset.layout===state.layout));$('#scenario').value=state.scenario;
 if(state.stage==='projects'){$('#app').innerHTML=banner()+projects();return;}
 if(isNew()&&!state.created){state.stage='projects';save();render();notify('프로젝트를 찾을 수 없어요. 목록에서 다시 선택해 주세요.');return;}
 const body=({plan,progress,approval,result}[state.stage]||progress)();const n=next();
 $('#app').innerHTML=banner()+(state.layout==='journey'?`${head()}${nav()}<div class="journey-body"><main id="main" class="content" tabindex="-1">${runPicker()}${body}</main>${rail()}</div>`:`<div class="overview-shell"><aside>${head()}${nav()}</aside><div><section class="overview-next"><div><div class="next-label">지금</div><div class="next">${n[0]}</div><p>${n[1]}</p></div><button data-stage="${n[2]}">${n[3]}</button></section><div class="overview-board"><main id="main" class="content" tabindex="-1">${runPicker()}${body}</main>${rail()}</div></div></div>`);
}
function showTask(id){const t=tasks().find(t=>t.id===id);if(!t)return;
let extra=id===runId()+'-task-integration'?`<h3>검수 단계</h3><ol class="plain-list"><li>격리 검사 · ${complete()?'예시 통과':isNew()&&state.flow.playback==='checks'?'예시 검수 중':'아직 시작 전'}</li><li>원격 CI · ${complete()?'예시 통과':isNew()&&state.flow.playback==='checks'?'예시 검수 중':'아직 시작 전'}</li><li>독립 검수 · ${complete()?'예시 통과':isNew()&&state.flow.playback==='checks'?'예시 검수 중':'아직 시작 전'}</li><li>Astra Ultra 최종 검수 · ${complete()?'예시 통과':isNew()&&state.flow.playback==='checks'?'예시 검수 중':'아직 시작 전'}</li></ol><p>위 단계는 같은 통합 업무에 속합니다. 예시 성공은 실제 모델·CI 증거가 아닙니다.</p>`:`<dl><dt>역할</dt><dd>${esc(t.role)}</dd><dt>요청 모델</dt><dd>${esc(t.model)} · ${t.model==='Astra'?'High':'후보 기본값'}</dd><dt>관측 설정</dt><dd>미확인 · 모델의 자기 설명으로 확인하지 않음</dd></dl>`;
open(t.title,`<span class="status ${t.kind}">${t.status}</span><p>${t.reason}</p><p class="subtle">업무 ${t.id} · ${runId()}</p>${extra}${id===runId()+'-task-screen'?`<details ${transferred()?'open':''}><summary>담당 이력</summary><p>${transferred()?'Astra → Claude · 예시 10:20 · 계정 한도 대기 후 이관. 같은 업무 ID, 새 담당 실행.':'Astra 배정 · 이관 기록 없음'}</p><p>구체적 이관 이유는 기록으로 확인해야 합니다. 공유 한도·누적 사용량을 초기화하지 않습니다.</p></details>${!oldRun()&&!transferred()&&(!isNew()||state.flow.playback==='waiting')&&!['quota','failure'].includes(state.scenario)?`<button data-action="handoff" ${offline()}>이관 예시 보기</button>`:''}`:''}${complete()||isNew()&&state.flow.playback==='checks'?`<h3>전달물</h3><p>예시 후보 <code>${candidateId()}</code>에 연결된 결과입니다.</p><button data-action="artifact">전달 근거</button>`:'<p class="subtle">아직 결과 전달 기록이 없습니다. 예정 관계를 실제 전달로 표시하지 않습니다.</p>'}`);
}
function organize(retry=false){
 if(!isNew()||!state.created.example||state.confirmed||state.flow.status==='preparing')return;
 if(!retry){state.flow.version++;state.spec=true;record('spec_saved');}
 state.plan=false;state.flow.status='preparing';record('plan_requested');notify('내용을 정리했습니다. 예시 계획 응답을 재생해 주세요. 실행 0건');
}
function decisionControls(){const choice=$('#decision-choice')?.value,reason=$('#decision-reason')?.value.trim();const button=$('[data-action="decision-submit"]');if(button)button.disabled=!$('#decision-reviewed')?.checked||!decisionLabels[choice]||choice==='request_changes'&&!reason;}
function action(name){
 if(name==='legacy-record'&&state.legacyPreview){const old=state.legacyPreview;open('이전 시안 기록',`<p>이전 버전에서 보관한 표시입니다. 새로운 계획 확정이나 후보 결정을 뜻하지 않습니다.</p><dl><dt>프로젝트</dt><dd>${esc(old.created?.name||'없음')}</dd><dt>목표</dt><dd>${esc(old.created?.goal||'없음')}</dd><dt>명세</dt><dd>${old.spec?'저장됨':'없음'}</dd><dt>계획</dt><dd>${old.plan?'요청됨':'없음'}</dd><dt>확정 표시</dt><dd>${old.confirmed?'있음 · 신규 실행 없음':'없음'}</dd><dt>이전 선택</dt><dd>${old.decision?'보관됨 · 이전 시안에는 결정 종류가 없었습니다.':'없음'}</dd>${old.decision?`<dt>승인 대상</dt><dd>${esc(old.decision.approval)}</dd><dt>실행</dt><dd>${esc(old.decision.run)}</dd><dt>대상 digest</dt><dd><code>${esc(old.decision.digest)}</code></dd>`:''}</dl>`);return;}
 if(['new','use-example','organize','plan-ready','plan-fail','retry-plan','edit-plan','confirm','confirm-submit','handoff','play-wait','play-checks','play-review','decision-submit'].includes(name)&&state.scenario==='offline')return notify('연결 후 다시 확인해 주세요.');
 if(name==='new'&&state.created){state.project='new';setStage('plan');notify('이 시안은 새 프로젝트 1개만 제공합니다. 기존 입력과 확정 기록을 이어서 보여 드립니다.');return;}
 if(name==='new'){open('새 프로젝트',`<p class="subtle">예시 입력만 이 탭에 보관합니다. 개인 정보나 비밀을 넣지 마세요.</p><button data-action="use-example">로그인 예시 목표 사용</button><form id="new-form"><div><label for="name">이름</label><input id="name" required maxlength="100" value="${esc(state.draft.name)}"><label for="goal">목표</label><textarea id="goal" required maxlength="2000" placeholder="어떤 일을 해 보고 싶나요?">${esc(state.draft.goal)}</textarea><p class="subtle">전체 체험은 위 버튼으로 선택한 고정 로그인 목표만 지원합니다. 자유 입력은 보관만 합니다.</p><div class="actions"><button class="primary" type="submit" ${offline()}>PM과 시작</button></div></div></form>`);return;}
 if(name==='use-example'){
  if(state.confirmed)return;
  state.draft.example=true;state.draft.goal=exampleGoal;
  if($('#new-form')){$('#goal').value=exampleGoal;if(!$('#name').value){state.draft.name='나의 로그인 체험';$('#name').value=state.draft.name;}save();notify('고정 로그인 예시 목표를 선택했습니다.');return;}
  if(!isNew()||!state.created)return;
  state.created={...state.created,originalGoal:state.created.originalGoal||state.created.goal,goal:exampleGoal,example:true};
 }
 if(name==='organize')organize();
 if(name==='retry-plan'&&state.flow.status==='failed')organize(true);
 if(['plan-ready','plan-fail'].includes(name)&&isNew()&&state.flow.status==='preparing'){
  state.plan=name==='plan-ready';state.flow.status=state.plan?'ready':'failed';record(state.plan?'plan_ready':'plan_failed');
  notify(state.plan?'계획이 준비됐습니다. 범위와 한도를 확인해 주세요. 실행 0건':'계획을 준비하지 못했습니다. 다시 시도해 주세요. 실행 0건');
 }
 if(name==='edit-plan'){
  if(!isNew()||!state.created.example||state.confirmed)return;
  open('내용 수정',`<p>내용을 바꾸면 이전 계획 검토는 무효가 됩니다.</p><form id="plan-edit-form"><div><label for="scope">범위</label><textarea id="scope" required maxlength="2000">${esc(state.flow.scope)}</textarea><label for="criteria">완료 조건</label><textarea id="criteria" required maxlength="2000">${esc(state.flow.criteria)}</textarea><button class="primary" type="submit">내용 반영</button></div></form>`);return;
 }
 if(name==='confirm'){
  if(!isNew()||state.flow.status!=='ready'||state.confirmed)return;
  open('계획 확인',`<p>${esc(projectName())}</p>${planDetails()}${binding()}<label><input type="checkbox" id="reviewed">범위·완료 조건·한도를 읽었습니다. 실제 실행 대신 예시 1건을 시작합니다.</label><button class="primary" data-action="confirm-submit" data-version="${state.flow.version}" disabled>이 계획으로 시작</button>`);return;
 }
 if(name==='confirm-submit'){
  if(!$('#reviewed')?.checked||!isNew()||!state.spec||!state.plan||state.confirmed||state.flow.status!=='ready'||Number($('[data-action="confirm-submit"]')?.dataset.version)!==state.flow.version)return;
  record('plan_confirmed',{run:'example-new-run-01'});state.flow.run={id:'example-new-run-01',project:projectId(),plan:planId(),planDigest:digest(),specVersion:state.flow.version,scope:state.flow.scope,criteria:state.flow.criteria};
  state.confirmed=true;state.flow.status='confirmed';state.flow.playback='parallel';record('run_created');state.stage='progress';$('#detail').close();notify('예시 실행 1건 · 실제 모델 호출 없음');
 }
 if(name==='handoff'&&['quota','failure'].includes(state.scenario))return;
 if(name==='handoff'){
  if(isNew()){if(!state.confirmed||state.flow.playback!=='waiting')return;state.flow.playback='handoff';record('handoff',{task:runId()+'-task-screen',from:'Astra',to:'Claude'});}else {if(oldRun())return;state.handoff=true;}
  const wasOpen=$('#detail').open;save();if(wasOpen)$('#detail').close();render();if(wasOpen){document.querySelector(`[data-task="${runId()}-task-screen"]`)?.focus();showTask(runId()+'-task-screen');}return;
 }
 const playbackSteps={'play-wait':['parallel','waiting','play_wait'],'play-checks':['handoff','checks','play_checks'],'play-review':['checks','review','play_review']};
 if(playbackSteps[name]){const [from,to,event]=playbackSteps[name];if(!isNew()||!state.confirmed||state.flow.playback!==from||['quota','failure'].includes(state.scenario))return;state.flow.playback=to;record(event,{candidate:to==='checks'||to==='review'?candidateId():null});}
 if(name==='dependency'){const delivered=complete()||isNew()&&state.flow.playback==='checks';open('선행 조건',`<p>화면 변경과 검사 코드가 모두 있어야 같은 후보를 검수합니다.</p><ul class="plain-list"><li>로그인 화면 정리 → 같은 후보 검수</li><li>오류 상황 검사 작성 → 같은 후보 검수</li></ul><p>${delivered?'두 결과가 예시 후보에 연결됐습니다.':'예정된 의존관계입니다. 결과가 전달됐다는 뜻은 아닙니다.'}</p><p class="subtle">${runId()}</p>${delivered?'<button data-action="artifact">전달 근거</button>':''}`);return;}
 if(name==='artifact'){if(!complete()&&!(isNew()&&state.flow.playback==='checks'))return;open('전달 근거',`<p>예시 결과 2건 → 통합 검수</p><dl><dt>프로젝트</dt><dd>${projectId()}</dd><dt>실행</dt><dd>${runId()}</dd><dt>후보</dt><dd>${candidateId()}</dd><dt>상태</dt><dd>전달 완료 · 예시 이벤트</dd></dl><p>로그인 화면 변경과 오류 검사 코드를 같은 후보에 연결한 고정 예시입니다. 실제 파일 다운로드나 원격 CI 결과는 없습니다.</p>`);return;}
 if(name==='plan-record'){open('계획 기록',`${binding()}<p>과거 확정 이벤트의 예시입니다. 현재의 다른 계획이나 추가 범위를 승인하지 않습니다.</p>`);return;}
 if(name==='investigation'){open('실패 조사','<p>예시 조사 항목 · 반복된 검사 실패의 원인을 확인합니다.</p><p>원인 조사는 범위를 늘리는 개발 승인과 별개입니다. 예산·시간·실패 상한은 유지하고 마스터의 새 결정 없이 반복하지 않습니다.</p>');return;}
 if(name==='decision'){
  const r=currentRequest();if(!r)return;
  open('후보 검토',`<p>로그인 화면 변경과 검사 결과 · 예시 후보입니다.</p><details class="binding"><summary>대상 근거</summary><dl>${Object.entries({프로젝트:r.project,계획:r.plan,'계획 digest':r.planDigest,실행:r.run,'승인 대상':r.approval,후보:r.candidate,'대상 digest':r.digest}).map(([k,v])=>`<dt>${k}</dt><dd><code>${esc(v)}</code></dd>`).join('')}</dl></details><p>배포·병합 권한은 포함하지 않습니다. 원래 요청은 pending으로 유지됩니다.</p><label for="decision-choice">내 결정</label><select id="decision-choice"><option value="">선택해 주세요</option><option value="approve">수용</option><option value="request_changes">수정 요청</option><option value="hold">보류 · 이 탭에서 나중에 보기</option></select><label for="decision-reason">의견 <small>수정 요청 시 필수</small></label><textarea id="decision-reason" maxlength="2000"></textarea><p class="subtle">보류는 제품의 승인 결정이 아닌 시안의 개인 표시입니다. 수정 요청은 자동 재실행하지 않습니다.</p><label><input type="checkbox" id="decision-reviewed">같은 실행의 후보와 근거를 확인했습니다.</label><button data-action="decision-submit" data-approval-id="${r.approval}" disabled>예시 선택 기록</button>`);return;
 }
 if(name==='decision-submit'){
  const r=currentRequest(),choice=$('#decision-choice')?.value,reason=$('#decision-reason')?.value.trim()||'';
  if(!r||$('[data-action="decision-submit"]')?.dataset.approvalId!==r.approval||!$('#decision-reviewed')?.checked||!decisionLabels[choice]||choice==='request_changes'&&!reason)return;
  const snapshot={...r,choice,reason,scope:'preview_only',decisionKind:choice==='hold'?'local_defer':'candidate_review'};
  state.decisions[r.approval]=snapshot;state.decision=snapshot;record('decision',{...snapshot});$('#detail').close();notify(`예시 ${decisionLabels[choice]} 기록 · 원래 후보 pending 유지`);
 }
 if(name==='reconnect'){state.scenario='normal';notify('예시 연결을 복구했습니다. 운영 서버에는 연결하지 않습니다.');}
 save();render();
 const nextAction={organize:'plan-ready','retry-plan':'plan-ready','plan-ready':'confirm','play-wait':'handoff','play-checks':'play-review'}[name];
 if(nextAction)document.querySelector(`[data-action="${nextAction}"]`)?.focus();
}
$('#theme').onclick=()=>{state.theme=state.theme==='light'?'black':'light';save();render();};
$('#scenario').onchange=e=>{state.scenario=e.target.value;save();render();};
$('#reset').onclick=()=>{if($('#detail').open)$('#detail').close();state=freshState();save();render();notify('이 탭의 입력·계획·실행·예시 결정을 초기화했습니다.');};
document.addEventListener('click',e=>{
 if(e.target.closest('.brand')){e.preventDefault();setStage('projects');return;}
 const b=e.target.closest('button');if(!b||b.disabled)return;
 if(b.dataset.layout){state.layout=b.dataset.layout;save();render();}
 if(b.dataset.stage)setStage(b.dataset.stage);
 if(b.dataset.project){state.project=b.dataset.project;state.run='current';setStage(isNew()?'plan':'progress');}
 if(b.dataset.approval)openRequest(b.dataset.approval);
 if(b.dataset.tab){state.tab=b.dataset.tab;save();render();$('#main')?.focus();}
 if(b.dataset.task)showTask(b.dataset.task);
 if(b.dataset.action)action(b.dataset.action);
});
document.addEventListener('change',e=>{
 if(e.target.id==='run'){state.run=e.target.value;save();render();}
 if(e.target.id==='reviewed')$('[data-action="confirm-submit"]').disabled=!e.target.checked;
 if(['decision-reviewed','decision-choice'].includes(e.target.id))decisionControls();
});
document.addEventListener('input',e=>{
 if(['name','goal','pm-reply'].includes(e.target.id)){state.draft[e.target.id==='pm-reply'?'reply':e.target.id]=e.target.value;if(e.target.id==='goal')state.draft.example=state.draft.example&&e.target.value===exampleGoal;save();}
 if(e.target.id==='decision-reason')decisionControls();
});
document.addEventListener('submit',e=>{
 if(e.target.id==='new-form'){
  e.preventDefault();if(state.created||state.scenario==='offline'||!e.target.reportValidity())return;
  const name=$('#name').value.trim(),goal=$('#goal').value.trim();if(!name||!goal)return;
  state.created={name,goal,example:state.draft.example&&goal===exampleGoal};state.project='new';state.spec=false;state.plan=false;state.confirmed=false;state.flow=freshState().flow;state.stage='plan';$('#detail').close();save();render();
 }
 if(e.target.id==='pm-form'){e.preventDefault();if(state.scenario==='offline'||state.confirmed)return;state.draft.sent=true;record('pm_reply');save();render();notify('고정 예시의 답변 보관 · 실제 PM 호출 없음');}
 if(e.target.id==='plan-edit-form'){
  e.preventDefault();if(state.scenario==='offline'||state.confirmed||!e.target.reportValidity())return;
  const scope=$('#scope').value.trim(),criteria=$('#criteria').value.trim();if(!scope||!criteria)return;
  if(scope!==state.flow.scope||criteria!==state.flow.criteria){state.flow.scope=scope;state.flow.criteria=criteria;state.flow.status='invalidated';state.plan=false;record('plan_review_invalidated');notify('내용이 바뀌었습니다. 다시 정리한 계획을 확인해 주세요. 실행 0건');}
  $('#detail').close();save();render();
 }
});
window.addEventListener('hashchange',()=>{if(location.hash==='#projects')setStage('projects');});
render();
})();
