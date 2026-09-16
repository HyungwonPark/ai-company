'use strict';

// 예시 데이터와 화면 상태만 사용한다. API, 쿠키, 브라우저 저장소에 접근하지 않는다.
const stage = document.getElementById('stage');
const notice = document.getElementById('notice');
const allowedConcepts = ['a', 'b', 'c'];
const fragment = new URLSearchParams(location.hash.slice(1));
const state = {
  concept: allowedConcepts.includes(fragment.get('concept')) ? fragment.get('concept') : 'a',
  screen: fragment.get('screen') === 'workspace' ? 'workspace' : 'login',
  view: 'progress', mobile: false, messages: [],
};
const titles = {progress: '역할별 진행', manager: 'PM 대화', reports: '보고서', approvals: '승인'};
const escapeText = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let noticeTimer;

function login() {
  return `<div class="login-page">
    <header class="login-header"><span>AI Company</span><span>Aigent workspace</span></header>
    <section class="login-brand"><p>Aigent workspace</p><h1><span>AI</span><span>Company</span></h1><span class="brand-domain">hyungwon.cloud</span></section>
    <section class="login-form-area"><div class="login-panel">
      <div class="login-panel-label">Aigent workspace</div>
      <form id="preview-login" autocomplete="off"><h2>로그인</h2>
        <label>아이디<input aria-label="아이디" value="edward" readonly autocomplete="off"></label>
        <label>비밀번호<input aria-label="비밀번호" type="password" value="preview-only" readonly autocomplete="off"></label>
        <button class="primary login-submit" type="submit"><span>로그인</span><span aria-hidden="true">↗</span></button>
      </form>
    </div></section>
    <footer class="login-footer">hyungwon.cloud</footer>
  </div>`;
}

function progress() {
  return `<div class="page-heading"><div><p class="eyebrow">개인 작업실</p><h1>일정 정리 도우미</h1><p class="description">이번 주 일정과 할 일을 한곳에서 정리합니다.</p></div><button class="secondary" data-view="manager">PM과 대화</button></div>
    <div class="work-summary"><span><strong>개발</strong>진행 중</span><span><strong>2</strong>작업 중인 역할</span><span><strong>1</strong>검토할 승인</span></div>
    <div class="section-heading"><h2>역할별 진행</h2><span>예시 프로젝트</span></div>
    <div class="role-grid">
      <article class="role"><header><span class="role-index">01</span><span class="status running">진행 중</span></header><h3>화면 개발</h3><p class="role-owner">Codex · 개발 담당</p><p class="role-task">주간 일정 화면 만들기</p><div class="task-steps"><span class="done">화면 구조</span><span class="current">일정 입력</span><span>모바일 확인</span></div><footer><span>최근 변경</span><strong>일정 목록 배치 완료</strong></footer></article>
      <article class="role"><header><span class="role-index">02</span><span class="status running">진행 중</span></header><h3>API 개발</h3><p class="role-owner">Claude · 개발 담당</p><p class="role-task">일정 저장과 조회 연결</p><div class="task-steps"><span class="done">데이터 구조</span><span class="current">저장 API</span><span>오류 처리</span></div><footer><span>최근 변경</span><strong>조회 응답 형식 정리</strong></footer></article>
      <article class="role"><header><span class="role-index">03</span><span class="status waiting">대기</span></header><h3>독립 검수</h3><p class="role-owner">Claude · 검수 담당</p><p class="role-task">기능과 예외 상황 확인</p><div class="task-steps"><span class="done">검사 기준</span><span>기능 확인</span><span>수정 의견</span></div><footer><span>대기 사유</span><strong>두 개발 역할의 산출물 대기</strong></footer></article>
    </div>
    <div class="activity"><h2>최근 진행</h2><div><time>방금</time><p>API 개발이 일정 저장 기능을 작업 중입니다.</p></div><div><time>5분 전</time><p>화면 개발이 주간 일정의 기본 배치를 완료했습니다.</p></div></div>`;
}

function manager() {
  return `<div class="page-heading"><div><p class="eyebrow">개인 작업실</p><h1>PM 대화</h1><p class="description">Astra Ultra · 프로젝트 매니저</p></div></div>
    <div class="manager-grid"><section class="conversation-panel" aria-label="예시 대화">
      <div class="chat-message mine"><span>나</span><p>이번 주 일정과 할 일을 한 화면에서 정리하고 싶어. 모바일에서도 편하게 쓰고 싶어.</p></div>
      <div class="chat-message"><span>Astra Ultra</span><p>화면 개발과 API 개발을 나누어 진행하겠습니다. 두 결과가 준비되면 독립 검수에서 일정 저장·수정·모바일 동작을 확인합니다.</p><ul><li>화면: 주간 일정과 할 일 입력</li><li>API: 일정 저장·조회·수정</li><li>완료 조건: 입력한 일정이 새로고침 후에도 유지</li></ul></div>
      ${state.messages.map(message => `<div class="chat-message mine"><span>나 · 예시 입력</span><p>${escapeText(message)}</p></div>`).join('')}
      <form id="preview-message"><label for="message">PM에게 전달할 내용</label><textarea id="message" rows="3" maxlength="2000" placeholder="예시 메시지를 입력해보세요."></textarea><button class="primary" type="submit">메시지 보내기</button></form>
    </section><aside class="plan-summary"><p class="eyebrow">계획 예시</p><h2>합의할 내용</h2><dl><div><dt>목표</dt><dd>주간 일정과 할 일 정리</dd></div><div><dt>개발 역할</dt><dd>화면 개발 · API 개발</dd></div><div><dt>검수</dt><dd>독립 검수 → Astra 최종 검수</dd></div></dl><button class="secondary" data-action="plan">계획 확인</button></aside></div>`;
}

function reports() {
  return `<div class="page-heading"><div><p class="eyebrow">개인 작업실</p><h1>보고서</h1><p class="description">변경 내용과 검수 의견을 확인합니다.</p></div></div>
    <article class="report"><div class="section-heading"><span class="status complete">예시 보고서</span><span>화면 개발</span></div><h2>주간 일정 화면의 기본 구조</h2><p>날짜별 일정과 할 일을 구분해 배치했습니다. 모바일에서는 선택한 날짜를 중심으로 목록을 보여줍니다.</p><h3>확인한 내용</h3><ul><li>날짜 선택과 일정 목록 표시</li><li>모바일 화면의 입력 버튼 배치</li><li>일정이 없을 때의 빈 화면</li></ul><div class="report-note">다음 단계: 실제 API 연결 후 저장·수정 흐름 검수</div></article>`;
}

function approvals() {
  return `<div class="page-heading"><div><p class="eyebrow">개인 작업실</p><h1>승인</h1><p class="description">실행할 대상과 변경 범위를 확인합니다.</p></div></div>
    <article class="report"><div class="section-heading"><span class="status waiting">검토 대기 · 예시</span></div><h2>개발 결과 미리보기 공개</h2><p>완료된 일정 화면을 별도 미리보기 주소에서 확인하는 요청입니다.</p><dl class="approval-facts"><div><dt>대상</dt><dd>일정 정리 도우미의 예시 화면</dd></div><div><dt>범위</dt><dd>화면 미리보기</dd></div><div><dt>기존 서비스</dt><dd>변경 없음</dd></div></dl><button class="primary" data-action="approval">승인 내용 보기</button><p class="example-caption">예시 버튼이며 실제 승인 기록은 생성하지 않습니다.</p></article>`;
}

function workspace() {
  return `<div class="workspace">
    <aside class="workspace-nav"><div class="workspace-brand">AI Company<span>Aigent workspace</span></div>
      <nav aria-label="예시 작업 화면">${Object.entries(titles).map(([id,label]) => `<button type="button" data-view="${id}" ${state.view===id?'aria-current="page"':''}>${label}</button>`).join('')}</nav>
      <button class="back-login" data-action="logout">로그인 화면</button>
    </aside>
    <div class="workspace-body"><header class="workspace-top"><span>일정 정리 도우미</span><span class="workspace-user">edward <b>디자인 예시</b></span></header><div class="workspace-content">${({progress,manager,reports,approvals}[state.view])()}</div></div>
  </div>`;
}

function render() {
  stage.dataset.concept=state.concept;
  stage.classList.toggle('mobile',state.mobile);
  stage.innerHTML=state.screen==='login'?login():workspace();
  for(const button of document.querySelectorAll('[data-concept]')) {
    if(button.tagName==='BUTTON') button.setAttribute('aria-pressed',String(button.dataset.concept===state.concept));
  }
  for(const button of document.querySelectorAll('[data-screen]')) button.setAttribute('aria-pressed',String(button.dataset.screen===state.screen));
  document.getElementById('mobile-toggle').setAttribute('aria-pressed',String(state.mobile));
  history.replaceState(null,'',`#concept=${state.concept}&screen=${state.screen}`);
}

function announce(message) {
  clearTimeout(noticeTimer);notice.textContent=message;notice.hidden=false;
  noticeTimer=setTimeout(()=>{notice.hidden=true;},4500);
}

document.addEventListener('click',event=>{
  const button=event.target.closest('button');if(!button)return;
  if(button.dataset.concept){state.concept=button.dataset.concept;render();}
  else if(button.dataset.screen){state.screen=button.dataset.screen;render();}
  else if(button.id==='mobile-toggle'){state.mobile=!state.mobile;render();}
  else if(button.dataset.view){state.screen='workspace';state.view=button.dataset.view;render();}
  else if(button.dataset.action==='logout'){state.screen='login';render();}
  else if(button.dataset.action==='plan')announce('예시 계획입니다. 실제 작업 생성이나 실행은 하지 않습니다.');
  else if(button.dataset.action==='approval')announce('예시 승인 화면입니다. 실제 승인이나 배포는 실행되지 않습니다.');
});
document.addEventListener('submit',event=>{
  if(event.target.id==='preview-login'){event.preventDefault();state.screen='workspace';state.view='progress';render();}
  else if(event.target.id==='preview-message'){
    event.preventDefault();const input=document.getElementById('message');const text=input.value.trim();
    if(text){state.messages.push(text);render();announce('이 화면 안에서만 표시되는 예시 메시지입니다.');}
  }
});
render();
