/* PR #23 R1–R5: synthetic preview only. All network requests are intercepted. */
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
  // Arbitrary goals remain drafts; the fixed login story must be an explicit choice.
  {
   const {context,page}=await session();await click(page,'new');await page.locator('#name').fill('텃밭 기록');await page.locator('#goal').fill('이번 주에 심은 작물을 정리하고 싶어요.');await page.locator('#new-form button[type=submit]').click();
   assert.equal(await action(page,'organize').count(),0,'Arbitrary goal must not silently become a login plan');assert.equal((await stored(page)).confirmed,false);
   assert.match(await page.locator('#main').innerText(),/고정|예시 목표/);await context.close();
  }
  for(const layout of ['journey','overview'])for(const width of [320,390,1440])for(const theme of ['light','black']){
   const {context,page}=await session(width);await page.locator(`[data-layout="${layout}"]`).click();if(theme==='black')await page.locator('#theme').click();
   // R3 starts at the project list: no knowledge of the run selector is required.
   const pending=page.locator('[data-approval="example-approval-01"]').first();await pending.waitFor();assert.match(await page.locator('.projects').innerText(),/확인할 일.*1/);
   await pending.click();assert.equal((await stored(page)).run,'previous');await click(page,'decision');
   const prior=await page.locator('#detail').innerText();for(const id of ['example-project-login','example-plan-01','example-run-01','example-approval-01','example-candidate-01','d'.repeat(64)])assert.ok(prior.includes(id),id);
   await page.keyboard.press('Escape');await page.locator('#run').selectOption('current');assert.equal(await action(page,'decision').count(),0);await page.locator('[data-approval="example-approval-01"]').first().click();assert.equal((await stored(page)).run,'previous');
   await decide(page,'hold');const priorDecision=(await stored(page)).decisions['example-approval-01'];assert.equal(priorDecision.choice,'hold');
   await stage(page,'projects');await createExample(page);await noRun(page);await click(page,'organize');await noRun(page);
   assert.equal((await stored(page)).flow.status,'preparing');assert.equal(await action(page,'confirm').count(),0);
   await click(page,'plan-fail');assert.equal((await stored(page)).flow.status,'failed');await noRun(page);
   await click(page,'retry-plan');await click(page,'plan-ready');await noRun(page);
   const first=await stored(page);assert.equal(first.flow.status,'ready');
   await click(page,'edit-plan');await page.locator('#scope').fill('로그인 안내와 오류 뒤의 행동을 정리합니다. 배포와 병합은 제외합니다.');await page.locator('#criteria').fill('한국어 320px에서 다음 행동을 찾고, 같은 후보의 검사·검수 근거를 확인합니다.');await page.locator('#plan-edit-form button[type=submit]').click();
   const edited=await stored(page);assert.equal(edited.flow.status,'invalidated');assert.equal(edited.confirmed,false);assert.equal(edited.flow.run,null);assert.equal(await action(page,'confirm').count(),0);
   await click(page,'organize');await click(page,'plan-ready');await noRun(page);assert.ok((await stored(page)).flow.version>first.flow.version);
   await click(page,'confirm');assert.equal(await action(page,'confirm-submit').isDisabled(),true);assert.match(await page.locator('#detail').innerText(),/범위/);assert.match(await page.locator('#detail').innerText(),/한도|예산/);
   await page.locator('#reviewed').check();await click(page,'confirm-submit');
   const confirmed=await stored(page);assert.equal(confirmed.confirmed,true);assert.ok(confirmed.flow.run);assert.equal(confirmed.events.filter(e=>e.type==='plan_confirmed').length,1);assert.equal(confirmed.events.filter(e=>e.type==='run_created').length,1);assert.equal(confirmed.events.filter(e=>e.type==='spec_saved').length,2);assert.equal(confirmed.events.filter(e=>e.type==='plan_requested').length,3);assert.equal(confirmed.flow.run.specVersion,confirmed.flow.version);assert.equal(confirmed.flow.run.scope,confirmed.flow.scope);await page.reload();assert.deepEqual((await stored(page)).flow.run,confirmed.flow.run);assert.equal((await stored(page)).events.filter(e=>e.type==='run_created').length,1);
   // R4 uses this very project/run for every later stage and artifact.
   const screen='example-new-run-01-task-screen';assert.equal(await page.locator(`[data-task="${screen}"]`).count(),1);
   assert.deepEqual(await page.locator('.task-flow .status').allTextContents(),['진행','진행','선행 대기']);
   await click(page,'play-wait');await page.locator(`[data-task="${screen}"]`).click();assert.match(await page.locator('#detail').innerText(),/한도/);await click(page,'handoff');assert.match(await page.locator('#detail').innerText(),/Astra → Claude/);assert.match(await page.locator('#detail').innerText(),new RegExp(screen));await page.keyboard.press('Escape');
   await click(page,'play-checks');await page.locator('[data-task="example-new-run-01-task-integration"]').click();assert.match(await page.locator('#detail').innerText(),/원격 CI/);assert.match(await page.locator('#detail').innerText(),/Astra Ultra/);await page.keyboard.press('Escape');
   await click(page,'play-review');await click(page,'dependency');await click(page,'artifact');assert.match(await page.locator('#detail').innerText(),/example-new-run-01/);assert.doesNotMatch(await page.locator('#detail').innerText(),/example-run-01\b/);await page.keyboard.press('Escape');
   await stage(page,'result');assert.match(await page.locator('#main').innerText(),/예시/);await stage(page,'approval');await click(page,'decision');
   const binding=await page.locator('#detail').innerText();for(const expected of ['example-project-new','example-new-run-01','example-approval-new-01'])assert.ok(binding.includes(expected));assert.doesNotMatch(binding,/example-run-01\b/);
   await page.locator('#decision-choice').selectOption('request_changes');await page.locator('#decision-reviewed').check();assert.equal(await action(page,'decision-submit').isDisabled(),true,'Revision requires an opinion');assert.equal(await page.locator('#detail').isVisible(),true);assert.equal((await stored(page)).decisions['example-approval-new-01'],undefined);
   await page.locator('#decision-reason').fill('오류 다음 행동을 더 짧게 보여 주세요.');await click(page,'decision-submit');
   const selected=await stored(page);assert.equal(selected.decisions['example-approval-new-01'].choice,'request_changes');assert.deepEqual(selected.decisions['example-approval-01'],priorDecision);assert.deepEqual(selected.flow.run,confirmed.flow.run);
   assert.match(await page.locator('#main').innerText(),/pending/);assert.match(await page.locator('#main').innerText(),/다시|수정/);await page.reload();assert.deepEqual((await stored(page)).decisions,selected.decisions);
   await shot(page,`task-workspace-review-${layout}-${theme}-${width}.png`);
   checks.push({id:'R2-R5',layout,width,theme,result:'PASS',evidence:'Previous pending discovered; separate hold/revision decisions; preparation failure/retry/edit invalidation; explicit one-run confirmation; parallel→wait→handoff→checks→review→same-run decision.'});
   await context.close();
  }
  assert.deepEqual(errors,[]);assert.deepEqual(violations,[]);
  await fs.writeFile(path.join(out,'task-workspace-review-validation.json'),JSON.stringify({status:'PASS',reference:'PR23 a446a5146f8cbb06a4f7575e64a17cf530c5ef49',browser:browser.version(),sandbox:true,checks,screenshots,errors,violations,limits:['Synthetic preview; no production API, models or approval changes','No physical APK or novice usability study','CSS workflow does not replace original graph routing tests']},null,2));
  console.log(JSON.stringify({status:'PASS',checks:checks.length,screenshots:screenshots.length}));
 }catch(error){await fs.writeFile(path.join(out,'task-workspace-review-validation.json'),JSON.stringify({status:'FAIL',error:error.stack,checks,screenshots,errors,violations},null,2));throw error;}finally{if(browser)await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
