/* Independently authored R25-2 browser regression. Real temporary SQLite/API only. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
(async()=>{
 const base=process.env.BASE_URL,fixtures=JSON.parse(process.env.GRAPH_FIXTURES),out=process.env.UI_OUTPUT||'/tmp/ai-company-journey-status';
 assert.equal(new URL(base).hostname,'127.0.0.1');await fs.mkdir(out,{recursive:true});
 const browser=await chromium.launch({headless:true,chromiumSandbox:true,...(process.env.CHROME_CHANNEL?{channel:process.env.CHROME_CHANNEL}:{}),...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
 const context=await browser.newContext({viewport:{width:390,height:844},serviceWorkers:'block',reducedMotion:'reduce'}),page=await context.newPage();page.setDefaultTimeout(10000);
 const errors=[],violations=[],checks=[],screens=[];let active='login';
 page.on('pageerror',error=>errors.push(error.message));
 await context.route('**/*',route=>{const req=route.request(),url=new URL(req.url());if(url.origin!==base||req.method()!=='GET'&&!['/api/login','/api/password'].includes(url.pathname)){violations.push({method:req.method(),path:url.pathname});return route.abort();}return route.continue();});
 try{
  await page.goto(base);await page.getByLabel('비밀번호',{exact:true}).fill(process.env.TEST_PASSWORD);await page.getByRole('button',{name:'로그인',exact:true}).click();
  await page.getByLabel('현재 비밀번호',{exact:true}).fill(process.env.TEST_PASSWORD);await page.getByLabel('새 비밀번호',{exact:true}).fill('journey-status-fixture-password');await page.getByLabel('새 비밀번호 확인',{exact:true}).fill('journey-status-fixture-password');await page.getByRole('button',{name:'비밀번호 변경 후 계속'}).click();await page.locator('.project-list-card').first().waitFor();
  const overview=await page.evaluate(async id=>await(await fetch('/api/projects/'+id+'/overview')).json(),fixtures.project_id);
  for(const width of [320,390,1440])for(const theme of ['light','black']){
   await page.setViewportSize({width,height:width>700?1000:844});await page.locator(`[data-theme-choice="${theme}"]`).click();
   for(const item of fixtures.status_cases){
    active={width,theme,case:item.name};
    await page.evaluate(hash=>{location.hash=hash;},'#reports?project='+fixtures.project_id+'&run='+item.run_id);
    const result=page.locator('.journey-result[data-result-run="'+item.run_id+'"]');await result.waitFor();
    assert.equal(await result.locator('header h2').innerText(),item.title);
    const selectorText=await page.locator('#journey-run option:checked').innerText();
    if(item.state==='waiting'&&item.name!=='scheduled')assert.doesNotMatch(selectorText,/예약/,'a waiting run without a scheduled retry cannot promise a reservation in the selector');
    const groups=await result.locator('.summary-grid > section').evaluateAll(items=>Object.fromEntries(items.map(item=>[item.querySelector('h3').childNodes[0].textContent.trim(),Number(item.querySelector('h3 span').textContent)])));
    assert.deepEqual(groups,Object.fromEntries(['완료','진행','대기','확인 필요','미확인'].map(title=>[title,item.groups[title]||0])));
    for(const status of item.statuses)assert.ok((await result.innerText()).includes(status)||await result.locator('details').evaluateAll((items,value)=>items.some(el=>el.textContent.includes(value)),status),'raw '+status+' available in original-state detail');
    assert.ok(await result.getByText('원문 상태',{exact:true}).count());
    if(overview.project_report?.run_id!==item.run_id)assert.equal(await page.locator('.project-summary').count(),0,'current-plan report not borrowed');
    else {
     const aggregate=page.locator('.project-summary');assert.equal(await aggregate.count(),1);
     assert.equal(await aggregate.locator('.summary-grid').count(),0,'same-run aggregate retained as collapsed evidence, not duplicate groups');
     assert.equal(await page.locator('.result-system-details').evaluate(el=>el.open),false);
     if(item.groups['확인 필요']||item.groups['미확인'])assert.ok(!(await result.locator('.journey-result-summary').innerText()).includes('산출물과 같은 후보의 검사를 이어갑니다'),'no contradictory next action');
    }
    assert.equal(await result.locator('a').filter({hasText:'승인 요청 확인'}).count(),0,'no unrelated approval borrowed');
    if(['completed-operator','operator-handoff','unknown','rejected','superseded'].includes(item.name)){
     if(!await result.locator('.result-role-details').evaluate(el=>el.open))await result.locator('.result-role-details > summary').click();
     await result.getByText('원문 상태',{exact:true}).first().click();
     await page.evaluate(()=>document.fonts.ready);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
     const name=`journey-status-${theme}-${width}-${item.name}.png`;await page.screenshot({path:path.join(out,name),fullPage:true});screens.push(name);
    }
    await page.locator('.nav').getByRole('link',{name:'진행',exact:true}).click();const graph=page.locator('[data-rg-root]');await graph.waitFor();
    assert.equal(await graph.getAttribute('data-snapshot'),item.snapshot_id);assert.equal(await page.locator('.journey-next strong').innerText(),item.title);
    const snapshot=overview.workspace_graph.snapshots.find(s=>s.id===item.snapshot_id);assert.deepEqual(snapshot.nodes.filter(n=>n.kind==='role').map(n=>n.status).sort(),[...item.statuses].sort());
    assert.equal(new URLSearchParams(new URL(page.url()).hash.split('?')[1]).get('run'),item.run_id);
    checks.push({...active,groups,title:item.title,selector_text:selectorText,run_id:item.run_id});
   }
  }
  assert.deepEqual(errors,[]);assert.deepEqual(violations,[]);
  const evidence={status:'PASS',browser:browser.version(),sandbox:true,source:'synthetic saved statuses in temporary SQLite, real ManagementHTTPServer and browser; no models',checks,artifacts:screens,limitations:['No operational DB, APK or approval mutation','Authentication fixture writes excluded from business-table comparison']};
  await fs.writeFile(path.join(out,'journey-status-validation.json'),JSON.stringify(evidence,null,2));console.log(JSON.stringify({status:'PASS',cases:checks.length}));
 }catch(error){await fs.writeFile(path.join(out,'journey-status-failure.json'),JSON.stringify({status:'FAIL',case:active,error:error.message,checks,errors,violations},null,2));await page.screenshot({path:path.join(out,'journey-status-failure.png'),fullPage:true}).catch(()=>{});throw error;}
 finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
