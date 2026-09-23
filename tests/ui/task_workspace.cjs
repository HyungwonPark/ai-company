/* All content is synthetic; every request is intercepted. No live services. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const root=path.resolve(__dirname,'../../docs/previews/task-workspace');
const out=process.env.UI_OUTPUT||'/tmp/task-workspace-evidence';
const origin='http://127.0.0.1:47994';
(async()=>{
 await fs.mkdir(out,{recursive:true});
 const browser=await chromium.launch({headless:true,chromiumSandbox:true,...(process.env.CHROME_CHANNEL?{channel:process.env.CHROME_CHANNEL}:{})});
 const context=await browser.newContext({viewport:{width:390,height:844},serviceWorkers:'block'});
 const page=await context.newPage();page.setDefaultTimeout(6000);
 const errors=[],requests=[],checks=[],fonts=[];
 page.on('pageerror',e=>errors.push(e.message));
 await context.route('**/*',async route=>{const r=route.request(),url=new URL(r.url());requests.push({path:url.pathname,method:r.method()});assert.equal(url.origin,origin);assert.equal(r.method(),'GET');assert.ok(!url.pathname.startsWith('/api/'));const file=path.resolve(root,'.'+(url.pathname==='/'?'/index.html':url.pathname));assert.ok(file.startsWith(root+path.sep));try{await route.fulfill({body:await fs.readFile(file),contentType:({'.html':'text/html','.css':'text/css','.js':'text/javascript'})[path.extname(file)]||'text/plain'});}catch(e){if(e.code==='ENOENT')await route.fulfill({status:404,body:''});else throw e;}});
 const snap=async name=>{await page.evaluate(()=>document.fonts.ready);await page.screenshot({path:path.join(out,`task-workspace-${name}.png`),fullPage:true});};
 const fit=async name=>{const x=await page.evaluate(()=>({w:innerWidth,s:document.documentElement.scrollWidth}));assert.ok(x.s<=x.w,`${name} overflow ${JSON.stringify(x)}`);};
 const click=async action=>page.locator(`[data-action="${action}"]`).first().click();
 const stage=async name=>page.locator(`.stage-nav [data-stage="${name}"]`).click();
 const stored=()=>page.evaluate(()=>JSON.parse(sessionStorage.getItem('ai-company:task-design:1')));
 async function scenario(value){await page.locator('.preview-tools details').evaluate(e=>e.open=true);await page.locator('#scenario').selectOption(value);await page.locator('.preview-tools details').evaluate(e=>e.open=false);}
 try{
  await page.goto(origin);await page.locator('[data-project=sample]').click();
  for(const layout of ['journey','overview'])for(const width of [320,390,1440])for(const theme of ['light','black']){
   await page.setViewportSize({width,height:width>700?1000:844});await page.locator(`[data-layout=${layout}]`).click();
   if(await page.locator('html').getAttribute('data-theme')!==theme)await page.locator('#theme').click();
   await stage('progress');await page.locator('[data-tab=work]').click();await fit(`${layout}/${theme}/${width}`);await snap(`${layout}-${theme}-${width}`);
   await page.locator('[data-task$="-task-screen"]').click();await page.getByRole('heading',{name:'로그인 화면 정리',exact:true}).waitFor();assert.match(await page.locator('#detail').innerText(),/example-run-02-task-screen/);await fit('dialog');await page.keyboard.press('Escape');assert.match(await page.evaluate(()=>document.activeElement.dataset.task),/-task-screen$/);
   await click('dependency');assert.match(await page.locator('#detail').innerText(),/예정된 의존관계/);await page.keyboard.press('Escape');
   await page.locator('[data-tab=team]').click();await page.getByText('요청 설정 · High / 실제 적용 미확인').waitFor();await fit('team');
   await page.locator('[data-tab=history]').click();await click('plan-record');await page.locator('#detail .binding summary').click();assert.match(await page.locator('#detail').innerText(),/example-run-02/);await page.keyboard.press('Escape');
   for(const view of ['plan','approval','result']){await stage(view);await fit(`${layout}/${theme}/${width}/${view}`);assert.ok(await page.locator('#main h2').count());}
   await stage('projects');await fit('projects');await page.locator('[data-project=sample]').click();
  }
  checks.push('2 layouts × 2 themes × 320/390/1440: render, node/condition/team/history selection, dialog Escape/focus, no overflow');
  const cdp=await context.newCDPSession(page);await cdp.send('DOM.enable');await cdp.send('CSS.enable');const dom=await cdp.send('DOM.getDocument');const node=await cdp.send('DOM.querySelector',{nodeId:dom.root.nodeId,selector:'.project-head h1'});fonts.push(...(await cdp.send('CSS.getPlatformFontsForNode',{nodeId:node.nodeId})).fonts);assert.ok(fonts.some(f=>/Noto.*CJK/.test(f.familyName)&&f.glyphCount>0));
  await page.setViewportSize({width:390,height:844});await page.locator('[data-layout=journey]').click();await stage('progress');await page.locator('[data-tab=work]').click();
  await page.locator('[data-task$="-task-screen"]').click();await click('handoff');await page.getByText('Claude · 후보 기본값',{exact:true}).waitFor();assert.match(await page.locator('#detail').innerText(),/example-run-02-task-screen/);await page.keyboard.press('Escape');
  await page.locator('[data-tab=history]').click();await page.locator('[data-task$="-task-screen"]').click();assert.match(await page.locator('#detail').innerText(),/Astra → Claude/);await page.keyboard.press('Escape');await page.locator('[data-tab=work]').click();await page.reload();assert.equal((await stored()).handoff,true);await page.locator('#run').selectOption('previous');await page.locator('[data-task$="-task-screen"]').click();assert.match(await page.locator('#detail').innerText(),/Astra · High/);assert.doesNotMatch(await page.locator('#detail').innerText(),/Astra → Claude/);await click('artifact');assert.match(await page.locator('#detail').innerText(),/example-run-01/);await page.keyboard.press('Escape');
  if(await page.locator('html').getAttribute('data-theme')!=='light')await page.locator('#theme').click();await stage('result');await page.getByText('검수는 끝났고,',{exact:false}).waitFor();await snap('journey-light-390-result');await stage('approval');await click('decision');assert.match(await page.locator('#detail').innerText(),/example-approval-01/);await page.locator('#decision-choice').selectOption('approve');await page.locator('#decision-reviewed').check();await click('decision-submit');assert.equal((await stored()).decision.run,'example-run-01');await page.getByText('원래 후보 pending 유지',{exact:false}).waitFor();
  await page.locator('#run').selectOption('current');await page.getByRole('heading',{name:'이 실행의 요청은 없어요'}).waitFor();assert.equal(await page.locator('[data-action=decision]').count(),0);
  checks.push('Handoff retains task ID; previous run keeps previous owner; report/candidate bound to one run, original pending, no cross-run request');
  await stage('projects');await click('new');await click('use-example');await page.locator('#new-form button[type=submit]').click();assert.equal((await stored()).confirmed,false);
  const createdName=(await stored()).created.name;
  await page.locator('#pm-reply').fill('안내를 짧고 쉽게 보여 주세요.');await page.locator('#pm-form button').click();await click('organize');assert.equal((await stored()).confirmed,false);assert.equal((await stored()).flow.status,'preparing');
  await page.reload();assert.equal(await page.locator('#pm-reply').inputValue(),'안내를 짧고 쉽게 보여 주세요.');await click('plan-ready');assert.equal((await stored()).confirmed,false);await click('confirm');await page.locator('#reviewed').check();await click('confirm-submit');assert.equal((await stored()).confirmed,true);
  const run=(await stored()).flow.run;await page.reload();assert.deepEqual((await stored()).flow.run,run);await page.locator('[data-task="example-new-run-01-task-screen"]').waitFor();await snap('new-project-confirmed');
  checks.push('Explicit fixed example → PM reply → organize/spec+plan preparation (0) → prepared plan (0) → explicit confirm (1); reload preserves draft and one same-project run');
  await stage('projects');await click('new');assert.equal((await stored()).confirmed,true);assert.equal((await stored()).created.name,createdName);await stage('projects');await page.locator('[data-project=sample]').click();if(await page.locator('html').getAttribute('data-theme')!=='black')await page.locator('#theme').click();
  for(const s of ['quota','failure','long','offline']){await scenario(s);await stage('progress');await page.locator('[data-tab=work]').click();await fit(s);await snap(`journey-black-390-${s}`);if(s==='offline'){await stage('plan');assert.match(await page.locator('.banner').innerText(),/마지막/);}}
  await page.locator('#run').selectOption('previous');for(const s of ['quota','failure']){await scenario(s);await stage('progress');assert.equal(await page.getByText('모든 후보 대기',{exact:true}).count(),0);assert.equal(await page.getByText('자동 재시도 중단',{exact:true}).count(),0);}await scenario('normal');await page.locator('#run').selectOption('previous');await stage('approval');await scenario('offline');await click('decision');await page.locator('#decision-choice').selectOption('hold');await page.locator('#decision-reviewed').check();await click('decision-submit');assert.equal(await page.locator('#detail').isVisible(),true);await page.keyboard.press('Escape');
  checks.push('All-candidate waiting, bounded failures, long Korean, offline actions preserve records');
  await scenario('normal');await stage('projects');await scenario('empty');await page.locator('[data-project=new]').waitFor();assert.equal(await page.getByRole('heading',{name:'아직 없어요'}).count(),0);await fit('empty with created project');
  await scenario('normal');await page.locator('[data-project=sample]').click();await page.locator('#run').selectOption('current');await stage('progress');await page.locator('[data-tab=work]').click();await page.setViewportSize({width:320,height:900});await page.addStyleTag({content:':root{font-size:32px}p,.task strong{font-size:32px!important}button{min-height:44px}'});await fit('text zoom 200%');await snap('text-zoom-320');
  await page.reload();for(let i=0;i<2;i++){await page.locator('.brand').click();await page.getByRole('heading',{name:'프로젝트',exact:true}).waitFor();await page.locator('[data-project=sample]').click();}await page.keyboard.press('Tab');await page.keyboard.press('Tab');assert.ok(await page.evaluate(()=>document.activeElement!==document.body));
  const small=await page.locator('button:visible').evaluateAll(es=>es.filter(e=>e.getBoundingClientRect().height<43.5).map(e=>({text:e.textContent,h:e.getBoundingClientRect().height})));assert.deepEqual(small,[]);
  checks.push('Empty state, keyboard focus, 200% text exercise, 44px visible button targets');
  assert.deepEqual(errors,[]);await fs.writeFile(path.join(out,'task-workspace-validation.json'),JSON.stringify({status:'PASS',browser:browser.version(),sandbox:true,source:'synthetic offline fixtures',checks,fonts,requests,limits:['No live API/model/CI evidence','No novice usability study','No physical APK or service-worker test']},null,2));
  console.log(JSON.stringify({status:'PASS',checks}));
 }catch(e){await snap('failure').catch(()=>{});await fs.writeFile(path.join(out,'task-workspace-failure.json'),JSON.stringify({error:e.stack,errors,checks},null,2));throw e;}finally{await browser.close();}
})();
