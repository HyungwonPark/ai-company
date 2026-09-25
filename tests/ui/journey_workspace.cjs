/* Independent journey read-path review: real temporary API, fixture execution facts. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
(async()=>{
 const base=process.env.BASE_URL,fixtures=JSON.parse(process.env.GRAPH_FIXTURES),out=process.env.UI_OUTPUT||'/tmp/ai-company-journey-ui';
 assert.equal(new URL(base).hostname,'127.0.0.1','only the temporary loopback API is allowed');
 await fs.mkdir(out,{recursive:true});
 const browser=await chromium.launch({headless:true,chromiumSandbox:true,...(process.env.CHROME_CHANNEL?{channel:process.env.CHROME_CHANNEL}:{}),...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
 const context=await browser.newContext({viewport:{width:1440,height:1000},serviceWorkers:'block',reducedMotion:'reduce'}),page=await context.newPage();page.setDefaultTimeout(10000);
 const errors=[],writes=[],violations=[],checks=[],screens=[];let currentCase='login';
 await context.route('**/*',route=>{const request=route.request(),url=new URL(request.url());if(url.origin!==base||request.method()!=='GET'&&!['/api/login','/api/password'].includes(url.pathname)){violations.push({method:request.method(),path:url.pathname});return route.abort();}if(request.method()!=='GET')writes.push(url.pathname);return route.continue();});
 page.on('pageerror',error=>errors.push(error.message));
 const route=()=>new URLSearchParams(new URL(page.url()).hash.split('?')[1]||'');
 const go=async hash=>{await page.evaluate(value=>{location.hash=value;},hash);await page.waitForTimeout(150);};
 const readOverview=()=>page.evaluate(async id=>(await(await fetch('/api/projects/'+id+'/overview')).json()),fixtures.project_id);
 const graph=()=>page.locator('[data-rg-root]');
 const nav=label=>page.locator('.nav').getByRole('link',{name:label,exact:true});
 const scoped=async(run,view)=>{assert.equal(route().get('project'),fixtures.project_id);assert.equal(route().get('run'),run,view+' retains the explicit execution');};
 const screenshot=async name=>{await page.evaluate(()=>document.fonts.ready);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),name+' page overflow');const file='journey-'+name+'.png';await page.screenshot({path:path.join(out,file),fullPage:true});screens.push(file);};
 const reportIds=()=>page.locator('.report-list [data-document-id^="report:"]').evaluateAll(items=>items.map(el=>el.dataset.documentId.slice(7)).sort());
 try{
  await page.goto(base+'/');await page.getByLabel('비밀번호',{exact:true}).fill(process.env.TEST_PASSWORD);await page.getByRole('button',{name:'로그인',exact:true}).click();
  await page.getByLabel('현재 비밀번호',{exact:true}).fill(process.env.TEST_PASSWORD);await page.getByLabel('새 비밀번호',{exact:true}).fill('journey-browser-fixture-only-password');await page.getByLabel('새 비밀번호 확인',{exact:true}).fill('journey-browser-fixture-only-password');await page.getByRole('button',{name:'비밀번호 변경 후 계속'}).click();await page.locator('.project-list-card').first().waitFor();
  const before=await readOverview(),snapshots=before.workspace_graph.snapshots,current=snapshots.find(s=>s.run_id===fixtures.run_ids[1]),old=snapshots.find(s=>s.run_id===fixtures.run_ids[0]),planned=snapshots.find(s=>!s.run_id);
  assert.ok(current&&old&&planned);assert.equal(current.approval_refs.length,0);assert.equal(old.approval_refs.length,1);
  currentCase='five stages and project search';
  for(const label of ['프로젝트','계획','진행','승인','결과'])assert.equal(await nav(label).count(),1,label+' visible navigation');
  await page.getByLabel('찾기',{exact:true}).fill('역할 상태');assert.equal(await page.locator('.project-list-card').count(),1);
  const recent=page.locator('.project-list-card a[href^="#progress"]');assert.equal(new URLSearchParams((await recent.getAttribute('href')).split('?')[1]).get('run'),current.run_id,'project recent link names the real run');await recent.click();await graph().waitFor();await scoped(current.run_id,'project entry');assert.equal(await graph().getAttribute('data-snapshot'),current.id);
  checks.push('project search, five journey links, real recent-run entry');
  for(const width of [320,390,1440])for(const theme of ['light','black']){
   currentCase={width,theme,stage:'same-run progress/result/approval'};await page.setViewportSize({width,height:width>700?1000:844});await page.locator(`[data-theme-choice="${theme}"]`).click();
   await go('#progress?project='+fixtures.project_id+'&run='+current.run_id);await graph().waitFor();assert.equal(await graph().getAttribute('data-snapshot'),current.id);await screenshot(`${theme}-${width}-progress`);
   await nav('결과').click();await page.locator('.report-list').waitFor();await scoped(current.run_id,'result');assert.deepEqual(await reportIds(),current.report_refs.map(r=>r.id).sort(),'only selected-run report identifiers');assert.equal(await page.locator('.journey-result').getAttribute('data-result-run'),current.run_id,'result summary explicitly identifies its run');await screenshot(`${theme}-${width}-result`);
   await nav('승인').click();await scoped(current.run_id,'approval');assert.equal(await page.locator('.approval-reading').count(),0,'current execution must not borrow an older pending request');
   await nav('진행').click();await graph().waitFor();await scoped(current.run_id,'return');assert.equal(await graph().getAttribute('data-snapshot'),current.id);
  }
  checks.push('Light/Black × 320/390/1440 actual navigation, selected-run result, no foreign pending approval, no horizontal overflow');
  currentCase='old execution and history';await page.setViewportSize({width:390,height:844});await page.locator('#journey-run').selectOption(old.id);await page.waitForFunction(id=>new URLSearchParams(location.hash.split('?')[1]).get('run')===id,old.run_id);await scoped(old.run_id,'snapshot selection');
  await nav('결과').click();await page.locator('.report-list').waitFor();assert.deepEqual(await reportIds(),old.report_refs.map(r=>r.id).sort(),'old execution exact reports');assert.equal(await page.locator('.journey-result').getAttribute('data-result-run'),old.run_id);if(before.project_report?.run_id!==old.run_id)assert.equal(await page.locator('.project-summary').count(),0,'latest-plan aggregate cannot be reused as old-run report');await nav('승인').click();await page.locator('.approval-reading').waitFor();await scoped(old.run_id,'old approval');
  assert.equal(await page.locator('.approval-reading [data-decision="approve"]').getAttribute('data-id'),old.approval_refs[0].id);await screenshot('old-run-approval');
  await page.goBack();await page.locator('.report-list').waitFor();await scoped(old.run_id,'browser back');await page.reload();await page.locator('.report-list').waitFor();await scoped(old.run_id,'reload');assert.deepEqual(await reportIds(),old.report_refs.map(r=>r.id).sort());
  await nav('진행').click();await graph().waitFor();assert.equal(await graph().getAttribute('data-snapshot'),old.id);checks.push('snapshot selection updates URL; older exact reports/approval preserved through navigation, real back and reload');
  currentCase='planned snapshot has no run';await page.locator('#journey-run').selectOption(planned.id);await page.waitForFunction(id=>new URLSearchParams(location.hash.split('?')[1]).get('snapshot')===id,planned.id);assert.equal(route().get('run'),null,'planned snapshot cannot inherit previous real run');assert.ok(await page.locator('.rg-node.is-planned').count());await nav('결과').click();assert.equal(await reportIds().then(ids=>ids.length),0,'planned snapshot cannot borrow a completed run report');checks.push('unconfirmed planned snapshot is explicit and never borrows execution reports');
  currentCase='missing and cross-project run';
  for(const view of ['progress','reports','approvals']){
   await go('#'+view+'?project='+fixtures.project_id+'&run=missing-journey-run');await page.getByText(/해당 실행을 확인할 수 없/).first().waitFor();assert.equal(route().get('run'),'missing-journey-run');assert.equal(await page.locator('.rg-node,.approval-reading,.report-list [data-document-id]').count(),0,'missing run displays no unrelated records');
  }
  await go('#progress?project='+fixtures.other_project_id+'&run='+old.run_id);await page.getByText(/해당 실행을 확인할 수 없/).first().waitFor();assert.equal(await page.locator('.rg-node').count(),0);
  await go('#progress?project=missing-journey-project&run='+old.run_id);await page.getByText('프로젝트를 열 수 없습니다',{exact:true}).waitFor();assert.equal(await page.locator('.rg-node,.approval-reading').count(),0);checks.push('unknown/out-of-scope run and project fail closed, without silently opening latest records');
  currentCase='late response';const endpoint='/api/projects/'+fixtures.project_id+'/overview';let release,intercepted;
  const held=new Promise(resolve=>{intercepted=resolve;});const gate=new Promise(resolve=>{release=resolve;});let once=true;
  await page.route('**'+endpoint,async handler=>{if(!once)return handler.continue();once=false;const response=await handler.fetch();intercepted();await gate;await handler.fulfill({response});});
  await go('#progress?project='+fixtures.project_id+'&run='+current.run_id);await held;await go('#progress?project='+fixtures.other_project_id);await page.waitForFunction(id=>document.querySelector('#project-select')?.value===id,fixtures.other_project_id);release();await page.waitForTimeout(400);assert.equal(route().get('project'),fixtures.other_project_id);assert.equal(await page.locator('.rg-node').count(),0,'late previous-project overview cannot replace selected project');await page.unroute('**'+endpoint);checks.push('deliberately delayed actual API response cannot replace a subsequently selected project');
  currentCase='offline/401';await go('#progress?project='+fixtures.project_id+'&run='+current.run_id);await graph().waitFor();await context.setOffline(true);await page.evaluate(()=>window.dispatchEvent(new Event('offline')));await page.getByText(/연결이 끊겼습니다/).first().waitFor();assert.equal(await graph().getAttribute('data-snapshot'),current.id);await context.setOffline(false);await page.evaluate(()=>window.dispatchEvent(new Event('online')));
  // Reconnection and the normal polling timer already fetch. Waiting for the
  // response avoids racing a refresh button that a correct 401 removes.
  const expiryRead=page.waitForResponse(response=>new URL(response.url()).pathname===endpoint&&response.status()===401,{timeout:12000});await page.route('**'+endpoint,handler=>handler.fulfill({status:401,contentType:'application/json',json:{error:{code:'unauthorized'}}}));await expiryRead;await page.getByRole('button',{name:'로그인',exact:true}).waitFor();assert.equal(await page.locator('.rg-node,.approval-reading,.report-list').count(),0);checks.push('offline reads retain last selected execution; actual automatic read of injected 401 removes private views');
  assert.deepEqual(errors,[]);assert.deepEqual(violations,[]);assert.deepEqual(writes,['/api/login','/api/password']);
  const evidence={status:'PASS',source:'temporary SQLite and actual ManagementHTTPServer; execution facts are synthetic; delayed read/offline/401 injections explicitly simulated',browser:browser.version(),sandbox:true,checks,write_paths:writes,artifacts:screens};await fs.writeFile(path.join(out,'journey-workspace-validation.json'),JSON.stringify(evidence,null,2));console.log(JSON.stringify({status:'PASS',checks}));
 }catch(error){await fs.writeFile(path.join(out,'journey-workspace-failure.json'),JSON.stringify({case:currentCase,error:error.message,checks,errors,violations},null,2));await page.screenshot({path:path.join(out,'journey-workspace-failure.png'),fullPage:true}).catch(()=>{});throw error;}
 finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
