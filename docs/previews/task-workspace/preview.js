/* Isolated design fixture. No fetch, API, workers, approval writes or model calls. */
'use strict';
(() => {
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const key = 'ai-company:task-design:1';
const initial = {layout:'journey',theme:'light',stage:'projects',tab:'work',scenario:'normal',project:'sample',run:'current',draft:{name:'',goal:''},created:null,spec:false,plan:false,confirmed:false,handoff:false,decision:null};
let state;
try { state = {...initial,...JSON.parse(sessionStorage.getItem(key) || '{}')}; } catch { state = structuredClone(initial); }
let opener = null;
const steps = [['projects','프로젝트'],['plan','계획'],['progress','진행'],['approval','승인'],['result','결과']];
const isNew = () => state.project === 'new';
const runId = () => isNew() ? (state.confirmed ? 'example-new-run-01' : null) : state.run === 'current' ? 'example-run-02' : 'example-run-01';
const planId = () => isNew() ? 'example-plan-new' : state.run === 'current' ? 'example-plan-02' : 'example-plan-01';
const digest = () => (isNew() ? 'c' : state.run === 'current' ? 'b' : 'a').repeat(64);
const oldRun = () => !isNew() && state.run === 'previous';
const projectName = () => isNew() ? state.created?.name || '새 프로젝트' : state.scenario === 'long' ? '모바일 로그인 화면을 더 쉽고 읽기 편하게 정리하고 한국어 안내와 오류 복구를 확인하는 프로젝트' : '모바일 로그인 정리';
const goal = () => isNew() ? state.created?.goal || '' : '휴대폰에서 편하게 로그인하고, 오류가 나도 다음 행동을 알 수 있게 해 주세요.';
const tasks = () => {
 const handoff=state.handoff&&!oldRun();
 const done=oldRun(), quota=state.scenario==='quota', failure=state.scenario==='failure';
 return [
 {id:runId()+'-task-screen',title:state.scenario==='long'?'긴 한국어와 오류 안내가 잘리는 문제를 고치고 로그인 화면을 읽기 편하게 정리하기':'로그인 화면 정리',role:'화면 개발',model:handoff?'Claude':'Astra',status:done?'완료':failure?'판단 필요':handoff&&!quota?'진행':'한도 대기',kind:done?'done':failure?'blocked':handoff&&!quota?'active':'waiting',reason:done?'후보에 반영됨':failure?'예시 실패 상한 3회에 도달했습니다.':handoff&&!quota?'승인된 대체 후보로 같은 일을 이어갑니다.':'개발 계정의 사용 가능 시각을 기다립니다.'},
 {id:runId()+'-task-tests',title:'오류 상황 검사 작성',role:'독립 검사',model:'Claude',status:done?'완료':quota?'한도 대기':'진행',kind:done?'done':quota?'waiting':'active',reason:done?'검사 결과 전달됨':quota?'모든 적격 후보가 대기 중입니다.':'개발과 독립적으로 검사 코드를 작성합니다.'},
 {id:runId()+'-task-integration',title:'같은 후보 검수',role:'검사 · 독립 검수 · 최종 검수',model:'단계별 담당',status:done?'완료':'선행 대기',kind:done?'done':'waiting',reason:done?'검사와 두 검수의 예시 기록이 있습니다.':'화면 변경과 검사 코드가 모두 필요합니다.'}
 ];
};
function save(){try{sessionStorage.setItem(key,JSON.stringify(state));}catch{}}
function notify(message){$('#notice').textContent=message;}
function setStage(stage){state.stage=stage;save();render();$('#main')?.focus();}
function open(title,body){if(!$('#detail').open)opener=document.activeElement;$('#detail-body').innerHTML=`<h2 id="detail-title">${esc(title)}</h2>${body}`;$('#detail').showModal();}
$('#detail').addEventListener('close',()=>{if(opener?.isConnected)opener.focus();});
function binding(){return `<details class="binding"><summary>근거</summary><dl><dt>프로젝트</dt><dd>${isNew()?'example-project-new':'example-project-login'}</dd><dt>실행</dt><dd>${runId()||'없음 · 아직 시작하지 않음'}</dd><dt>계획</dt><dd>${planId()}</dd><dt>계획 digest</dt><dd><code>${digest()}</code></dd></dl><p>식별자와 기록은 모두 예시입니다. 요약·번역·과거 기록은 현재의 실행 승인으로 쓰지 않습니다.</p></details>`;}
function nav(){return `<nav class="stage-nav" aria-label="작업 단계">${steps.map(([id,label],i)=>`<button data-stage="${id}" ${state.stage===id?'aria-current="page"':''}><span>0${i+1}</span>${label}</button>`).join('')}</nav>`;}
function next(){
 if(isNew())return state.confirmed?['작업 시작 대기','예시 확정은 저장됐습니다. 실제 작업은 실행하지 않습니다.','progress','진행 보기']:!state.spec?['PM 제안 확인','목표와 범위, 완료 조건을 함께 정하세요.','plan','제안 보기']:!state.plan?['새 계획 요청','저장한 명세로 계획을 다시 만듭니다.','plan','계획 보기']:['계획 확인','명세 저장만으로 작업이 시작되지는 않습니다.','plan','계획 보기'];
 if(oldRun())return ['후보를 검토해 주세요','같은 실행의 검사 근거와 변경 범위를 읽어 보세요.','approval','후보 보기'];
 if(state.scenario==='failure')return ['실패 원인 검토','추가 개발 범위와 재시도는 별도 결정이 필요합니다.','progress','업무 보기'];
 if(state.scenario==='quota')return ['예약 재개 대기','모든 적격 후보가 대기 중입니다. 보고와 승인은 계속 읽을 수 있습니다.','progress','대기 보기'];
 return [state.handoff?'개발과 검사 진행 중':'개발 재개 대기',state.handoff?'같은 업무를 대체 담당자가 이어갑니다.':'독립 검사는 계속 진행 중입니다.','progress','업무 보기'];
}
function rail(){let n=next();return `<aside class="rail" aria-label="요약"><section><div class="next-label">다음</div><div class="next">${n[0]}</div><p>${n[1]}</p><button data-stage="${n[2]}">${n[3]}</button></section><section><h3>내 결정</h3><p>${oldRun()?'후보 검토 1건':isNew()&&state.plan&&!state.confirmed?'계획 확인 1건':'현재 실행의 요청 없음'}</p><button data-stage="${isNew()&&state.plan&&!state.confirmed?'plan':'approval'}">${isNew()&&state.plan&&!state.confirmed?'계획 검토':'승인 보기'}</button></section><section><h3>기록</h3><p>PM의 설명과 시스템 사실을 나눠 읽습니다.</p><button data-stage="result">결과 보기</button></section></aside>`;}
function runPicker(){return isNew()?'':`<label class="run-picker">실행 <select id="run"><option value="current" ${state.run==='current'?'selected':''}>현재 · 2차 작업</option><option value="previous" ${state.run==='previous'?'selected':''}>이전 · 1차 작업</option></select></label>`;}
function head(){return `<div class="project-head"><div><button class="back" data-stage="projects">← 프로젝트</button><h1>${esc(projectName())}</h1><p class="subtle">${esc(goal())}</p></div></div>`;}
function banner(){return state.scenario==='offline'?'<div class="banner" role="alert"><strong>연결 끊김</strong><p>마지막 예시 기록입니다. 현재 상태를 확인하지 못했어요. 확인·저장 조작은 잠깐 멈춥니다.</p><button data-action="reconnect">다시 연결</button></div>':'';}
function projects(){return `<main id="main" class="projects" tabindex="-1"><div class="projects-top"><h1>프로젝트</h1><button class="primary" data-action="new">새 프로젝트</button></div><p>이름과 목표로 PM과 시작하세요.</p>${state.scenario==='empty'&&!state.created?'<div class="empty"><h2>아직 없어요</h2><p>해 보고 싶은 일을 편하게 적어 주세요.</p></div>':`<ul class="project-list">${state.scenario!=='empty'?`<li><button data-project="sample"><span><strong>모바일 로그인 정리</strong><small>${state.scenario==='quota'?'모든 적격 후보 대기':state.scenario==='failure'?'개발 실패 원인 검토 · 독립 검사 진행 중':state.handoff?'개발과 독립 검사 진행 중':'개발 재개 대기 · 독립 검사 진행 중'}</small></span><span class="arrow" aria-hidden="true">↗</span></button></li>`:''}${state.created?`<li><button data-project="new"><span><strong>${esc(state.created.name)}</strong><small>${state.confirmed?'예시 확정됨 · 실제 실행 없음':'PM 제안 확인'}</small></span><span class="arrow" aria-hidden="true">↗</span></button></li>`:''}</ul>`}<p class="readonly">시안의 가상 프로젝트입니다. 운영 프로젝트나 승인 기록을 읽지 않습니다.</p></main>`;}
function plan(){
 let fresh=isNew(),confirmed=fresh?state.confirmed:true;
 return `<h2>계획</h2><p class="meta">PM 제안 · 예시 응답, 모델 호출 없음</p><p class="pm-quote">${fresh?'먼저 어떤 상황이 가장 불편한지 알려 주세요. 목표에 맞춰 할 일과 완료 조건을 정리할게요.':'로그인 화면과 오류 검사를 나눠 진행하겠습니다. 두 결과가 모이면 같은 후보를 검사하고 검수합니다.'}</p>
 ${fresh?`<form id="pm-form"><label for="pm-reply">PM에게</label><textarea id="pm-reply" placeholder="예: 오류가 나면 어디를 눌러야 할지 모르겠어요.">${esc(state.draft.reply||'')}</textarea><button type="submit" ${offline()}>예시 답변</button></form>${state.draft.sent?'<p class="subtle">입력을 이 시안에 보관했습니다. 아래 제안은 미리 만든 예시입니다.</p>':''}`:''}
 <hr class="divider"><h3>할 일</h3><ul class="plain-list"><li>화면 개발 — 로그인 화면을 정리합니다.</li><li>독립 검사 — 오류 상황을 확인합니다. 개발과 병렬로 진행합니다.</li><li>통합 검수 — 같은 후보의 검사 → 독립 검수 → Astra 최종 검수.</li></ul>
 <h3>완료 조건</h3><p>휴대폰에서 글을 읽고 오류 다음 행동을 찾을 수 있어야 합니다. 검사·검수 근거를 한 실행에 연결합니다.</p>
 <details><summary>실행 명세</summary><p>프로젝트의 저장소·고정 커밋·허용 파일·검사 명령·모델 후보·예산을 실제 PM과 정할 자리입니다. 이 화면에는 실행 가능한 명세가 없습니다.</p><p>현재 운영의 pilot 두 파일 제한을 넓히지 않습니다. 명세 저장과 계획 확정은 다른 사건입니다.</p></details>
 <div class="actions">${confirmed?'<span class="status">예시 계획 확정됨</span>':!state.spec?`<button class="primary" data-action="save-spec" ${offline()}>명세 저장</button>`:!state.plan?`<button class="primary" data-action="request-plan" ${offline()}>새 계획 요청</button>`:`<button class="primary" data-action="confirm" ${offline()}>계획 검토</button>`}</div>${fresh?`<p class="subtle">예시 명세 ${state.spec?'v1 저장':'미저장'} · 예시 실행 ${state.confirmed?1:0}건</p>`:''}${binding()}`;
}
function offline(){return state.scenario==='offline'?'disabled':'';}
function taskButton(t){return `<button class="task" data-task="${t.id}"><span class="status ${t.kind}">${t.status}</span><strong>${esc(t.title)}</strong><span class="owner">${esc(t.role)} · ${esc(t.model)}</span></button>`;}
function work(){const t=tasks();return `<div class="parallel">${oldRun()?'두 역할의 결과가 모였습니다':'서로 기다리지 않는 두 업무'}</div><ol class="task-flow"><li class="parallel-item">${taskButton(t[0])}</li><li class="parallel-item">${taskButton(t[1])}</li><li class="merge"><span class="dependency"><button data-action="dependency">${oldRun()?'두 결과 전달 완료':'선행 조건 · 화면 변경 + 검사 코드'}</button></span>${taskButton(t[2])}</li></ol>${!oldRun()&&state.scenario==='quota'?'<div class="banner"><strong>모든 후보 대기</strong><p>예약은 유지합니다. 새 모델 호출 없이 기다리며 다른 실행의 보고·승인은 읽을 수 있습니다.</p></div>':''}${!oldRun()&&state.scenario==='failure'?'<div class="banner"><strong>자동 재시도 중단</strong><p>예시 실패 상한 3회. 원인 조사는 별도 항목이며 추가 개발 승인으로 간주하지 않습니다.</p><button data-action="investigation">조사 보기</button></div>':''}`;}
function team(){return `<ul class="team-list">${tasks().slice(0,2).map(t=>`<li><div class="team-line"><h3>${esc(t.role)}</h3><span class="status ${t.kind}">${t.status}</span></div><p>${esc(t.title)}</p><strong>${esc(t.model)}</strong><p>요청 설정 · ${t.model==='Astra'?'High':'후보 기본값'} / 실제 적용 미확인</p><button data-task="${t.id}">업무·담당 보기</button></li>`).join('')}<li><h3>검수</h3><p>통합 검사 → 독립 검수 → Astra Ultra 최종 검수</p><p>같은 통합 업무 안의 단계입니다. 새 업무 세 개로 세지 않습니다.</p></li></ul>`;}
function history(){return `<ol class="events">${state.handoff&&!oldRun()?`<li><time>예시 10:20</time><h3>개발 담당 변경</h3><p>Astra → Claude · 같은 업무 ID 유지. 공유 계정 한도와 누적 사용량은 유지합니다.</p><button data-task="${runId()}-task-screen">이관 근거</button></li>`:''}<li><time>예시 10:12</time><h3>${oldRun()?'검수 기록 도착':state.scenario==='quota'?'검사도 한도 대기':'검사 코드 작성 중'}</h3><p>${oldRun()?'검사·독립 검수·최종 검수의 예시 결과를 연결했습니다.':state.scenario==='quota'?'모든 적격 후보 대기. 사용 가능할 때 예약 재개합니다.':'개발 계정 대기와 무관하게 독립 검사는 계속합니다.'}</p></li><li><time>예시 10:00</time><h3>계획 확정</h3><p>고정 계획에 예시 실행 1건을 연결했습니다.</p><button data-action="plan-record">원문·근거</button></li></ol>`;}
function progress(){if(isNew())return `<h2>진행</h2><div class="empty"><h3>${state.confirmed?'시작 대기':'아직 시작하지 않았어요'}</h3><p>${state.confirmed?'예시 확정 기록만 저장했습니다. 실제 모델·개발·검사는 실행하지 않습니다.':'PM의 제안을 검토하고 직접 계획을 확정해야 합니다.'}</p><button data-stage="plan">계획 보기</button></div>${binding()}`;
return `<h2>진행</h2><div class="run-meta"><span>${oldRun()?'1차 · 검수 완료, 후보 결정 대기':'2차 · 개발과 검사'}</span><span>${runId()}</span></div><nav class="tabs" aria-label="진행 보기">${[['work','업무'],['team','팀'],['history','기록']].map(([id,label])=>`<button data-tab="${id}" aria-pressed="${state.tab===id}">${label}</button>`).join('')}</nav>${state.tab==='team'?team():state.tab==='history'?history():work()}${binding()}`;}
function approval(){return `<h2>승인</h2>${oldRun()?`<p class="meta">후보 · example-approval-01 · pending</p><p class="summary-line">로그인 화면 변경을<br>검토해 주세요.</p><p>같은 후보의 검사·검수 예시 기록을 읽은 뒤 판단합니다. 계획 확정과 후보 수용은 별도 결정입니다.</p><div class="actions"><button class="primary" data-action="decision">후보 검토</button><button data-stage="result">검수 근거</button></div>${state.decision?'<p class="banner">예시 선택만 기록했습니다. 원래 후보는 pending입니다.</p>':''}`:'<div class="empty"><h3>요청이 없어요</h3><p>이 실행의 후보 승인 요청은 아직 없습니다. 이전 실행의 요청을 여기에 섞지 않습니다.</p><button data-stage="progress">진행 보기</button></div>'}${binding()}`;}
function result(){return `<h2>결과</h2><p class="meta">시스템 집계 · 예시 저장 기록</p><p class="summary-line">${isNew()?'아직 결과가 없어요.':oldRun()?'검수는 끝났고,<br>후보 결정을 기다립니다.':'진행 중입니다.<br>완료로 보고하지 않습니다.'}</p><div class="report-grid"><section><h3>완료</h3><p>${isNew()?'없음':oldRun()?'두 업무의 결과와 검사·독립 검수·최종 검수 예시 기록':'계획 확정 · 역할별 업무 배정'}</p></section><section><h3>남은 일</h3><p>${isNew()?'실제 실행은 이 시안에서 하지 않습니다.':oldRun()?'마스터의 후보 검토. 배포·병합은 포함하지 않습니다.':'개발·검사 결과 수집과 같은 후보 검수'}</p></section><section><h3>차단</h3><p>${isNew()?'없음':oldRun()?'자동 진행 없음':state.scenario==='failure'?'자동 재시도 상한에 도달':state.scenario==='quota'?'모든 적격 후보 한도 대기':state.handoff?'개발 이관 후 진행 중':'개발 계정 한도 대기'}</p></section><section><h3>내 결정</h3><p>${oldRun()?'후보 검토 1건':'이 실행의 후보 요청 없음'}</p><button data-stage="approval">승인 보기</button></section></div><hr class="divider"><p class="meta">PM 작성 · 예시 설명</p><p>${oldRun()?'목표를 충족했는지 근거를 확인해 주세요. 검수 통과가 배포 승인을 대신하지 않습니다.':'독립적으로 할 수 있는 일은 계속합니다. 기다리는 업무는 범위와 예산을 유지합니다.'}</p><details><summary>원문</summary><p>읽기용 한국어와 원문을 함께 보존하는 자리입니다. 이 시안의 설명은 처음부터 한글로 만든 예시이며 번역 모델을 호출하지 않았습니다.</p></details>${binding()}`;}
function render(){
 const before=document.activeElement;
 const id=before?.id;
 const attribute=['data-task','data-action','data-tab','data-stage'].find(a=>before?.hasAttribute(a));
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
let extra=id===runId()+'-task-integration'?`<h3>검수 단계</h3><ol class="plain-list"><li>격리 검사 · ${oldRun()?'예시 통과':'아직 시작 전'}</li><li>원격 CI · ${oldRun()?'예시 통과':'아직 시작 전'}</li><li>독립 검수 · ${oldRun()?'예시 통과':'아직 시작 전'}</li><li>Astra Ultra 최종 검수 · ${oldRun()?'예시 통과':'아직 시작 전'}</li></ol><p>위 단계는 같은 통합 업무에 속합니다. 예시 성공은 실제 모델·CI 증거가 아닙니다.</p>`:`<dl><dt>역할</dt><dd>${esc(t.role)}</dd><dt>요청 모델</dt><dd>${esc(t.model)} · ${t.model==='Astra'?'High':'후보 기본값'}</dd><dt>관측 설정</dt><dd>미확인 · 모델의 자기 설명으로 확인하지 않음</dd></dl>`;
open(t.title,`<span class="status ${t.kind}">${t.status}</span><p>${t.reason}</p><p class="subtle">업무 ${t.id} · ${runId()}</p>${extra}${id===runId()+'-task-screen'?`<details ${state.handoff&&!oldRun()?'open':''}><summary>담당 이력</summary><p>${state.handoff&&!oldRun()?'Astra → Claude · 예시 10:20 · 계정 한도 대기 후 이관. 같은 업무 ID, 새 담당 실행.':'Astra 배정 · 이관 기록 없음'}</p><p>구체적 이관 이유는 기록으로 확인해야 합니다. 공유 한도·누적 사용량을 초기화하지 않습니다.</p></details>${!oldRun()&&!state.handoff&&!['quota','failure'].includes(state.scenario)?`<button data-action="handoff" ${offline()}>이관 예시 보기</button>`:''}`:''}${oldRun()?'<h3>전달물</h3><p>예시 후보 <code>example-candidate-01</code>에 연결된 결과입니다.</p><button data-action="artifact">전달 근거</button>':'<p class="subtle">아직 결과 전달 기록이 없습니다. 예정 관계를 실제 전달로 표시하지 않습니다.</p>'}`);
}
function action(name){
 if(['save-spec','request-plan','confirm','confirm-submit','handoff','decision-submit'].includes(name)&&state.scenario==='offline')return notify('연결 후 다시 확인해 주세요.');
 if(name==='new'&&state.created){state.project='new';setStage('plan');notify('이 시안은 새 프로젝트 1개만 제공합니다. 기존 입력과 확정 기록을 이어서 보여 드립니다.');return;}
 if(name==='new'){open('새 프로젝트',`<p class="subtle">예시 입력만 보관합니다. 개인 정보나 비밀을 넣지 마세요.</p><form id="new-form"><div><label for="name">이름</label><input id="name" required maxlength="100" value="${esc(state.draft.name)}"><label for="goal">목표</label><textarea id="goal" required maxlength="2000" placeholder="어떤 일을 해 보고 싶나요?">${esc(state.draft.goal)}</textarea><div class="actions"><button class="primary" type="submit" ${offline()}>PM과 시작</button></div></div></form>`);return;}
 if(name==='save-spec'){state.spec=true;notify('예시 명세 v1 저장 · 실행 0건');}
 if(name==='request-plan'){state.plan=true;notify('저장한 명세 v1에 연결한 새 예시 계획입니다.');}
 if(name==='confirm'){open('계획 확인',`<p>${esc(projectName())}</p><p>두 업무를 병렬로 진행하고 같은 후보를 검수하는 예시입니다.</p>${binding()}<label><input type="checkbox" id="reviewed">범위와 완료 조건을 읽었습니다. 예시 상태만 바뀌며 실제 실행은 시작되지 않습니다.</label><button class="primary" data-action="confirm-submit" disabled>예시에서 확정</button>`);return;}
 if(name==='confirm-submit'){if(!$('#reviewed')?.checked||!state.spec||!state.plan)return;state.confirmed=true;state.stage='progress';$('#detail').close();notify('예시 확정 1건 · 실제 모델 호출 없음');}
 if(name==='handoff'&&['quota','failure'].includes(state.scenario))return;
 if(name==='handoff'){state.handoff=true;save();$('#detail').close();render();document.querySelector(`[data-task="${runId()}-task-screen"]`)?.focus();showTask(runId()+'-task-screen');return;}
 if(name==='dependency'){open('선행 조건',`<p>화면 변경과 검사 코드가 모두 있어야 같은 후보를 검수합니다.</p><ul class="plain-list"><li>로그인 화면 정리 → 같은 후보 검수</li><li>오류 상황 검사 작성 → 같은 후보 검수</li></ul><p>${oldRun()?'두 결과가 예시 후보에 연결됐습니다.':'예정된 의존관계입니다. 결과가 전달됐다는 뜻은 아닙니다.'}</p><p class="subtle">${runId()}</p>${oldRun()?'<button data-action="artifact">전달 근거</button>':''}`);return;}
 if(name==='artifact'){open('전달 근거','<p>예시 결과 2건 → 통합 검수</p><dl><dt>실행</dt><dd>example-run-01</dd><dt>후보</dt><dd>example-candidate-01</dd><dt>상태</dt><dd>전달 완료 · 예시 이벤트</dd></dl><p>실제 파일 다운로드나 원격 CI 결과는 없습니다.</p>');return;}
 if(name==='plan-record'){open('계획 기록',`${binding()}<p>과거 확정 이벤트의 예시입니다. 현재의 다른 계획이나 추가 범위를 승인하지 않습니다.</p>`);return;}
 if(name==='investigation'){open('실패 조사','<p>예시 조사 항목 · 반복된 검사 실패의 원인을 확인합니다.</p><p>원인 조사는 범위를 늘리는 개발 승인과 별개입니다. 예산·시간·실패 상한은 유지하고 마스터의 새 결정 없이 반복하지 않습니다.</p>');return;}
 if(name==='decision'){open('후보 검토',`<p>로그인 화면 변경과 검사 결과 · 예시 후보입니다.</p><dl><dt>실행</dt><dd>example-run-01</dd><dt>승인 대상</dt><dd>example-approval-01</dd><dt>후보</dt><dd>example-candidate-01</dd><dt>대상 digest</dt><dd><code>${'d'.repeat(64)}</code></dd></dl><p>배포·병합 권한은 포함하지 않습니다. 원래 요청은 pending으로 유지됩니다.</p><label><input type="checkbox" id="decision-reviewed">같은 실행의 후보와 근거를 확인했습니다.</label><button data-action="decision-submit" disabled>예시 선택 기록</button>`);return;}
 if(name==='decision-submit'){if(!$('#decision-reviewed')?.checked||!oldRun())return;state.decision={run:'example-run-01',approval:'example-approval-01',digest:'d'.repeat(64),scope:'preview_only'};$('#detail').close();notify('별도 예시 선택 기록 · 원래 후보 pending 유지');}
 if(name==='reconnect'){state.scenario='normal';notify('예시 연결을 복구했습니다. 운영 서버에는 연결하지 않습니다.');}
 save();render();
 const nextAction={'save-spec':'request-plan','request-plan':'confirm'}[name];
 if(nextAction)document.querySelector(`[data-action="${nextAction}"]`)?.focus();
}
$('#theme').onclick=()=>{state.theme=state.theme==='light'?'black':'light';save();render();};
$('#scenario').onchange=e=>{state.scenario=e.target.value;save();render();};
$('#reset').onclick=()=>{state=structuredClone(initial);save();render();notify('이 탭의 예시만 초기화했습니다.');};
document.addEventListener('click',e=>{
 if(e.target.closest('.brand')){e.preventDefault();setStage('projects');return;}
 const b=e.target.closest('button');if(!b)return;
 if(b.dataset.layout){state.layout=b.dataset.layout;save();render();}
 if(b.dataset.stage)setStage(b.dataset.stage);
 if(b.dataset.project){state.project=b.dataset.project;state.run='current';setStage(isNew()?'plan':'progress');}
 if(b.dataset.tab){state.tab=b.dataset.tab;save();render();$('#main')?.focus();}
 if(b.dataset.task)showTask(b.dataset.task);
 if(b.dataset.action)action(b.dataset.action);
});
document.addEventListener('change',e=>{if(e.target.id==='run'){state.run=e.target.value;save();render();}if(e.target.id==='reviewed')$('[data-action=confirm-submit]').disabled=!e.target.checked;if(e.target.id==='decision-reviewed')$('[data-action=decision-submit]').disabled=!e.target.checked;});
document.addEventListener('input',e=>{if(['name','goal','pm-reply'].includes(e.target.id)){state.draft[e.target.id==='pm-reply'?'reply':e.target.id]=e.target.value;save();}});
document.addEventListener('submit',e=>{
 if(e.target.id==='new-form'){e.preventDefault();if(state.created||state.scenario==='offline'||!e.target.reportValidity())return;const name=$('#name').value.trim(),goal=$('#goal').value.trim();if(!name||!goal)return;state.created={name,goal};state.project='new';state.spec=false;state.plan=false;state.confirmed=false;state.stage='plan';$('#detail').close();save();render();}
 if(e.target.id==='pm-form'){e.preventDefault();if(state.scenario==='offline')return;state.draft.sent=true;save();render();notify('예시 입력 저장 · 실제 PM 호출 없음');}
});
window.addEventListener('hashchange',()=>{if(location.hash==='#projects')setStage('projects');});
render();
})();
