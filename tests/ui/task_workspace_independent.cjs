/* Independent review of the isolated one-project design fixture. No live requests. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const root=path.resolve(__dirname,'../../docs/previews/task-workspace');
const out=process.env.UI_OUTPUT||'/tmp/task-workspace-independent';
const origin='http://127.0.0.1:47995';
const checks=[],errors=[],violations=[],requests=[],physical=[],screenshots=[];
let browser;

async function session(options){
 const context=await browser.newContext({serviceWorkers:'block',reducedMotion:'reduce',...options});
 await context.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url());
  const names={'/':'index.html','/index.html':'index.html','/preview.js':'preview.js','/style.css':'style.css'};
  requests.push({method:request.method(),path:url.pathname});
  if(url.origin===origin&&request.method()==='GET'&&url.pathname==='/favicon.ico')return route.fulfill({status:404,body:''});
  if(url.origin!==origin||request.method()!=='GET'||!names[url.pathname]){
   violations.push(`${request.method()} ${url.origin}${url.pathname}`);return route.abort();
  }
  await route.fulfill({body:await fs.readFile(path.join(root,names[url.pathname])),contentType:{'.html':'text/html','.js':'text/javascript','.css':'text/css'}[path.extname(names[url.pathname])]});
 });
 const page=await context.newPage();page.setDefaultTimeout(7000);
 page.on('pageerror',error=>errors.push(error.message));
 await page.goto(origin);
 return {context,page};
}
const stored=page=>page.evaluate(()=>JSON.parse(sessionStorage.getItem('ai-company:task-design:1')));
const stage=(page,name)=>page.locator(`.stage-nav [data-stage="${name}"]`).click();
const action=(page,name)=>page.locator(`[data-action="${name}"]`).first();
async function scenario(page,value){
 const settings=page.locator('.preview-tools details');
 if(!await settings.evaluate(element=>element.open))await settings.locator('summary').click();
 await page.locator('#scenario').selectOption(value);
 await settings.locator('summary').click();
}
async function focused(page,selector){
 assert.equal(await page.evaluate(selector=>document.activeElement?.matches(selector),selector),true,`Focus should remain at ${selector}`);
}
async function keyActivate(page,locator){await locator.focus();await page.keyboard.press('Enter');}
async function chooseRun(page,value){
 await page.locator('#run').focus();await page.locator('#run').selectOption(value);await focused(page,'#run');
}
async function tabTo(page,selector){
 for(let i=0;i<15;i++){
  if(await page.evaluate(selector=>document.activeElement?.matches(selector),selector))return;
  await page.keyboard.press('Tab');
 }
 assert.fail(`Keyboard Tab did not reach ${selector}`);
}
async function pressAt(page,locator,kind){
 await locator.scrollIntoViewIfNeeded();
 const box=await locator.boundingBox();assert.ok(box);assert.ok(box.height>=43.5,'Primary touch target is at least 44px');
 const point={x:box.x+box.width/2,y:box.y+box.height/2};
 assert.equal(await locator.evaluate((element,point)=>element.contains(document.elementFromPoint(point.x,point.y)),point),true,'Target center must be physically exposed');
 const target=await locator.getAttribute('data-task')||await locator.getAttribute('data-action')||await locator.innerText();
 if(kind==='touch')await page.touchscreen.tap(point.x,point.y);else await page.mouse.click(point.x,point.y);
 physical.push({width:page.viewportSize().width,input:kind,target});
}
async function fit(page){
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'No horizontal document overflow');
}
async function shot(page,name){
 await page.evaluate(()=>document.fonts.ready);await fit(page);
 await page.screenshot({path:path.join(out,name),fullPage:true});screenshots.push(name);
}

(async()=>{
 await fs.mkdir(out,{recursive:true});
 try{
  browser=await chromium.launch({headless:true,chromiumSandbox:true,channel:process.env.CHROME_CHANNEL||'chrome'});
  const {context,page}=await session({viewport:{width:1440,height:1000}});
  await scenario(page,'empty');await page.getByRole('heading',{name:'아직 없어요'}).waitFor();
  await keyActivate(page,action(page,'new'));
  await page.locator('#name').fill('독립 검수용 로그인');
  await page.locator('#goal').fill('휴대폰에서 오류 안내를 읽고 다음 행동을 찾습니다.');
  await keyActivate(page,page.locator('#new-form button'));
  await focused(page,'#main');await page.keyboard.press('Tab');await focused(page,'#pm-reply');
  await page.locator('#pm-reply').fill('작은 화면에서도 안내를 보존해 주세요.');
  await keyActivate(page,page.locator('#pm-form button'));
  assert.equal(await page.evaluate(()=>document.activeElement.isConnected&&document.activeElement!==document.body),true);
  await keyActivate(page,action(page,'save-spec'));
  await focused(page,'[data-action="request-plan"]');
  assert.equal((await stored(page)).confirmed,false);
  assert.match(await page.locator('#main .binding').textContent(),/없음 · 아직 시작하지 않음/);
  await page.keyboard.press('Enter');await focused(page,'[data-action="confirm"]');
  assert.equal((await stored(page)).confirmed,false);
  const nextDecision=page.locator('.rail').getByRole('button',{name:'계획 검토',exact:true});
  assert.equal(await nextDecision.getAttribute('data-stage'),'plan');
  await keyActivate(page,nextDecision);await focused(page,'#main');
  await keyActivate(page,action(page,'confirm'));
  assert.equal(await action(page,'confirm-submit').isDisabled(),true);
  assert.match(await page.locator('#detail .binding').textContent(),/example-project-new/);
  await tabTo(page,'#reviewed');await page.keyboard.press('Space');
  await page.keyboard.press('Tab');await focused(page,'[data-action="confirm-submit"]');
  await page.keyboard.press('Enter');await focused(page,'#main');
  await page.getByRole('heading',{name:'시작 대기'}).waitFor();
  const confirmed=await stored(page);
  assert.equal(confirmed.confirmed,true);
  await stage(page,'projects');await page.locator('[data-project="new"]').waitFor();
  assert.equal(await page.getByRole('heading',{name:'아직 없어요'}).count(),0);
  await keyActivate(page,action(page,'new'));assert.equal(await page.locator('#detail').isVisible(),false);
  await page.reload();
  const repeated=await stored(page);
  for(const field of ['created','draft','spec','plan','confirmed'])assert.deepEqual(repeated[field],confirmed[field],`Repeat creation preserves ${field}`);
  assert.match(await page.locator('#main .binding').textContent(),/example-new-run-01/);
  checks.push('Empty list → creation → keyboard spec save (no run) → new plan (no run) → correct plan CTA → explicit confirmation; repeat creation/reload preserves one project and confirmation');

  await scenario(page,'normal');
  for(let i=0;i<2;i++){
   await keyActivate(page,page.locator('.brand'));await focused(page,'#main');
   await page.getByRole('heading',{name:'프로젝트',exact:true}).waitFor();
   await keyActivate(page,page.locator('[data-project="sample"]'));
  }
  for(const value of ['quota','failure']){
   await scenario(page,value);await chooseRun(page,'current');
   await page.locator('[data-tab="work"]').click();
   await page.locator('[data-task="example-run-02-task-screen"]').click();
   assert.equal(await action(page,'handoff').count(),0);
   await page.keyboard.press('Escape');await focused(page,'[data-task="example-run-02-task-screen"]');
   await chooseRun(page,'previous');
   assert.deepEqual(await page.locator('.task .status').allTextContents(),['완료','완료','완료']);
   assert.equal(await page.getByText('모든 후보 대기',{exact:true}).count(),0);
   assert.equal(await page.getByText('자동 재시도 중단',{exact:true}).count(),0);
   await page.locator('[data-tab="history"]').click();
   const history=await page.locator('.events').innerText();
   assert.match(history,/검수 기록 도착/);assert.doesNotMatch(history,/검사도 한도 대기|개발 담당 변경/);
   await stage(page,'result');assert.match(await page.locator('.summary-line').innerText(),/후보 결정을 기다립니다/);
   await stage(page,'approval');assert.match(await page.locator('#main .meta').innerText(),/example-approval-01.*pending/);
   await stage(page,'progress');
  }
  checks.push('Repeated brand navigation and focus survive; current quota/failure forbid handoff while previous run retains completion, historical records and its own pending candidate');
  await context.close();

  for(const {width,theme,input,layout} of [{width:320,theme:'light',input:'mouse',layout:'journey'},{width:390,theme:'black',input:'touch',layout:'overview'}]){
   const mobile=await session({viewport:{width,height:844},isMobile:true,hasTouch:true});
   const page=mobile.page;
   if(theme==='black')await pressAt(page,page.locator('#theme'),input);
   await pressAt(page,page.locator(`[data-layout="${layout}"]`),input);
   await pressAt(page,page.locator('[data-project="sample"]'),input);
   const task='example-run-02-task-screen';
   await pressAt(page,page.locator(`[data-task="${task}"]`),input);
   assert.match(await page.locator('#detail').innerText(),new RegExp(task));
   await pressAt(page,action(page,'handoff'),input);
   assert.match(await page.locator('#detail').innerText(),/Astra → Claude/);
   assert.match(await page.locator('#detail').innerText(),new RegExp(task));
   await pressAt(page,page.locator('#detail').getByRole('button',{name:'닫기',exact:true}),input);
   await focused(page,`[data-task="${task}"]`);
   await pressAt(page,action(page,'dependency'),input);
   assert.match(await page.locator('#detail').innerText(),/예정된 의존관계/);
   assert.equal(await action(page,'artifact').count(),0);
   await pressAt(page,page.locator('#detail').getByRole('button',{name:'닫기',exact:true}),input);
   await pressAt(page,page.locator('[data-tab="history"]'),input);
   await pressAt(page,page.locator(`[data-task="${task}"]`),input);
   assert.match(await page.locator('#detail').innerText(),new RegExp(task));
   await pressAt(page,page.locator('#detail').getByRole('button',{name:'닫기',exact:true}),input);
   await chooseRun(page,'previous');await pressAt(page,page.locator('[data-tab="work"]'),input);
   await pressAt(page,page.locator('[data-task="example-run-01-task-screen"]'),input);
   const previous=await page.locator('#detail').innerText();
   assert.match(previous,/Astra · High/);assert.doesNotMatch(previous,/Astra → Claude/);
   await pressAt(page,page.locator('#detail').getByRole('button',{name:'닫기',exact:true}),input);
   await pressAt(page,action(page,'dependency'),input);await pressAt(page,action(page,'artifact'),input);
   assert.match(await page.locator('#detail').innerText(),/example-run-01/);
   assert.match(await page.locator('#detail').innerText(),/example-candidate-01/);
   await pressAt(page,page.locator('#detail').getByRole('button',{name:'닫기',exact:true}),input);
   await pressAt(page,page.locator('.stage-nav [data-stage="approval"]'),input);
   await pressAt(page,action(page,'decision'),input);
   assert.equal(await action(page,'decision-submit').isDisabled(),true);
   const binding=await page.locator('#detail').innerText();
   for(const expected of ['example-run-01','example-approval-01','example-candidate-01','d'.repeat(64)])assert.ok(binding.includes(expected));
   assert.ok(!binding.includes('example-run-02'));
   await shot(page,`task-workspace-independent-${theme}-${width}.png`);
   await page.locator('#decision-reviewed').check();await pressAt(page,action(page,'decision-submit'),input);
   const choice=(await stored(page)).decision;
   assert.deepEqual(choice,{run:'example-run-01',approval:'example-approval-01',digest:'d'.repeat(64),scope:'preview_only'});
   assert.match(await page.locator('#main .meta').innerText(),/pending/);
   await chooseRun(page,'current');assert.equal(await action(page,'decision').count(),0);
   await fit(page);checks.push(`${width}px ${theme}/${layout}: real ${input} task, stable handoff ID, history, planned/recorded dependency and candidate choice; previous owner isolated, original pending preserved`);
   await mobile.context.close();
  }
  assert.deepEqual(errors,[]);assert.deepEqual(violations,[]);assert.equal(screenshots.length,2);
  await fs.writeFile(path.join(out,'task-workspace-independent-validation.json'),JSON.stringify({status:'PASS',fixture:true,browser:browser.version(),sandbox:true,source:'independent synthetic UI review; intercepted local assets only',checks,physical,screenshots,requests,errors,violations,limits:['No live API, database or model execution','No physical device or APK verification','Screenshots require a separate visual review']},null,2));
  console.log(JSON.stringify({status:'PASS',checks,screenshots}));
 }catch(error){
  await fs.writeFile(path.join(out,'task-workspace-independent-validation.json'),JSON.stringify({status:'FAIL',fixture:true,sandbox:true,error:error.stack,checks,physical,screenshots,errors,violations},null,2));
  throw error;
 }finally{if(browser)await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
