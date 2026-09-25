/* Independent G1/G3 checks through real temporary reads. Synthetic updates are explicit. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const {createHash}=require('node:crypto');
(async()=>{
 const base=process.env.BASE_URL,fixtures=JSON.parse(process.env.GRAPH_FIXTURES),out=process.env.UI_OUTPUT||'/tmp/ai-company-journey-ui';
 assert.equal(new URL(base).hostname,'127.0.0.1');await fs.mkdir(out,{recursive:true});
 const browser=await chromium.launch({headless:true,chromiumSandbox:true,...(process.env.CHROME_CHANNEL?{channel:process.env.CHROME_CHANNEL}:{}),...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
 const errors=[],violations=[],writes=[],checks=[],measurements=[];let context,page,storageState,currentCase;
 const open=async width=>{
  context=await browser.newContext({viewport:{width,height:width>700?1100:844},serviceWorkers:'block',reducedMotion:'reduce',...(storageState?{storageState}:{})});
  await context.route('**/*',route=>{const req=route.request(),url=new URL(req.url());if(url.origin!==base||req.method()!=='GET'&&!['/api/login','/api/password'].includes(url.pathname)){violations.push({method:req.method(),path:url.pathname});return route.abort();}if(req.method()!=='GET')writes.push(url.pathname);return route.continue();});
  page=await context.newPage();page.setDefaultTimeout(10000);page.on('pageerror',error=>errors.push(error.message));await page.goto(base+'/#progress?project='+fixtures.project_id+'&run='+fixtures.run_ids[1]);
  if(!storageState){await page.getByLabel('비밀번호',{exact:true}).fill(process.env.TEST_PASSWORD);await page.getByRole('button',{name:'로그인',exact:true}).click();await page.getByLabel('현재 비밀번호',{exact:true}).fill(process.env.TEST_PASSWORD);await page.getByLabel('새 비밀번호',{exact:true}).fill('journey-graph-fixture-only-password');await page.getByLabel('새 비밀번호 확인',{exact:true}).fill('journey-graph-fixture-only-password');await page.getByRole('button',{name:'비밀번호 변경 후 계속'}).click();}
  await page.locator('[data-rg-root]').waitFor();
 };
 const action=name=>page.locator(`[data-rg-action="${name}"]`).click();
 const help=()=>page.locator('.rg-help');
 const helpOpen=async reason=>assert.equal(await help().evaluate(el=>el.open),true,reason);
 const close=async()=>{if(await page.locator('.rg-detail.is-open').count())await page.locator('#rg-detail-close').click();};
 const screenshot=async name=>{await page.evaluate(()=>document.fonts.ready);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'no page overflow');await page.screenshot({path:path.join(out,'journey-graph-'+name+'.png'),fullPage:true});};
 try{
  for(const width of [320,390,1440])for(const theme of ['light','black']){
   currentCase={width,theme,stage:'initial node-group center'};await open(width);await page.locator(`[data-theme-choice="${theme}"]`).click();
   const overview=await page.evaluate(async id=>(await(await fetch('/api/projects/'+id+'/overview')).json()),fixtures.project_id),snapshot=overview.workspace_graph.snapshots.find(s=>s.run_id===fixtures.run_ids[1]),ids=snapshot.nodes.filter(n=>!['document','artifact'].includes(n.kind)).map(n=>n.id);
   const reading=await page.locator('.rg-viewport').evaluate((viewport,ids)=>{
    const rect=viewport.getBoundingClientRect(),nodes=[...viewport.querySelectorAll('.rg-node')].filter(node=>ids.includes(node.dataset.rgNode)),boxes=nodes.map(node=>node.getBoundingClientRect());
    const left=Math.min(...boxes.map(box=>box.left)),right=Math.max(...boxes.map(box=>box.right));
    return {center_error:Math.abs((left+right)/2-(rect.left+rect.right)/2),viewport:{left:rect.left,right:rect.right,width:rect.width},group:{left,right},font_sizes:nodes.flatMap(node=>{const zoom=node.getBoundingClientRect().width/parseFloat(node.style.width);return [...node.querySelectorAll('strong,.rg-node-kind,.rg-node-state,.rg-node-model')].map(text=>parseFloat(getComputedStyle(text).fontSize)*zoom);}),camera:viewport.querySelector('.rg-scene').style.transform,scroll_left:viewport.scrollLeft,scroll_top:viewport.scrollTop};
   },ids);
   assert.ok(reading.center_error<=8,`role group center differs ${reading.center_error}px`);assert.ok(reading.font_sizes.every(size=>size>=14-.01),'initial role text at least 14 CSS px after transform');assert.equal(reading.scroll_left,0);assert.equal(reading.scroll_top,0);assert.equal(await page.locator('[data-rg-action="fit"]').textContent(),'전체 보기');measurements.push({width,theme,...reading});await screenshot(`${theme}-${width}-initial`);
   currentCase.stage='help state across redraw';await help().locator('summary').click();await helpOpen('explicit open');await action('list');await helpOpen('list switch');
   const node=snapshot.nodes.find(n=>n.kind==='role'),target=()=>page.locator(`[data-rg-node="${node.id}"]`);await target().click();await page.locator('.rg-detail.is-open').waitFor();await helpOpen('node selection');await close();await action('graph');await helpOpen('graph switch');
   await page.locator(`[data-theme-choice="${theme==='light'?'black':'light'}"]`).click();await helpOpen('theme redraw');await page.locator(`[data-theme-choice="${theme}"]`).click();
   await page.getByRole('button',{name:/새로고침/,exact:false}).first().click();await helpOpen('actual API refresh');
   const old=overview.workspace_graph.snapshots.find(s=>s.run_id===fixtures.run_ids[0]);await page.locator('#rg-snapshot').selectOption(old.id);await helpOpen('different execution');await page.locator('#rg-snapshot').selectOption(snapshot.id);await helpOpen('execution return');
   await screenshot(`${theme}-${width}-help`);checks.push({width,theme,checks:'main node centering and readable initial scale; page help survives node/mode/theme/real refresh/run switch'});
   if(!storageState)storageState=await context.storageState();await context.close();context=null;
  }
  currentCase={stage:'detail state through resize and changed API read'};await open(390);
  const endpoint='/api/projects/'+fixtures.project_id+'/overview',original=await page.evaluate(async url=>(await(await fetch(url)).json()),endpoint),snapshot=original.workspace_graph.snapshots.find(s=>s.run_id===fixtures.run_ids[1]),role=snapshot.nodes.find(n=>n.kind==='role');
  await help().locator('summary').click();await action('list');await page.locator(`[data-rg-node="${role.id}"]`).click();await page.locator('.rg-detail.is-open').waitFor();assert.equal(await page.evaluate(()=>document.activeElement.id),'rg-detail-heading');
  const detail=()=>page.locator('.rg-detail');await detail().locator('details').last().locator('summary').click();await detail().evaluate(panel=>{panel.scrollTop=160;});const previousScroll=await detail().evaluate(panel=>panel.scrollTop);assert.ok(previousScroll>0,'mobile detail is actually scrolled');
  // An unchanged poll alone need not rebuild DOM. Alter one read-only record to force a real render path.
  const updated=structuredClone(original);updated.workspace_graph.cursor++;updated.workspace_graph.observed_at++;
  const changed=updated.workspace_graph.snapshots.find(s=>s.id===snapshot.id);changed.nodes.find(n=>n.id===role.id).name='갱신 후에도 같은 역할';updated.workspace_graph.fingerprint=createHash('sha256').update(JSON.stringify(updated.workspace_graph.snapshots)).digest('hex');
  await page.route('**'+endpoint,handler=>handler.fulfill({status:200,contentType:'application/json',json:updated}));await page.getByRole('button',{name:/새로고침/,exact:false}).first().click();await page.locator('#rg-detail-heading').filter({hasText:'갱신 후에도 같은 역할'}).waitFor();
  await helpOpen('changed API read');assert.ok(await detail().locator('details').last().evaluate(el=>el.open),'expanded original evidence preserved');assert.ok(Math.abs(await detail().evaluate(panel=>panel.scrollTop)-previousScroll)<2,'same-target detail scroll preserved');
  await page.locator('#rg-detail-heading').focus();await page.setViewportSize({width:1440,height:1100});await page.waitForTimeout(200);await helpOpen('width change');assert.ok(await detail().locator('details').last().evaluate(el=>el.open),'detail evidence survives width redraw');assert.equal(await page.evaluate(()=>document.activeElement.id),'rg-detail-heading','selected detail focus survives width redraw');
  await page.setViewportSize({width:390,height:844});await page.waitForTimeout(200);await screenshot('mobile-open-detail');
  currentCase.stage='missing selected record remains visible';changed.nodes=changed.nodes.filter(n=>n.id!==role.id);changed.edges=changed.edges.filter(e=>e.from!==role.id&&e.to!==role.id);updated.workspace_graph.cursor++;updated.workspace_graph.observed_at++;updated.workspace_graph.fingerprint=createHash('sha256').update(JSON.stringify(updated.workspace_graph.snapshots)).digest('hex');
  await page.getByRole('button',{name:/새로고침/,exact:false}).first().click();await page.locator('[data-rg-missing-record]').waitFor();assert.ok(await page.locator('.rg-detail.is-open').isVisible(),'missing target must stay visible on mobile');assert.ok(await page.locator('#rg-detail-close').isVisible());await helpOpen('selected record disappears');await screenshot('mobile-missing-record');await page.locator('#rg-detail-close').click();assert.equal(await page.locator('.rg-detail.is-open').count(),0);assert.equal(await page.evaluate(()=>document.activeElement.id),'rg-snapshot','missing list target returns focus to execution selector');
  checks.push({checks:'changed synthetic read preserves same-target expanded evidence/scroll; resize preserves detail focus; missing selected record stays visible and close returns to a valid control'});
  await page.unroute('**'+endpoint);
  assert.deepEqual(errors,[]);assert.deepEqual(violations,[]);assert.deepEqual(writes,['/api/login','/api/password']);
  await fs.writeFile(path.join(out,'journey-graph-validation.json'),JSON.stringify({status:'PASS',source:'temporary SQLite with actual ManagementHTTPServer; changed/disappearing record injections explicitly synthetic; no model or production operations',browser:browser.version(),sandbox:true,measurements,checks,write_paths:writes},null,2));console.log(JSON.stringify({status:'PASS',measurements,checks}));
 }catch(error){await fs.writeFile(path.join(out,'journey-graph-failure.json'),JSON.stringify({case:currentCase,error:error.message,checks,measurements,errors,violations},null,2));if(page&&!page.isClosed())await page.screenshot({path:path.join(out,'journey-graph-failure.png'),fullPage:true}).catch(()=>{});throw error;}
 finally{if(context)await context.close();await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
