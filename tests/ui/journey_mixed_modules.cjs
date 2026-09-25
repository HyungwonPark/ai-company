/* Independently authored R25-1b: real authentication/API, selective public asset failure. */
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const crypto=require('node:crypto');
const {execFileSync}=require('node:child_process');
const {chromium}=require('playwright');
const baseline='75227480d27d3eb1cd57fa43a07a0ab986be3eb5';
const root=path.resolve(__dirname,'../../src/ai_company/web');
const out=process.env.UI_OUTPUT||'/tmp/ai-company-journey-mixed';
const hash=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
const old=name=>execFileSync('git',['show',baseline+':src/ai_company/web/'+name],{cwd:root,maxBuffer:8*1024*1024});
(async()=>{
 const base=process.env.BASE_URL,fixture=JSON.parse(process.env.GRAPH_FIXTURES);
 assert.equal(new URL(base).hostname,'127.0.0.1');await fs.mkdir(out,{recursive:true});
 const files=['app.js','workspace-graph-ui.js','report-ui.js'],expectedOld={},expectedNew={};
 for(const name of files){expectedOld[name]=hash(old(name));expectedNew[name]=hash(await fs.readFile(path.join(root,name)));}
 const newCache=(await fs.readFile(path.join(root,'sw.js'),'utf8')).match(/const CACHE='([^']+)'/)[1];
 const browser=await chromium.launch({headless:true,chromiumSandbox:true,...(process.env.CHROME_CHANNEL?{channel:process.env.CHROME_CHANNEL}:{}),...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
 const cases=[{name:'old-graph',fail:['workspace-graph-ui.js']},{name:'old-graph-report',fail:['workspace-graph-ui.js','report-ui.js']},{name:'old-report',fail:['report-ui.js']},{name:'old-app-new-modules',fail:[],oldapp:true}];
 const results=[],screens=[];let password=process.env.TEST_PASSWORD,context,page,active=null;
 const configure=async({mode='old',fail=[],oldapp=false}={})=>{const params=new URLSearchParams({mode,oldapp:oldapp?'1':'0'});for(const item of fail)params.append('fail',item);const response=await context.request.get(base+'/__mixed__/configure?'+params);assert.equal(response.status(),200);};
 async function waitAsync(p,fn,arg){const until=Date.now()+12000;while(Date.now()<until){if(await p.evaluate(fn,arg))return;await p.waitForTimeout(100);}throw Error('Actual asynchronous worker state did not reach expected boundary');}
 try{
  for(const item of cases){
   active={case:item.name,stage:'baseline login',errors:[],observations:[]};context=await browser.newContext({viewport:{width:390,height:844},reducedMotion:'reduce'});await configure();
   const opened=async()=>{const p=await context.newPage();p.setDefaultTimeout(12000);p.on('pageerror',error=>active.errors.push(error.message));const cdp=await context.newCDPSession(p);await cdp.send('Network.enable');await cdp.send('Network.setCacheDisabled',{cacheDisabled:true});return p;};
   page=await opened();await page.goto(base);await page.getByLabel('비밀번호',{exact:true}).fill(password);await page.getByRole('button',{name:'로그인',exact:true}).click();
   if(password===process.env.TEST_PASSWORD){await page.getByLabel('현재 비밀번호',{exact:true}).fill(password);password='mixed-module-fixture-password';await page.getByLabel('새 비밀번호',{exact:true}).fill(password);await page.getByLabel('새 비밀번호 확인',{exact:true}).fill(password);await page.getByRole('button',{name:'비밀번호 변경 후 계속'}).click();}
   await page.locator('.project-list-card').first().waitFor();await page.evaluate(()=>navigator.serviceWorker.ready);await page.waitForFunction(()=>navigator.serviceWorker.controller);
   await page.goto(base+'/#manager?project='+fixture.draft_project_id);await page.locator('#message-content').fill('혼합 모듈 검사 · 보내지 않은 내 목표');
   const draftKey='ai-company:draft:edward:'+fixture.draft_project_id,draftValue=await page.evaluate(key=>sessionStorage.getItem(key),draftKey);assert.equal(draftValue,'혼합 모듈 검사 · 보내지 않은 내 목표');
   const other=await opened();await other.goto(base+'/#manager?project='+fixture.draft_project_id);await other.locator('#message-content').fill('다른 기존 탭 · 미전송 초안');
   await configure({mode:'new'});await page.evaluate(async()=>{await(await navigator.serviceWorker.getRegistration()).update();});
   await waitAsync(page,async()=>{const registration=await navigator.serviceWorker.getRegistration();return registration?.waiting?.state==='installed';});
   assert.equal(await other.locator('#message-content').inputValue(),'다른 기존 탭 · 미전송 초안');
   await configure({mode:'new',fail:item.fail,oldapp:item.oldapp});active.stage='authenticated selective module failure';
   const requestedRun=fixture.run_ids[0],requestedHash='#progress?project='+fixture.project_id+'&run='+requestedRun;
   const requests=files.map(async name=>{const response=await page.waitForResponse(r=>new URL(r.url()).pathname==='/'+name);return [name,hash(await response.body())];});
   await page.goto(base+(item.oldapp?'/#progress?project='+fixture.project_id:'/'+requestedHash));
   const loaded=Object.fromEntries(await Promise.all(requests));
   for(const name of files){const shouldOld=item.oldapp&&name==='app.js'||item.fail.includes(name);assert.equal(loaded[name],(shouldOld?expectedOld:expectedNew)[name],item.name+' '+name+' bytes');}
   const states=[];for(const worker of context.serviceWorkers())try{states.push(await worker.evaluate(async()=>({cache:CACHE,clients:(await self.clients.matchAll({type:'window'})).length})));}catch{}
   assert.ok(states.some(s=>s.cache==='ai-company-shell-v8'&&s.clients===2),JSON.stringify(states));assert.ok(states.some(s=>s.cache===newCache&&s.clients===0),JSON.stringify(states));
   const session=await page.evaluate(async()=>await(await fetch('/api/session')).json());assert.equal(session.authenticated,true);
   const overview=await page.evaluate(async id=>await(await fetch('/api/projects/'+id+'/overview')).json(),fixture.project_id);assert.equal(overview.project.id,fixture.project_id);
   const current=overview.workspace_graph.snapshots.find(s=>s.run_id===requestedRun);assert.ok(current);
   const writesBefore=await(await context.request.get(base+'/__mixed__/evidence')).json();
   assert.equal(writesBefore.writes.filter(p=>!['/api/login','/api/password'].includes(p)).length,0);
   active.observations.push({loaded,states,authenticated:true,project_api:true,http_cache_disabled:true});
   assert.deepEqual(active.errors,[],'mixed modules must not throw unhandled exceptions during authenticated project render');
   if(!item.oldapp){
    const notice=page.locator('[data-module-upgrade]');await notice.waitFor();assert.equal(await notice.getByRole('heading',{name:'화면 갱신 필요',exact:true}).count(),1);
    assert.equal(new URL(page.url()).hash,requestedHash,'incompatible modules cannot replace the explicit run URL');
    assert.equal(await page.locator('.rg-node,.approval-reading,.journey-result').count(),0,'no unrelated execution or actionable candidate borrowed');
    assert.doesNotMatch(await notice.innerText(),/getScope|selectScope|TypeError|undefined/,'internal method names are not user guidance');
    assert.equal(await page.evaluate(key=>sessionStorage.getItem(key),draftKey),draftValue);
    // Exercise original document-level handlers with stale controls still held by a
    // caller. Read-only gate must reject new work, not merely hide its buttons.
    await page.evaluate(()=>{const button=document.createElement('button');button.dataset.action='create-project';document.body.append(button);button.click();button.remove();const form=document.createElement('form');form.id='message-form';form.innerHTML='<textarea name="content">전송 금지</textarea><button type="submit">전송</button>';document.body.append(form);form.dispatchEvent(new SubmitEvent('submit',{bubbles:true,cancelable:true}));form.remove();});
    assert.equal(await page.locator('#dialog[open]').count(),0);assert.equal(await page.evaluate(key=>sessionStorage.getItem(key),draftKey),draftValue);
    for(const [width,theme] of [[320,'light'],[390,'black']]){await page.setViewportSize({width,height:844});await page.locator('[data-theme-choice="'+theme+'"]').click();await notice.waitFor();assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));const name='journey-mixed-'+item.name+'-'+theme+'-'+width+'.png';await page.screenshot({path:path.join(out,name),fullPage:true});screens.push(name);}
    assert.equal(await other.locator('#message-content').inputValue(),'다른 기존 탭 · 미전송 초안');
    await configure({mode:'new'});active.stage='explicit user recovery';
    await Promise.all([page.waitForNavigation(),page.getByRole('button',{name:'화면 다시 불러오기',exact:true}).click()]);
    const graph=page.locator('[data-rg-root]');await graph.waitFor();assert.equal(await graph.getAttribute('data-snapshot'),current.id);assert.equal(new URL(page.url()).hash,requestedHash);
    for(const [name,view] of [['결과','reports'],['승인','approvals'],['진행','progress']]){await page.locator('.nav').getByRole('link',{name,exact:true}).click();await page.waitForFunction(view=>location.hash.startsWith('#'+view+'?'),view);assert.equal(new URLSearchParams(new URL(page.url()).hash.split('?')[1]).get('run'),requestedRun);}
    await page.goto(base+'/#manager?project='+fixture.draft_project_id);await page.locator('#message-content').waitFor();assert.equal(await page.locator('#message-content').inputValue(),draftValue,'explicit reload restores the saved draft');
   }else{
    await page.locator('[data-rg-root]').waitFor();assert.equal(await page.locator('#project-select').inputValue(),fixture.project_id);
    for(const view of ['reports','approvals','progress']){await page.evaluate(hash=>{location.hash=hash;},'#'+view+'?project='+fixture.project_id);await page.waitForTimeout(150);assert.equal(await page.locator('#project-select').inputValue(),fixture.project_id);}
    active.observations.push({legacy:'old app supports project-scoped navigation only; exact new journey scope is not claimed retroactively'});
   }
   assert.deepEqual(active.errors,[]);const finalEvidence=await(await context.request.get(base+'/__mixed__/evidence')).json();assert.equal(finalEvidence.writes.filter(p=>!['/api/login','/api/password'].includes(p)).length,0,'mixed/recovered reads create no business writes');
   active.observations.push({business_writes:0,other_tab_draft:await other.locator('#message-content').inputValue(),draft_storage:await page.evaluate(key=>sessionStorage.getItem(key),draftKey),asset_failures:finalEvidence.asset_failures});
   results.push({...active,status:'PASS'});await context.close();context=null;
  }
  const evidence={status:'PASS',baseline,browser:browser.version(),sandbox:true,source:'temporary actual ManagementHTTPServer/password login/SQLite; only public asset failure injected',cases:results,artifacts:screens,limitations:['No physical APK, production, models or approval mutation','Legacy app retains its preexisting project-scoped navigation contract','Authentication fixture writes are excluded from business-table comparison']};
  await fs.writeFile(path.join(out,'journey-mixed-validation.json'),JSON.stringify(evidence,null,2));console.log(JSON.stringify({status:'PASS',cases:results.length}));
 }catch(error){await page?.screenshot({path:path.join(out,'journey-mixed-failure.png'),fullPage:true}).catch(()=>{});await fs.writeFile(path.join(out,'journey-mixed-failure.json'),JSON.stringify({status:'FAIL',error:error.message,active,completed:results},null,2));throw error;}
 finally{await context?.close();await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
