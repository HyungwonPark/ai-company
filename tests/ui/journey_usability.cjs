/* Independent U1–U3 browser review. Real read API and explicitly frozen preview. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const {createHash}=require('node:crypto');
const digest=bytes=>createHash('sha256').update(bytes).digest('hex');
(async()=>{
 const base=process.env.BASE_URL,fixtures=JSON.parse(process.env.GRAPH_FIXTURES),out=process.env.UI_OUTPUT||'/tmp/ai-company-usability';
 assert.equal(new URL(base).hostname,'127.0.0.1');await fs.mkdir(out,{recursive:true});
 const browser=await chromium.launch({headless:true,chromiumSandbox:true,...(process.env.CHROME_CHANNEL?{channel:process.env.CHROME_CHANNEL}:{}),...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
 const checks=[],screens=[],errors=[],violations=[],comparisons=[];let currentCase='login',page;
 const screenshot=async name=>{await page.evaluate(()=>document.fonts.ready);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),name+' page overflow');await page.screenshot({path:path.join(out,name),fullPage:true});screens.push(name);};
 const go=async hash=>{await page.evaluate(value=>{location.hash=value;},hash);await page.waitForTimeout(120);};
 const route=()=>new URLSearchParams(new URL(page.url()).hash.split('?')[1]);
 const currentRun=fixtures.run_ids[1],oldRun=fixtures.run_ids[0],pid=fixtures.project_id;
 try{
  const context=await browser.newContext({viewport:{width:390,height:844},serviceWorkers:'block',reducedMotion:'reduce'});page=await context.newPage();page.setDefaultTimeout(12000);
  page.on('pageerror',error=>errors.push(error.message));
  await context.route('**/*',handler=>{const request=handler.request(),url=new URL(request.url());if(url.origin!==base||request.method()!=='GET'&&!['/api/login','/api/password'].includes(url.pathname)){violations.push({method:request.method(),path:url.pathname});return handler.abort();}return handler.continue();});
  await page.goto(base);await page.getByLabel('비밀번호',{exact:true}).fill(process.env.TEST_PASSWORD);await page.getByRole('button',{name:'로그인',exact:true}).click();
  await page.getByLabel('현재 비밀번호',{exact:true}).fill(process.env.TEST_PASSWORD);await page.getByLabel('새 비밀번호',{exact:true}).fill('journey-usability-fixture-password');await page.getByLabel('새 비밀번호 확인',{exact:true}).fill('journey-usability-fixture-password');await page.getByRole('button',{name:'비밀번호 변경 후 계속'}).click();await page.locator('.project-list-card').first().waitFor();
  const overview=await page.evaluate(async id=>await(await fetch('/api/projects/'+id+'/overview')).json(),pid);
  const old=overview.workspace_graph.snapshots.find(item=>item.run_id===oldRun),recent=overview.workspace_graph.snapshots.find(item=>item.run_id===currentRun),approval=overview.approvals.find(item=>item.id===old.approval_refs[0].id);assert.ok(approval);
  for(const width of [320,390,1440])for(const theme of ['light','black']){
   currentCase={source:'authenticated API',width,theme};await page.setViewportSize({width,height:width>700?1000:844});await page.locator(`[data-theme-choice="${theme}"]`).click();
   await go(`#progress?project=${pid}&run=${currentRun}`);await page.locator('[data-rg-root]').waitFor();
   assert.equal(await page.locator('#journey-run').count(),1);assert.equal(await page.locator('#rg-snapshot:visible').count(),0,'one integrated execution selector');
   assert.equal(await page.locator('#main h1').filter({hasText:'진행'}).count(),1);assert.equal(await page.locator('.rg-header h2:visible').count(),0,'no duplicate progress heading');
   assert.equal(await page.locator('[data-rg-root]').getAttribute('data-snapshot'),recent.id);
   const visibleText=await page.locator('#main').innerText();assert.ok(!visibleText.includes('조회 순서'),'internal cursor belongs in collapsed detail');
   await screenshot(`usability-api-${theme}-${width}-progress-long.png`);
   await page.locator('.nav').getByRole('link',{name:'결과',exact:true}).click();await page.locator('.journey-result').waitFor();
   const summary=page.locator('.journey-result-summary');await summary.waitFor();
   assert.equal(await page.locator('.journey-result header[aria-label="현재 결론"] h2').count(),1);
   for(const label of ['남은 일','다음 행동'])assert.equal(await summary.getByText(label,{exact:true}).count(),1,label+' occurs once in first result summary');
   assert.equal(await page.locator('.journey-result').getAttribute('data-result-run'),currentRun);
   assert.equal(await page.locator('.result-role-details[open]').count(),0,'role evidence starts collapsed');assert.equal(await page.locator('.role-counts').count(),1,'one compact role count row');
   const firstDetails=page.locator('.result-role-details').first();await firstDetails.locator(':scope > summary').click();assert.ok(await firstDetails.getAttribute('open')!==null);assert.ok((await firstDetails.textContent()).includes('원문 상태'));await firstDetails.locator(':scope > summary').click();
   await screenshot(`usability-api-${theme}-${width}-result.png`);
   await page.locator('.nav').getByRole('link',{name:'승인',exact:true}).click();await page.getByText('이 대상의 승인 요청 없음',{exact:true}).waitFor();assert.equal(route().get('run'),currentRun);assert.equal(await page.locator('.approval-reading').count(),0,'no old approval borrowed');
   await page.locator('.journey-all-approvals').click();await page.locator('.approval-reading').waitFor();assert.equal(route().get('all'),'1');
   assert.match(await page.locator('.journey-context').innerText(),/결정할 요청\s*1\s*건/);assert.match(await page.locator('[data-approval-counts]').innerText(),/조회된 요청\s*1\s*건/);
   const selected=page.locator('[aria-label="선택한 승인"]');assert.ok((await selected.innerText()).includes(approval.title));assert.ok((await selected.textContent()).includes(approval.subject_digest));assert.ok((await selected.innerText()).includes('변경 영향'));
   await page.getByRole('link',{name:'이 실행의 요청만 보기',exact:true}).click();await page.locator('.approval-reading').waitFor();assert.equal(route().get('run'),oldRun);
   await page.locator('.nav').getByRole('link',{name:'결과',exact:true}).click();await page.locator('.journey-result').waitFor();assert.equal(route().get('run'),oldRun);
   await page.goBack();await page.locator('.approval-reading').waitFor();assert.equal(route().get('run'),oldRun);checks.push({width,theme,flow:'selected progress → exact result → empty selected approvals → project requests → exact old execution → result → real back',business_writes:0});
  }
  currentCase='keyboard and emulated pinch zoom';await page.setViewportSize({width:390,height:844});await go(`#progress?project=${pid}&run=${currentRun}`);await page.locator('[data-rg-root]').waitFor();
  await page.locator('.skip-link').focus();await page.keyboard.press('Enter');assert.equal(await page.evaluate(()=>document.activeElement.id),'main','skip link still focuses reading start');
  await page.keyboard.press('Tab');const keyboard=await page.evaluate(()=>{const el=document.activeElement,s=getComputedStyle(el);return {tag:el.tagName,text:el.textContent.slice(0,80),outline:s.outlineStyle,outlineWidth:s.outlineWidth,shadow:s.boxShadow};});
  assert.ok(keyboard.tag!=='BODY');assert.ok(keyboard.outline!=='none'&&parseFloat(keyboard.outlineWidth)>0||keyboard.shadow!=='none','keyboard focus is visibly drawn');await screenshot('usability-keyboard.png');
  const cdp=await context.newCDPSession(page);await cdp.send('Emulation.setPageScaleFactor',{pageScaleFactor:2});assert.equal(await page.evaluate(()=>visualViewport.scale),2);await page.screenshot({path:path.join(out,'usability-pinch-zoom-200.png'),fullPage:false});screens.push('usability-pinch-zoom-200.png');await cdp.send('Emulation.setPageScaleFactor',{pageScaleFactor:1});checks.push({keyboard,zoom:'actual Chrome visualViewport scale 2 through CDP; device pinch not tested'});
  currentCase='empty error waiting and unknown';
  for(const name of ['scheduled','unknown','rejected']){
   const item=fixtures.status_cases.find(value=>value.name===name);await go(`#reports?project=${pid}&run=${item.run_id}`);await page.locator(`.journey-result[data-result-run="${item.run_id}"]`).waitFor();assert.equal(await page.locator('.journey-result header h2').innerText(),item.title);await screenshot(`usability-state-${name}.png`);
  }
  await go(`#reports?project=${pid}&run=missing-run`);await page.getByText(/해당 실행을 확인할 수 없/).first().waitFor();assert.equal(await page.locator('.journey-result').count(),0);assert.equal(await page.locator('.empty-mark').count(),0);await screenshot('usability-state-empty-error.png');
  currentCase='synthetic browser clock expiry, open confirmation and refresh';
  // The API fixture's original deadline/status/digest stay unchanged. Only this
  // browser clock moves to either side of that exact deadline; no decision POST.
  await page.clock.install({time:Math.trunc(approval.expires_at*1000)-2000});
  await go(`#approvals?project=${pid}&run=${oldRun}`);await page.locator('.approval-reading').waitFor();
  await page.locator(`[data-decision="approve"][data-id="${approval.id}"]`).click();await page.locator('#decision-form').waitFor();
  assert.equal(await page.locator('#decision-form button[type="submit"]').isEnabled(),true);
  await page.clock.fastForward(2100);
  await page.waitForFunction(()=>document.querySelector('#decision-form button[type="submit"]')?.disabled);
  assert.match(await page.locator('.approval-reading nav').innerText(),/만료/);assert.match(await page.locator('.approval-head').innerText(),/만료/);
  assert.equal(await page.locator('[data-decision]:enabled').count(),0);
  await page.locator('dialog [data-action="close-dialog"]').first().click();
  const unchanged=await page.evaluate(async id=>await(await fetch('/api/projects/'+id+'/overview')).json(),pid);
  assert.deepEqual(unchanged.approvals.find(item=>item.id===approval.id),approval,'display clock cannot change stored approval facts');
  await page.reload();await page.locator('.approval-reading').waitFor();assert.match(await page.locator('.approval-head').innerText(),/만료/);
  checks.push({expiry:'before → exact-boundary/past timer → refresh',clock:'browser-only simulated at original API fixture deadline',confirmation:'opened before expiry; disabled automatically without submission',original_status:approval.status,original_digest_preserved:true});
  assert.deepEqual(errors,[]);assert.deepEqual(violations,[]);await context.close();
  // Same immutable preview fixture, themes, widths, route and collapsed details.
  let fixedFixture;
  for(const version of ['before','after']){
   const html=process.env[version==='before'?'USABILITY_BEFORE_HTML':'USABILITY_AFTER_HTML'];assert.ok(html);
   for(const width of [320,390,1440])for(const theme of ['light','black']){
    currentCase={source:'immutable packaged preview',version,width,theme};
    const previewContext=await browser.newContext({viewport:{width,height:width>700?1000:844},serviceWorkers:'block',reducedMotion:'reduce'});page=await previewContext.newPage();page.setDefaultTimeout(12000);page.on('pageerror',error=>errors.push(error.message));
    page.on('request',request=>{if(/^https?:/.test(request.url()))violations.push({preview:version,url:request.url()});});
    await page.goto(pathToFileURL(html).href+'#projects');await page.locator('.project-list-card').first().waitFor();
    const frozen=await page.evaluate(()=>JSON.parse(document.getElementById('preview-fixture').textContent));if(!fixedFixture)fixedFixture=frozen;else assert.deepEqual(frozen,fixedFixture,'before and after use identical original facts');
    await page.locator(`[data-theme-choice="${theme}"]`).click();const fpid=frozen.fixtures.project_id,oldId=frozen.fixtures.run_ids[0],recentId=frozen.fixtures.run_ids[1];
    for(const [view,run] of [['progress',recentId],['reports',recentId],['approvals',oldId]]){
     await go(`#${view}?project=${fpid}&run=${run}`);await page.locator(view==='progress'?'[data-rg-root]':view==='reports'?'.journey-result':'.approval-reading').waitFor();
     await page.locator('details[open]').evaluateAll(items=>items.forEach(item=>item.removeAttribute('open')));
     const metrics=await page.evaluate(()=>({height:document.documentElement.scrollHeight,mainTop:document.querySelector('#main').getBoundingClientRect().top,previewBannerHeight:document.querySelector('#preview-banner')?.getBoundingClientRect().height,mainHeadingCount:document.querySelectorAll('#main h1').length,visibleSelectors:[...document.querySelectorAll('#main select')].filter(el=>el.getBoundingClientRect().height).length,badges:[...document.querySelectorAll('#main .badge')].filter(el=>el.getBoundingClientRect().height).map(el=>({text:el.innerText,font:parseFloat(getComputedStyle(el).fontSize)}))}));
     if(version==='after'){
      for(const badge of metrics.badges)assert.ok(badge.font>=14,`important badge ${badge.text} >=14px`);
      if(view==='approvals'){
       const list=await page.locator('.approval-reading nav').innerText(),detail=await page.locator('.approval-head').innerText();assert.match(list,/만료/);assert.match(detail,/만료/);
       assert.equal(await page.locator('[data-decision]:enabled').count(),0,'all expired decisions denied before submission');
      }
     }
     await screenshot(`usability-${version}-${theme}-${width}-${view}.png`);comparisons.push({version,width,theme,view,run,details:'closed',...metrics});
    }
    assert.ok((await page.evaluate(()=>window.PREVIEW_AUDIT)).every(item=>item.method==='GET'));await previewContext.close();
   }
  }
  assert.deepEqual(errors,[]);assert.deepEqual(violations,[]);
  const result={status:'PASS',browser:browser.version(),sandbox:true,scope:'authenticated temporary real API + immutable packaged before/after; synthetic saved facts, no production/models/APK',checks,comparisons,fixture_sha256:digest(JSON.stringify(fixedFixture)),artifacts:screens,limitations:['Keyboard and CDP pinch zoom are desktop Chrome emulation; physical Android not tested','No approval decision submitted','Page height is observational, not acceptance score']};
  await fs.writeFile(path.join(out,'journey-usability-validation.json'),JSON.stringify(result,null,2));console.log(JSON.stringify({status:'PASS',flows:checks.length,comparisons:comparisons.length,screens:screens.length}));
 }catch(error){await fs.writeFile(path.join(out,'journey-usability-failure.json'),JSON.stringify({status:'FAIL',currentCase,error:error.message,checks,comparisons,errors,violations},null,2));if(page&&!page.isClosed())await page.screenshot({path:path.join(out,'journey-usability-failure.png'),fullPage:true}).catch(()=>{});throw error;}
 finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
