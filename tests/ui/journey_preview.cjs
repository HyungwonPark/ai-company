/* The packaged product modules with fixed public GET fixtures, not live API/model/APK proof. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const {execFileSync}=require('node:child_process');
const {createHash}=require('node:crypto');
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
(async()=>{
 const out=path.resolve(process.env.UI_OUTPUT||'/tmp/ai-company-journey-preview');await fs.mkdir(out,{recursive:true});
 const html=path.resolve(process.env.PREVIEW_HTML||path.join(out,'AI-Company-journey.html'));
 if(!process.env.PREVIEW_HTML)execFileSync('python3',['scripts/package_journey_preview.py',html],{stdio:'inherit'});
 const manifestPath=html.replace(/\.[^.]+$/,'.manifest.json'),manifest=JSON.parse(await fs.readFile(manifestPath,'utf8'));
 assert.equal(sha(await fs.readFile(html)),manifest.html_sha256,'the tested HTML is exactly the recorded artifact');
 const fixtureBytes=await fs.readFile('docs/previews/journey/fixture.json'),fixture=JSON.parse(fixtureBytes);
 assert.equal(sha(fixtureBytes),manifest.fixture_sha256);
 for(const [file,expected] of Object.entries(manifest.sources_sha256))assert.equal(sha(await fs.readFile(file)),expected,'packaged source matches this candidate: '+file);
 assert.equal(manifest.original_graph_fixture_sha256,'ac970aba4ccf4abe47bd862da581cdb2b412a0cd7ca6fb98b5d9e13b798b25ba');
 const pid=fixture.fixtures.project_id,overview=fixture.overviews[pid];
 const recent=overview.workspace_graph.snapshots.find(s=>s.run_id===fixture.fixtures.run_ids[1]);
 const old=overview.workspace_graph.snapshots.find(s=>s.run_id===fixture.fixtures.run_ids[0]);
 assert.ok(recent&&old&&old.approval_refs.length);
 const approval=overview.approvals.find(a=>a.id===old.approval_refs[0].id);assert.ok(approval);
 const browser=await chromium.launch({headless:true,chromiumSandbox:true,...(process.env.CHROME_CHANNEL?{channel:process.env.CHROME_CHANNEL}:{}),...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
 const checks=[],errors=[],network=[],consoleErrors=[];let page=null,currentCase=null;
 const snapshot=async name=>{await page.evaluate(()=>document.fonts.ready);await page.screenshot({path:path.join(out,`journey-preview-${name}.png`),fullPage:true});};
 const scope=async (run,snapshotId)=>{
  await page.waitForFunction(({pid,run,snapshotId})=>{const p=new URLSearchParams(location.hash.split('?')[1]);return p.get('project')===pid&&p.get('run')===run&&p.get('snapshot')===snapshotId;},{pid,run,snapshotId});
  assert.ok((await page.locator('.journey-context').textContent()).includes(run));
 };
 try{
  for(const width of [320,390,1440])for(const theme of ['light','black']){
   currentCase={width,theme,stage:'open packaged HTML'};
   const context=await browser.newContext({viewport:{width,height:width>700?1100:844},serviceWorkers:'block',reducedMotion:'reduce'});
   page=await context.newPage();page.setDefaultTimeout(10000);
   page.on('pageerror',error=>errors.push({width,theme,message:error.message}));
   page.on('request',request=>{if(/^https?:/.test(request.url()))network.push({width,theme,method:request.method(),url:request.url()});});
   page.on('console',message=>{if(message.type()==='error')consoleErrors.push({width,theme,message:message.text().slice(0,2000)});});
   await page.goto(pathToFileURL(html).href+'#projects');await page.locator('.project-list-card').first().waitFor();
   assert.equal(await page.locator('#preview-banner strong').textContent(),'예시 · 읽기 전용');
   const packaged=await page.evaluate(()=>({fixture:JSON.parse(document.getElementById('preview-fixture').textContent),manifest:JSON.parse(document.getElementById('preview-manifest').textContent)}));
   assert.deepEqual(packaged.fixture,fixture,'the browser decodes the exact original frozen projection');
   assert.deepEqual(packaged.manifest.sources_sha256,manifest.sources_sha256);
   await page.locator(`[data-theme-choice="${theme}"]`).click();
   assert.equal(await page.locator('html').getAttribute('data-theme'),theme);
   assert.equal(await page.locator('.project-list-card').count(),fixture.projects.length);
   await page.locator(`.project-list-card h2 a[href="#manager?project=${pid}"]`).click();
   await page.locator('#message-form').waitFor();assert.ok((await page.locator('#main').textContent()).includes('실제 실행 아님'));
   await page.locator('.nav a[aria-label="진행"]').click();await page.locator('#journey-run').selectOption(recent.id);
   await scope(recent.run_id,recent.id);await page.locator('.rg-node').first().waitFor();
   assert.equal(await page.locator('[data-rg-root]').getAttribute('data-snapshot'),recent.id);
   assert.equal(await page.locator('.rg-node').count(),recent.nodes.length);
   currentCase.stage='node and actual relationship selection';
   const node=page.locator('.rg-node').first();const nodeId=await node.getAttribute('data-rg-node');await node.click();
   assert.equal(await page.locator('.rg-detail').getAttribute('data-rg-detail-id'),nodeId);
   await page.locator('[data-rg-action="clear"]').click();
   await page.locator('[data-rg-action="fit"]').click();
   const edge=recent.edges.find(e=>e.kind==='specification')||recent.edges[0];assert.ok(edge);
   await page.locator(`[data-rg-label="${edge.id}"] text`).click();
   assert.equal(await page.locator('.rg-detail').getAttribute('data-rg-detail-id'),edge.id);
   const names=id=>recent.nodes.find(n=>n.id===id).name;
   assert.equal(await page.locator('.rg-direction').textContent(),names(edge.from)+' → '+names(edge.to));
   assert.ok((await page.locator('.rg-detail').textContent()).includes(recent.run_id));
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'packaged viewport has no page-wide overflow');
   await snapshot(`${theme}-${width}-progress`);await page.locator('[data-rg-action="clear"]').click();
   currentCase.stage='same-run result and approval navigation';
   await page.locator('.nav a[aria-label="결과"]').click();await scope(recent.run_id,recent.id);
   await page.locator('#journey-run').selectOption(old.id);await scope(old.run_id,old.id);
   await page.locator('.nav a[aria-label="승인"]').click();await scope(old.run_id,old.id);
   await page.locator('.approval-reading').waitFor();assert.ok((await page.locator('.approval-reading').textContent()).includes(approval.title));
   // Preserve real expiry evaluation. The frozen fixture may have expired by
   // the time this package is inspected; never turn the clock back to pass.
   const expired=Date.now()/1000>=approval.expires_at;
   // The frozen pending record is not rewritten to the server's expired state.
   // All decisions must be disabled; every preview write still returns 405.
   if(expired)assert.equal(await page.locator(`button[data-decision][data-id="${approval.id}"]:enabled`).count(),0);
   if(width===390)await snapshot(`${theme}-${width}-approval`);
   await page.locator('.nav a[aria-label="진행"]').click();await scope(old.run_id,old.id);await page.locator('[data-rg-root]').waitFor();
   assert.equal(await page.locator('[data-rg-root]').getAttribute('data-snapshot'),old.id);
   currentCase.stage='non-hash hrefs removed, including auxiliary/context activation';
   const excluded=page.locator('.rg-export');await excluded.waitFor();
   await page.waitForFunction(()=>[...document.querySelectorAll('a')].filter(a=>a.hasAttribute('href')).every(a=>a.getAttribute('href').startsWith('#')));
   assert.equal(await excluded.getAttribute('href'),null);assert.equal(await excluded.getAttribute('target'),null);assert.equal(await excluded.getAttribute('aria-disabled'),'true');
   await excluded.scrollIntoViewIfNeeded();const rect=await excluded.boundingBox(),before=page.url(),pages=context.pages().length;assert.ok(rect);
   for(const button of ['left','middle','right'])await page.mouse.click(rect.x+rect.width/2,rect.y+rect.height/2,{button});
   await page.waitForTimeout(150);assert.equal(page.url(),before);assert.equal(context.pages().length,pages,'disabled links cannot open a new tab');
   assert.ok((await page.locator('#preview-notice').textContent()).includes('포함되지 않습니다'));
   const audit=await page.evaluate(()=>window.PREVIEW_AUDIT);
   assert.ok(audit.every(item=>item.method==='GET'),'reading and selection do not attempt a mutation');
   checks.push({width,theme,project:pid,recent_run:recent.run_id,approval_run:old.run_id,approval:approval.id,approval_expired:expired,node:nodeId,edge:edge.id,read_requests:audit.length,network_requests:network.filter(item=>item.width===width&&item.theme===theme).length});
   if(width===390&&theme==='light'){
    currentCase.stage='actual product creation form hits the read-only boundary';
    await page.locator('.nav a[aria-label="프로젝트"]').click();await page.locator('#project-create').click();
    await page.locator('#create-form [name="name"]').fill('오프라인 저장 거부 검사');
    await page.locator('#create-form [name="goal"]').fill('예시 파일에서는 새 프로젝트를 저장하지 않습니다.');
    await page.locator('#create-form button[type="submit"]').click();
    await page.waitForFunction(()=>window.PREVIEW_AUDIT.some(item=>item.method==='POST'&&item.path==='/api/projects'));
    await page.waitForFunction(()=>document.querySelector('#create-form')?.textContent.includes('읽기 전용 미리보기'));
    const denied=await page.evaluate(async()=>{
     const result=[];for(const method of ['PUT','PATCH','DELETE'])result.push({method,status:(await fetch('/api/projects',{method,body:'{}'})).status});
     return {result,projects:await(await fetch('/api/projects')).json(),external:(await fetch('https://hyungwon.cloud/api/session')).status,fixture:document.getElementById('preview-fixture').textContent};
    });
    assert.ok(denied.result.every(item=>item.status===405));assert.equal(denied.external,404);assert.deepEqual(denied.projects.projects,fixture.projects);assert.deepEqual(JSON.parse(denied.fixture),fixture);
    checks.push({boundary:'real product creation rejected; PUT/PATCH/DELETE rejected; external fetch rejected; project/fixture unchanged'});
   }
   await context.close();page=null;
  }
  assert.deepEqual(errors,[]);assert.deepEqual(network,[],'the packaged UI makes no external HTTP requests');
  await fs.writeFile(path.join(out,'journey-preview-validation.json'),JSON.stringify({status:'PASS',scope:'actual packaged product UI + frozen synthetic fetch; no real API/models/approvals/APK',browser:browser.version(),sandbox:true,html_sha256:manifest.html_sha256,fixture_sha256:manifest.fixture_sha256,source_commit:manifest.source_commit,source_dirty:manifest.source_dirty,checks,console_errors:consoleErrors,network_requests:network},null,2));
  console.log('PASS: packaged product in Light/Black 320/390/1440, exact-run reading, graph selection, source hashes, write/network/navigation isolation');
 }catch(error){
  await fs.writeFile(path.join(out,'journey-preview-failure.json'),JSON.stringify({currentCase,error:error.message,checks,errors,consoleErrors,network},null,2));
  if(page&&!page.isClosed())await page.screenshot({path:path.join(out,'journey-preview-failure.png'),fullPage:true});
  throw error;
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
