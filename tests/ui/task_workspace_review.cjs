/* PR #23 R1–R5 and F1–F2: synthetic preview only. All network requests are intercepted. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const root=path.resolve(__dirname,'../../docs/previews/task-workspace');
const out=process.env.UI_OUTPUT||'/tmp/task-workspace-review';
const origin='http://127.0.0.1:47996';
const key='ai-company:task-design:1';
const checks=[],screenshots=[],errors=[],violations=[];
let browser;
const stored=page=>page.evaluate(key=>JSON.parse(sessionStorage.getItem(key)),key);
const action=(page,name)=>page.locator(`[data-action="${name}"]`).first();
const click=(page,name)=>action(page,name).click();
const stage=(page,name)=>page.locator(`.stage-nav [data-stage="${name}"]`).click();
async function fit(page){assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'No horizontal overflow');}
async function shot(page,name){await page.evaluate(()=>document.fonts.ready);await fit(page);await page.screenshot({path:path.join(out,name),fullPage:true});screenshots.push(name);}
async function session(width=390){
 const context=await browser.newContext({viewport:{width,height:width>700?1000:844},hasTouch:width<700,isMobile:width<700,serviceWorkers:'block',reducedMotion:'reduce'});
 await context.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url());
  const names={'/':'index.html','/index.html':'index.html','/preview.js':'preview.js','/style.css':'style.css'};
  if(url.origin===origin&&request.method()==='GET'&&url.pathname==='/favicon.ico')return route.fulfill({status:404,body:''});
  if(url.origin!==origin||request.method()!=='GET'||!names[url.pathname]){violations.push(`${request.method()} ${url.origin}${url.pathname}`);return route.abort();}
  await route.fulfill({body:await fs.readFile(path.join(root,names[url.pathname])),contentType:{'.html':'text/html','.js':'text/javascript','.css':'text/css'}[path.extname(names[url.pathname])]});
 });
 const page=await context.newPage();page.setDefaultTimeout(7000);page.on('pageerror',e=>errors.push(e.message));await page.goto(origin);return {context,page};
}
async function createExample(page){
 await click(page,'new');await click(page,'use-example');
 assert.ok((await page.locator('#name').inputValue()).trim());assert.ok((await page.locator('#goal').inputValue()).trim());
 await page.locator('#new-form button[type=submit]').click();
 await page.locator('#pm-reply').fill('오류 안내가 길지 않고 다음 행동을 바로 찾고 싶어요.');
 await page.locator('#pm-form button').click();
}
async function noRun(page){const state=await stored(page);assert.equal(state.confirmed,false);assert.equal(state.flow.run,null);assert.match(await page.locator('#main .binding').textContent(),/아직 시작하지 않음/);}
async function decide(page,choice,reason=''){
 await click(page,'decision');await page.locator('#decision-choice').selectOption(choice);
 if(reason)await page.locator('#decision-reason').fill(reason);
 await page.locator('#decision-reviewed').check();await click(page,'decision-submit');
}
async function scenario(page,value){
 const settings=page.locator('.preview-tools details');
 if(!await settings.evaluate(element=>element.open))await settings.locator('summary').click();
 await page.locator('#scenario').selectOption(value);await settings.locator('summary').click();
}
const reportText=(page,title)=>page.locator('.report-grid section').filter({has:page.getByRole('heading',{name:title,exact:true})}).locator('p').first().innerText();
async function phaseReport(page,phase){
 const before=await stored(page),run=before.flow.run;
 assert.equal(before.flow.playback,phase);assert.equal(before.scenario,'normal');
 assert.deepEqual(await page.locator('.task-flow [data-task]').evaluateAll(items=>items.map(item=>item.dataset.task)),['screen','tests','integration'].map(role=>`${run.id}-task-${role}`));
 const statuses={parallel:['진행','진행','선행 대기'],waiting:['한도 대기','진행','선행 대기'],handoff:['진행','진행','선행 대기'],checks:['완료','완료','검수 중'],review:['완료','완료','완료']};
 assert.deepEqual(await page.locator('.task-flow .status').allTextContents(),statuses[phase],`${phase}: progress follows the recorded phase`);
 await stage(page,'result');
 const done=await reportText(page,'완료'),remaining=await reportText(page,'남은 일'),blocked=await reportText(page,'차단');
 const next=page.locator('.rail > section').first();
 if(['parallel','waiting','handoff'].includes(phase)){
  assert.match(done,/계획 확정/);assert.match(done,/배정/);assert.doesNotMatch(done,/결과 전달|검수.*완료|작성 완료/);
  assert.match(remaining,/개발/);assert.match(remaining,/검사/);
 }else if(phase==='checks'){
  for(const part of [/화면 개발/,/검사 작성 완료/,/결과 전달/])assert.match(done,part);
  for(const part of [/같은 후보/,/격리 검사/,/원격 CI/,/독립 검수/,/최종 검수/])assert.match(remaining,part);
  assert.doesNotMatch(remaining,/개발.*수집|검사 작성.*수집/,'Already delivered inputs must not be reported as awaiting collection');
  assert.equal(await action(page,'artifact').isVisible(),true,'The completed inputs are inspectable during integration checks');
 }else{
  assert.match(done,/두 업무/);assert.match(done,/검사.*독립 검수.*최종 검수/);assert.match(remaining,/후보 검토/);
 }
 if(phase==='waiting')assert.match(blocked,/한도 대기/);
 else{assert.match(blocked,/없음/);assert.doesNotMatch(blocked,/한도 대기|상한에 도달/);}
 assert.match(await next.locator('.next').innerText(),phase==='checks'?/후보.*검수/:phase==='review'?/후보.*검토/:phase==='waiting'?/대기/:/진행/);
 assert.equal(await next.locator('button').getAttribute('data-stage'),phase==='review'?'approval':'progress');
 const binding=await page.locator('#main .binding').textContent();
 for(const value of [run.project,run.id,run.plan,run.planDigest])assert.ok(binding.includes(value),`${phase} report preserves ${value}`);
 const after=await stored(page);assert.deepEqual(after.flow,before.flow);assert.deepEqual(after.events,before.events);assert.deepEqual(after.decisions,before.decisions);
 const result={phase,taskStatuses:statuses[phase],run:run.id,plan:run.plan,completed:done,remaining,blocked,next:await next.innerText()};
 await stage(page,'progress');return result;
}
(async()=>{
 await fs.mkdir(out,{recursive:true});
 try{
  browser=await chromium.launch({headless:true,chromiumSandbox:true,channel:process.env.CHROME_CHANNEL||'chrome'});
  // Fresh storage reproduces R1: no restored object can accidentally mask shared defaults.
  {
   const {context,page}=await session();
   assert.equal(await page.evaluate(key=>sessionStorage.getItem(key),key),null);
   await click(page,'new');await page.locator('#name').fill('초기화 검수');await page.locator('#goal').fill('원래의 빈 입력으로 돌아갑니다.');
   await page.locator('#new-form button[type=submit]').click();await page.locator('#pm-reply').fill('검수 답변');
   await page.reload();assert.equal(await page.locator('#pm-reply').inputValue(),'검수 답변');
   const settings=page.locator('.preview-tools details');await settings.locator('summary').click();await page.locator('#reset').click();
   await click(page,'new');assert.equal(await page.locator('#name').inputValue(),'');assert.equal(await page.locator('#goal').inputValue(),'');
   const cleared=await stored(page);assert.equal(cleared.draft.reply||'','');assert.equal(cleared.created,null);assert.equal(cleared.confirmed,false);assert.deepEqual(cleared.events,[]);assert.deepEqual(cleared.decisions,{});
   await page.keyboard.press('Escape');await fit(page);
   checks.push({id:'R1',result:'PASS',evidence:'Fresh storage: three drafts survive reload, reset clears all drafts, events, decisions and confirmation.'});await context.close();
  }
  // F1: a closed creation form remains in the DOM; the visible project's choice must still work immediately.
  for(const layout of ['journey','overview'])for(const theme of ['light','black']){
   const width=layout==='journey'?320:390,{context,page}=await session(width);
   await page.locator(`[data-layout="${layout}"]`).click();if(theme==='black')await page.locator('#theme').click();
   const name='텃밭 기록',original='이번 주에 심은 작물을 정리하고 싶어요.';
   await click(page,'new');await page.locator('#name').fill(name);await page.locator('#goal').fill(original);await page.locator('#new-form button[type=submit]').click();
   assert.equal(await action(page,'organize').count(),0,'Arbitrary goal must not silently become a login plan');assert.equal((await stored(page)).confirmed,false);
   assert.match(await page.locator('#main').innerText(),/고정|예시 목표/);
   assert.equal(await page.locator('#detail').evaluate(element=>element.open),false);assert.equal(await page.locator('#detail #new-form').count(),1,'Exercise the retained, closed form that caused F1');
   const before=await stored(page);assert.equal(before.created.goal,original);assert.equal(before.created.example,false);await noRun(page);
   // No reload or dialog reopen between creation and this explicit in-page choice.
   await page.locator('#main [data-action="use-example"]').click();
   const converted=await stored(page);
   assert.equal(converted.project,before.project);assert.equal(converted.created.name,name);assert.equal(converted.created.originalGoal,original);
   assert.equal(converted.created.example,true);assert.notEqual(converted.created.goal,original);assert.equal(converted.created.goal,converted.draft.goal);assert.equal(converted.draft.example,true);
   assert.equal(await page.locator('.project-head h1').innerText(),name);assert.equal(await page.locator('.project-head > div > p').innerText(),converted.created.goal);
   assert.equal(await action(page,'organize').isVisible(),true);assert.match(await page.locator('#main .binding').textContent(),/example-project-new/);
   assert.deepEqual(converted.events.slice(0,before.events.length),before.events,'Existing history is preserved');
   assert.equal(converted.events.filter(event=>['plan_confirmed','run_created'].includes(event.type)).length,0);await noRun(page);
   await page.reload();const restored=await stored(page);
   for(const field of ['project','created','draft','flow','confirmed','events','decisions'])assert.deepEqual(restored[field],converted[field],`F1 reload preserves ${field}`);
   await noRun(page);assert.equal(await action(page,'organize').isVisible(),true);assert.equal(await page.locator('.project-head > div > p').innerText(),converted.created.goal);
   await shot(page,`task-workspace-f1-converted-${layout}-${theme}-${width}.png`);
   await stage(page,'projects');assert.equal(await page.locator('[data-project="new"]').count(),1);assert.match(await page.locator('[data-project="new"]').innerText(),new RegExp(name));
   checks.push({id:'F1',layout,width,theme,result:'PASS',evidence:'Arbitrary goal stays a draft until an explicit in-page example choice; works immediately with the closed form retained; original goal and same project preserved, no run, reload retains the choice.'});
   await context.close();
  }
  for(const layout of ['journey','overview'])for(const width of [320,390,1440])for(const theme of ['light','black']){
   const {context,page}=await session(width);await page.locator(`[data-layout="${layout}"]`).click();if(theme==='black')await page.locator('#theme').click();
   // R3 starts at the project list: no knowledge of the run selector is required.
   const pending=page.locator('[data-approval="example-approval-01"]').first();await pending.waitFor();assert.match(await page.locator('.projects').innerText(),/확인할 일.*1/);
   await pending.click();assert.equal((await stored(page)).run,'previous');await click(page,'decision');await page.locator('#detail .binding summary').click();
   const prior=await page.locator('#detail').innerText();for(const id of ['example-project-login','example-plan-01','example-run-01','example-approval-01','example-candidate-01','d'.repeat(64)])assert.ok(prior.includes(id),id);
   await page.keyboard.press('Escape');await page.locator('#run').selectOption('current');assert.equal(await action(page,'decision').count(),0);await page.locator('[data-approval="example-approval-01"]').first().click();assert.equal((await stored(page)).run,'previous');
   await decide(page,'hold');const priorDecision=(await stored(page)).decisions['example-approval-01'];assert.equal(priorDecision.choice,'hold');
   await stage(page,'projects');await createExample(page);await noRun(page);await click(page,'organize');await noRun(page);
   assert.equal((await stored(page)).flow.status,'preparing');assert.equal(await action(page,'confirm').count(),0);
   await click(page,'plan-fail');assert.equal((await stored(page)).flow.status,'failed');assert.match(await page.locator('#notice').innerText(),/다시 시도/);await noRun(page);
   await click(page,'retry-plan');await click(page,'plan-ready');await noRun(page);
   const first=await stored(page);assert.equal(first.flow.status,'ready');assert.match(await page.locator('#notice').innerText(),/계획이 준비됐습니다/);assert.doesNotMatch(await page.locator('#notice').innerText(),/응답을 재생/);
   await click(page,'edit-plan');await page.locator('#scope').fill('로그인 안내와 오류 뒤의 행동을 정리합니다. 배포와 병합은 제외합니다.');await page.locator('#criteria').fill('한국어 320px에서 다음 행동을 찾고, 같은 후보의 검사·검수 근거를 확인합니다.');await page.locator('#plan-edit-form button[type=submit]').click();
   const edited=await stored(page);assert.equal(edited.flow.status,'invalidated');assert.match(await page.locator('#notice').innerText(),/다시 정리한 계획/);assert.equal(edited.confirmed,false);assert.equal(edited.flow.run,null);assert.equal(await action(page,'confirm').count(),0);
   await click(page,'organize');await click(page,'plan-ready');await noRun(page);assert.ok((await stored(page)).flow.version>first.flow.version);
   if(width===390)await shot(page,`task-workspace-plan-${layout}-${theme}-${width}.png`);
   await click(page,'confirm');assert.equal(await action(page,'confirm-submit').isDisabled(),true);assert.match(await page.locator('#detail').innerText(),/범위/);assert.match(await page.locator('#detail').innerText(),/한도|예산/);
   await page.locator('#reviewed').check();await click(page,'confirm-submit');
   const confirmed=await stored(page);assert.equal(confirmed.confirmed,true);assert.ok(confirmed.flow.run);assert.equal(confirmed.events.filter(e=>e.type==='plan_confirmed').length,1);assert.equal(confirmed.events.filter(e=>e.type==='run_created').length,1);assert.equal(confirmed.events.filter(e=>e.type==='spec_saved').length,2);assert.equal(confirmed.events.filter(e=>e.type==='plan_requested').length,3);assert.equal(confirmed.flow.run.specVersion,confirmed.flow.version);assert.equal(confirmed.flow.run.scope,confirmed.flow.scope);await page.reload();assert.deepEqual((await stored(page)).flow.run,confirmed.flow.run);assert.equal((await stored(page)).events.filter(e=>e.type==='run_created').length,1);
   // R4 uses this very project/run for every later stage and artifact.
   const screen='example-new-run-01-task-screen';assert.equal(await page.locator(`[data-task="${screen}"]`).count(),1);
   assert.deepEqual(await page.locator('.task-flow .status').allTextContents(),['진행','진행','선행 대기']);
   const phases=[await phaseReport(page,'parallel')];
   if(width===390)await shot(page,`task-workspace-cycle-${layout}-${theme}-${width}.png`);
   await click(page,'play-wait');phases.push(await phaseReport(page,'waiting'));
   await page.locator(`[data-task="${screen}"]`).click();assert.match(await page.locator('#detail').innerText(),/한도/);await page.locator('#detail [data-action="handoff"]').click();assert.match(await page.locator('#detail').innerText(),/Astra → Claude/);assert.match(await page.locator('#detail').innerText(),new RegExp(screen));await page.keyboard.press('Escape');
   phases.push(await phaseReport(page,'handoff'));
   await click(page,'play-checks');await page.locator('[data-task="example-new-run-01-task-integration"]').click();assert.match(await page.locator('#detail').innerText(),/원격 CI/);assert.match(await page.locator('#detail').innerText(),/Astra Ultra/);await page.keyboard.press('Escape');
   phases.push(await phaseReport(page,'checks'));
   await stage(page,'result');
   if(width===390)await shot(page,`task-workspace-f2-checks-${layout}-${theme}-${width}.png`);
   await click(page,'artifact');const delivered=await page.locator('#detail').innerText();
   for(const value of [confirmed.flow.run.project,confirmed.flow.run.id,'example-candidate-new-01'])assert.ok(delivered.includes(value));
   assert.match(delivered,/전달 완료/);assert.doesNotMatch(delivered,/example-run-01\b/);await page.keyboard.press('Escape');await stage(page,'progress');
   const checksBefore=await stored(page),blockedChecks=[];
   for(const value of ['quota','failure']){
    await scenario(page,value);
    assert.deepEqual(await page.locator('.task-flow .status').allTextContents(),['완료','완료',value==='quota'?'한도 대기':'판단 필요'],'Completed producer work is preserved while integration waits or is blocked');
    if(!await action(page,'play-review').isDisabled())await click(page,'play-review');
    const blocked=await stored(page);assert.equal(blocked.flow.playback,'checks');assert.deepEqual(blocked.flow.run,confirmed.flow.run);assert.deepEqual(blocked.events,checksBefore.events);assert.deepEqual(blocked.decisions,checksBefore.decisions);
    await stage(page,'result');const reason=await reportText(page,'차단');
    assert.match(reason,value==='quota'?/한도 대기/:/상한/);assert.doesNotMatch(reason,/없음/);
    assert.match(await reportText(page,'완료'),/검사 작성 완료.*결과 전달/);assert.match(await reportText(page,'남은 일'),/같은 후보.*독립 검수.*최종 검수/);
    assert.match(await page.locator('.rail > section').first().locator('.next').innerText(),value==='quota'?/대기/:/실패/);
    await stage(page,'approval');assert.equal(await action(page,'decision').count(),0);assert.equal(await page.locator('[data-approval="example-approval-new-01"]').count(),0,'A blocked verification cannot create a candidate request');
    blockedChecks.push({scenario:value,phase:blocked.flow.playback,run:blocked.flow.run.id,blocked:reason});await stage(page,'progress');
   }
   await scenario(page,'normal');assert.deepEqual((await stored(page)).flow,checksBefore.flow);
   await click(page,'play-review');await click(page,'dependency');await click(page,'artifact');assert.match(await page.locator('#detail').innerText(),/example-new-run-01/);assert.doesNotMatch(await page.locator('#detail').innerText(),/example-run-01\b/);await page.keyboard.press('Escape');
   phases.push(await phaseReport(page,'review'));
   for(const value of ['quota','failure']){
    await scenario(page,value);
    assert.deepEqual(await page.locator('.task-flow .status').allTextContents(),['완료','완료','완료']);assert.equal(await page.getByText('모든 후보 대기',{exact:true}).count(),0);assert.equal(await page.getByText('자동 재시도 중단',{exact:true}).count(),0);
    await stage(page,'result');assert.match(await reportText(page,'완료'),/두 업무.*최종 검수/);assert.match(await reportText(page,'차단'),/없음/);assert.doesNotMatch(await reportText(page,'차단'),/한도|상한/);assert.match(await reportText(page,'내 결정'),/후보 검토 1건/);await stage(page,'progress');
   }
   await scenario(page,'normal');
   await stage(page,'result');assert.match(await page.locator('#main').innerText(),/예시/);await stage(page,'approval');await click(page,'decision');await page.locator('#detail .binding summary').click();
   const binding=await page.locator('#detail').innerText();for(const expected of ['example-project-new','example-new-run-01','example-approval-new-01'])assert.ok(binding.includes(expected));assert.doesNotMatch(binding,/example-run-01\b/);await page.locator('#detail .binding summary').click();
   await page.locator('#decision-choice').selectOption('request_changes');await page.locator('#decision-reviewed').check();assert.equal(await action(page,'decision-submit').isDisabled(),true,'Revision requires an opinion');assert.equal(await page.locator('#detail').isVisible(),true);assert.equal((await stored(page)).decisions['example-approval-new-01'],undefined);
   await page.locator('#decision-reason').fill('오류 다음 행동을 더 짧게 보여 주세요.');await click(page,'decision-submit');
   const selected=await stored(page);assert.equal(selected.decisions['example-approval-new-01'].choice,'request_changes');assert.deepEqual(selected.decisions['example-approval-01'],priorDecision);assert.deepEqual(selected.flow.run,confirmed.flow.run);
   assert.match(await page.locator('#main').innerText(),/pending/);assert.match(await page.locator('#main').innerText(),/다시|수정/);await page.reload();assert.deepEqual((await stored(page)).decisions,selected.decisions);
   await stage(page,'result');assert.match(await reportText(page,'내 결정'),/수정 요청.*pending/);assert.match(await reportText(page,'남은 일'),/새 범위·계획.*다시 실행하지 않습니다/);assert.match(await page.locator('.rail > section').first().locator('.next').innerText(),/수정 요청/);assert.deepEqual((await stored(page)).flow.run,confirmed.flow.run);await stage(page,'approval');
   await shot(page,`task-workspace-review-${layout}-${theme}-${width}.png`);
   checks.push({id:'R2-R5',layout,width,theme,result:'PASS',evidence:'Previous pending discovered; separate hold/revision decisions; preparation failure/retry/edit invalidation; explicit one-run confirmation; parallel→wait→handoff→checks→review→same-run decision.'});
   checks.push({id:'F2',layout,width,theme,result:'PASS',phases,blockedChecks,evidence:'Progress task IDs/statuses, completed inputs, remaining checks, blockers and next action agree at all five phases; inspection preserves the exact run and event history; waiting/failure cannot create a review result or candidate request.'});
   await context.close();
  }
  assert.deepEqual(errors,[]);assert.deepEqual(violations,[]);
  await fs.writeFile(path.join(out,'task-workspace-review-validation.json'),JSON.stringify({status:'PASS',reference:'PR23 c802be905656a9421b1c1cfe23c93266e2db1a81',priorReference:'PR23 a446a5146f8cbb06a4f7575e64a17cf530c5ef49',browser:browser.version(),sandbox:true,checks,screenshots,errors,violations,limits:['Synthetic preview; no production API, models or approval changes','No physical APK or novice usability study','CSS workflow does not replace original graph routing tests']},null,2));
  console.log(JSON.stringify({status:'PASS',checks:checks.length,screenshots:screenshots.length}));
 }catch(error){await fs.writeFile(path.join(out,'task-workspace-review-validation.json'),JSON.stringify({status:'FAIL',error:error.stack,checks,screenshots,errors,violations},null,2));throw error;}finally{if(browser)await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
