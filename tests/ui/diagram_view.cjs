/* Real temporary API + pinned compiler. Never operational data or model calls. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path');
(async()=>{
 const base=process.env.BASE_URL,fixtures=JSON.parse(process.env.GRAPH_FIXTURES),out=process.env.UI_OUTPUT||'/tmp/ai-company-diagrams';
 await fs.mkdir(out,{recursive:true});
 const browser=await chromium.launch({headless:true,chromiumSandbox:true,...(process.env.CHROME_CHANNEL?{channel:process.env.CHROME_CHANNEL}:{}),...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
 const context=await browser.newContext({viewport:{width:1440,height:1000},serviceWorkers:'block',reducedMotion:'reduce'}),page=await context.newPage();page.setDefaultTimeout(12000);
 const errors=[],writes=[],checks=[];page.on('pageerror',e=>errors.push(e.message));context.on('request',r=>{if(r.method()!=='GET')writes.push({path:new URL(r.url()).pathname,body:r.postDataJSON()});});
 try{
  await page.goto(base+'/?workspace=integrated#projects');
  await page.getByLabel('비밀번호',{exact:true}).fill(process.env.TEST_PASSWORD);await page.getByRole('button',{name:'로그인',exact:true}).click();
  await page.getByLabel('현재 비밀번호',{exact:true}).fill(process.env.TEST_PASSWORD);await page.getByLabel('새 비밀번호',{exact:true}).fill('diagram-test-only-strong-password');await page.getByLabel('새 비밀번호 확인',{exact:true}).fill('diagram-test-only-strong-password');await page.getByRole('button',{name:'비밀번호 변경 후 계속'}).click();await page.locator('.nav').waitFor();
  const before=await page.evaluate(async id=>await(await fetch('/api/projects/'+id+'/overview')).json(),fixtures.project_id);
  const current=before.workspace_graph.snapshots.find(s=>s.run_id===fixtures.run_ids[1]),endpoint='/api/projects/'+fixtures.project_id+'/diagrams';
  await page.evaluate(id=>location.hash='#progress?project='+id,fixtures.project_id);await page.locator('.rg-export').waitFor();
  const [viewer]=await Promise.all([context.waitForEvent('page'),page.locator('.rg-export').click()]);viewer.setDefaultTimeout(12000);viewer.on('pageerror',e=>errors.push(e.message));
  await viewer.getByRole('button',{name:'그림 만들기',exact:true}).waitFor();
  assert.equal((await(await context.request.get(base+endpoint)).json()).items.length,0);
  assert.ok((await viewer.locator('#history-list').textContent()).includes('저장된 그림이 없습니다'));
  // The response is lost after server completion. The client must reuse its key on reload.
  let lost=true;await viewer.route('**'+endpoint,async route=>{if(route.request().method()==='POST'&&lost){lost=false;await route.fetch();await route.abort('failed');}else await route.continue();});
  await viewer.getByRole('button',{name:'그림 만들기',exact:true}).click();await viewer.getByRole('button',{name:'같은 요청 확인',exact:true}).waitFor();
  await viewer.reload();await viewer.getByRole('button',{name:'같은 요청 확인',exact:true}).click();await viewer.getByRole('button',{name:'그림 만들기',exact:true}).waitFor();
  const list=(await(await context.request.get(base+endpoint)).json()).items;assert.equal(list.length,1);assert.equal(list[0].status,'completed',JSON.stringify(list[0]));await viewer.locator('iframe').waitFor();
  const r=list[0].receipt;assert.equal(r.project_id,fixtures.project_id);assert.equal(r.run_id,current.run_id);assert.equal(r.plan_digest,current.plan_digest);assert.equal(r.source,'fixture');
  const posts=writes.filter(w=>w.path===endpoint);assert.equal(posts.length,2);assert.deepEqual(posts[0].body,posts[1].body);
  checks.push('no generation on GET; lost response + reload reuse same key and one frozen artifact');
  await viewer.locator('iframe').evaluate(el=>new Promise(resolve=>{if(el.contentWindow)resolve();else el.addEventListener('load',resolve,{once:true});}));
  assert.equal(await viewer.locator('iframe').getAttribute('sandbox'),'');assert.equal(await viewer.locator('iframe').evaluate(el=>el.contentDocument),null);
  const frame=viewer.frameLocator('iframe');await frame.getByRole('heading',{name:'그림',exact:true}).waitFor();assert.ok(await frame.locator('svg').count());
  const htmlResponse=await context.request.get(base+endpoint+'/'+r.id+'/index.html');assert.ok(htmlResponse.headers()['content-security-policy'].includes("sandbox; default-src 'none'"));
  assert.ok((await context.request.get(base+'/')).headers()['content-security-policy'].includes("style-src 'self'"));
  checks.push('actual compiler SVG, opaque iframe, no scripts/API access, main CSP unchanged');
  for(const width of [1440,390,320])for(const theme of ['light','black']){
   await viewer.setViewportSize({width,height:width>700?1000:844});await viewer.locator(`header [data-theme=${theme}]`).click();await viewer.evaluate(()=>document.fonts.ready);
   assert.ok(await viewer.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),`overflow ${width} ${theme}`);
   const sizes=await viewer.locator('header button,#actions button,.downloads a').evaluateAll(nodes=>nodes.map(n=>({w:n.getBoundingClientRect().width,h:n.getBoundingClientRect().height})));assert.ok(sizes.every(x=>x.w>=44&&x.h>=44));
   await viewer.screenshot({path:path.join(out,`workspace-diagram-${theme}-${width}.png`),fullPage:true});
  }
  const [download]=await Promise.all([viewer.waitForEvent('download'),viewer.getByRole('link',{name:'HTML',exact:true}).click()]);assert.equal(download.suggestedFilename(),'index.html');
  const downloadFile=path.join(out,'workspace-diagram.html');await download.saveAs(downloadFile);const bytes=await fs.readFile(downloadFile);assert.equal(require('node:crypto').createHash('sha256').update(bytes).digest('hex'),r.files['index.html'].sha256);
  await fs.writeFile(path.join(out,'workspace-diagram.svg'),await(await context.request.get(base+endpoint+'/'+r.id+'/diagram.svg')).body());
  await fs.writeFile(path.join(out,'workspace-diagram-receipt.json'),JSON.stringify(r,null,2));
  checks.push('Light/Black 320/390/1440, Korean controls, 44px, HTML download SHA256');
  // Keyboard navigation and enlarged text retain every action; iframe has readable list anchors.
  await viewer.setViewportSize({width:640,height:900});await viewer.evaluate(()=>{document.documentElement.style.fontSize='200%';});assert.equal(await viewer.evaluate(()=>getComputedStyle(document.body).fontSize),'32px');assert.equal(await viewer.locator('#generate').evaluate(el=>getComputedStyle(el).fontSize),'32px');const inner=viewer.frames().find(f=>f!==viewer.mainFrame());await inner.evaluate(()=>{document.documentElement.style.fontSize='200%';});assert.equal(await inner.evaluate(()=>getComputedStyle(document.body).fontSize),'32px');assert.ok(await viewer.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await viewer.keyboard.press('Tab');assert.ok(await viewer.evaluate(()=>document.activeElement!==document.body));
  await frame.getByRole('link',{name:'역할 목록',exact:true}).click();await frame.getByRole('heading',{name:'역할',exact:true}).waitFor();
  const after=await(await context.request.get(base+'/api/projects/'+fixtures.project_id+'/overview')).json();
  assert.deepEqual(after.runs,before.runs);assert.deepEqual(after.approvals,before.approvals);assert.deepEqual(after.plans,before.plans);
  assert.ok(writes.every(w=>['/api/login','/api/password',endpoint].includes(w.path)));
  await viewer.goto(base+'/diagram-view.html?project=invalid');assert.ok((await viewer.locator('#notice').textContent()).includes('진행 화면'));
  assert.deepEqual(errors,[]);checks.push('keyboard/list anchors, no execution/approval writes, invalid link safe');
  await fs.writeFile(path.join(out,'workspace-diagram-validation.json'),JSON.stringify({fixture:true,browser:await browser.version(),sandbox:true,checks},null,2));console.log('PASS: '+checks.join('; '));
 }finally{await context.close();await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
