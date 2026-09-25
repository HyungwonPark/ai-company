/* APK start URL contract exercised by a real browser against an ephemeral API.
 * Closing/reopening browser contexts is not Android installation/device evidence. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
(async()=>{
 const base=process.env.BASE_URL,fixtures=JSON.parse(process.env.GRAPH_FIXTURES),out=process.env.UI_OUTPUT||'/tmp/ai-company-mobile-entry';
 assert.equal(new URL(base).hostname,'127.0.0.1','only the ephemeral loopback server is permitted');
 await fs.mkdir(out,{recursive:true});
 const browser=await chromium.launch({headless:true,chromiumSandbox:true,...(process.env.CHROME_CHANNEL?{channel:process.env.CHROME_CHANNEL}:{}),...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
 const errors=[],violations=[],writes=[],checks=[];let password=process.env.TEST_PASSWORD,context,page,currentCase;
 const open=async(width,storageState)=>{
  context=await browser.newContext({viewport:{width,height:width>700?1000:844},serviceWorkers:'block',reducedMotion:'reduce',...(storageState?{storageState}:{})});
  await context.route('**/*',async route=>{const request=route.request(),url=new URL(request.url());if(url.origin!==base||request.method()!=='GET'&&!['/api/login','/api/password'].includes(url.pathname)){violations.push({method:request.method(),path:url.pathname});return route.abort();}if(request.method()!=='GET')writes.push(url.pathname);return route.continue();});
  page=await context.newPage();page.setDefaultTimeout(12000);page.on('pageerror',error=>errors.push(error.message));
  await page.goto(base+'/');assert.equal(new URL(page.url()).search,'','the icon route must not require a workspace query');
 };
 const screenshot=async name=>{await page.evaluate(()=>document.fonts.ready);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no page overflow');await page.screenshot({path:path.join(out,`mobile-entry-${name}.png`),fullPage:true});};
 const pickProject=async()=>{
  const card=page.locator('.project-list-card h2 a').filter({hasText:'역할 상태 확인 · 그래프 예시'});await card.waitFor();assert.equal(await card.getAttribute('href'),'#manager?project='+fixtures.project_id);await card.click();
  await page.locator('#message-form').waitFor();assert.equal(await page.locator('#project-select').inputValue(),fixtures.project_id);
 };
 const progress=async()=>{await page.locator('.nav a[aria-label="진행"]').click();await page.locator('[data-rg-root]').waitFor();assert.ok(await page.locator('#app').evaluate(el=>el.classList.contains('integrated-workspace')));assert.equal(new URL(page.url()).search,'');};
 const overview=()=>page.evaluate(async id=>{const response=await fetch('/api/projects/'+id+'/overview');if(!response.ok)throw new Error('fixture overview unavailable');return response.json();},fixtures.project_id);
 const graphSelection=async snapshot=>{await page.locator('#rg-snapshot').selectOption(snapshot.id);await page.waitForFunction(id=>document.querySelector('[data-rg-root]')?.dataset.snapshot===id,snapshot.id);assert.equal(await page.locator('.rg-edge').count(),snapshot.edges.length);await page.locator('.rg-node').first().click();await page.locator('.rg-detail.is-open').waitFor();};
 const backToGraph=async snapshot=>{await page.goBack();await page.locator('[data-rg-root]').waitFor();assert.equal(await page.locator('#rg-snapshot').inputValue(),snapshot.id);const params=new URLSearchParams(new URL(page.url()).hash.split('?')[1]);assert.equal(params.get('project'),fixtures.project_id);assert.equal(params.get('run'),snapshot.run_id);};
 const clear=async()=>{if(await page.locator('.rg-detail.is-open').count())await page.getByRole('button',{name:'상세 닫기',exact:true}).click();};
 try{
  for(const width of [320,390,1440])for(const theme of ['light','black']){
   currentCase={width,theme,stage:'login at icon URL'};await open(width);
   await page.getByLabel('비밀번호',{exact:true}).fill(password);await page.getByRole('button',{name:'로그인',exact:true}).click();
   if(checks.length===0){await page.getByLabel('현재 비밀번호',{exact:true}).fill(password);password='mobile-entry-fixture-only-password';await page.getByLabel('새 비밀번호',{exact:true}).fill(password);await page.getByLabel('새 비밀번호 확인',{exact:true}).fill(password);await page.getByRole('button',{name:'비밀번호 변경 후 계속'}).click();}
   await page.locator('.project-list-card').first().waitFor();await page.locator(`[data-theme-choice="${theme}"]`).click();await screenshot(`${theme}-${width}-projects`);
   currentCase.stage='project and default graph';await pickProject();const before=await overview();assert.equal(before.project.source,'fixture');const snapshots=before.workspace_graph.snapshots,current=snapshots.find(s=>s.run_id===fixtures.run_ids[1]),old=snapshots.find(s=>s.run_id===fixtures.run_ids[0]);assert.ok(current&&old);assert.ok(current.report_refs.length&&old.approval_refs.length);
   await progress();await graphSelection(current);await screenshot(`${theme}-${width}-graph`);
   currentCase.stage='same-run report and browser back';const reportLink=page.locator('.rg-reference-links a[href^="#reports"]');await reportLink.click();await page.locator('.journey-result').waitFor();assert.equal(new URLSearchParams(new URL(page.url()).hash.split('?')[1]).get('run'),current.run_id);assert.equal(await page.locator('.journey-result').getAttribute('data-result-run'),current.run_id);
   const reportIds=new Set(current.report_refs.map(r=>r.id));assert.deepEqual((await page.locator('.report-list .document-title').allTextContents()).sort(),before.reports.filter(r=>reportIds.has(r.id)).map(r=>r.title).sort());assert.deepEqual(await page.locator('.report-list [data-document-id^="report:"]').evaluateAll(items=>items.map(el=>el.dataset.documentId.slice(7)).sort()),[...reportIds].sort(),'current run exact report IDs, not just matching titles');await screenshot(`${theme}-${width}-reports`);await backToGraph(current);
   currentCase.stage='same previous run report then approval';await clear();await graphSelection(old);assert.ok(old.report_refs.length,'approval-bearing run also has reports to compare');await page.locator('.rg-reference-links a[href^="#reports"]').click();await page.locator('.journey-result').waitFor();assert.equal(new URLSearchParams(new URL(page.url()).hash.split('?')[1]).get('run'),old.run_id);
   const oldReportIds=old.report_refs.map(r=>r.id).sort();assert.deepEqual(await page.locator('.report-list [data-document-id^="report:"]').evaluateAll(items=>items.map(el=>el.dataset.documentId.slice(7)).sort()),oldReportIds,'approval-bearing run exact report IDs');assert.deepEqual((await page.locator('.report-list .document-title').allTextContents()).sort(),before.reports.filter(r=>oldReportIds.includes(r.id)).map(r=>r.title).sort());await screenshot(`${theme}-${width}-previous-run-reports`);await backToGraph(old);
   currentCase.stage='same-run approval and cancelled review';await page.locator('.rg-reference-links a[href^="#approvals"]').click();await page.locator('.approval-reading').waitFor();assert.equal(new URLSearchParams(new URL(page.url()).hash.split('?')[1]).get('run'),old.run_id);
   const approval=before.approvals.find(a=>a.id===old.approval_refs[0].id);assert.ok(approval);const review=page.locator(`.approval-reading [data-decision="approve"][data-id="${approval.id}"]`);await review.waitFor();await screenshot(`${theme}-${width}-approval`);await review.click();await page.locator('#decision-form').waitFor();assert.equal(await page.locator('#decision-form').getAttribute('data-id'),approval.id);assert.equal(await page.locator('#decision-form').getAttribute('data-digest'),approval.subject_digest);await page.locator('#dialog').getByRole('button',{name:'취소',exact:true}).click();await backToGraph(old);
   const after=await overview();for(const key of ['runs','plans','approvals'])assert.deepEqual(after[key],before[key],key+' unchanged by reading/review cancellation');
   currentCase.stage='closed browser context relaunch at icon URL';const storageState=await context.storageState();await context.close();await open(width,storageState);await page.locator('.project-list-card').first().waitFor();assert.equal(await page.locator('#login-form').count(),0,'saved session reopens without relogin');assert.equal(await page.locator(`[data-theme-choice="${theme}"]`).getAttribute('aria-pressed'),'true');await pickProject();await progress();await graphSelection(current);await screenshot(`${theme}-${width}-relaunch`);
   const resumed=await overview();for(const key of ['runs','plans','approvals'])assert.deepEqual(resumed[key],before[key],key+' preserved across context relaunch');
   checks.push({width,theme,entry:'/',project_id:fixtures.project_id,report_run:current.run_id,report_ids:[...reportIds],approval_run_report_ids:oldReportIds,approval_run:old.run_id,approval_id:approval.id,approval_status:approval.status,graph_edges:current.edges.length,back:'real page.goBack three times; previous run report and approval share the same run',relaunch:'closed browser context, restored browser cookies/localStorage, / with no query; not Android device',writes:'login/password only'});await context.close();context=null;
  }
  assert.deepEqual(errors,[]);assert.deepEqual(violations,[]);assert.equal(writes.filter(p=>p==='/api/password').length,1);assert.equal(writes.filter(p=>p==='/api/login').length,6);
  await fs.writeFile(path.join(out,'mobile-entry-validation.json'),JSON.stringify({status:'PASS',source:'ephemeral actual API fixture; no production/models/APK device',browser:browser.version(),sandbox:true,checks,writes},null,2));console.log('PASS: default / login, project, integrated graph, same-run reports/approval, browser back/relaunch; Light/Black 320/390/1440; no execution or decision writes');
 }catch(error){await fs.writeFile(path.join(out,'mobile-entry-failure.json'),JSON.stringify({case:currentCase,error:error.message,checks,violations},null,2));if(page&&!page.isClosed())await page.screenshot({path:path.join(out,'mobile-entry-failure.png'),fullPage:true}).catch(()=>{});throw error;}
 finally{if(context)await context.close();await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
