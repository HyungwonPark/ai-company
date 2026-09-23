/* Isolated-origin shell update/rollback. No operational URL, DB or credentials. */
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const http=require('node:http');
const crypto=require('node:crypto');
const {execFileSync}=require('node:child_process');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'../../src/ai_company/web');
const out=process.env.UI_OUTPUT||'/tmp/ai-company-journey-sw';
const baseline='75227480d27d3eb1cd57fa43a07a0ab986be3eb5';
const hash=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
const oldFiles=new Map();
let version='old',writeCount=0,privateReads=0,failReport=false;
function oldFile(name){
 if(!oldFiles.has(name))oldFiles.set(name,execFileSync('git',['show',`${baseline}:src/ai_company/web/${name}`],{cwd:root,stdio:['ignore','pipe','ignore'],maxBuffer:8*1024*1024}));
 return oldFiles.get(name);
}
const mime={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.json':'application/json','.webmanifest':'application/manifest+json','.svg':'image/svg+xml','.woff2':'font/woff2'};
const server=http.createServer(async(req,res)=>{
 const url=new URL(req.url,'http://127.0.0.1');
 res.setHeader('Cache-Control','no-store');
 if(url.pathname.startsWith('/api/')){
  res.setHeader('Content-Type','application/json');
  if(req.method==='POST')writeCount++;
  if(url.pathname==='/api/private')privateReads++;
  res.end(JSON.stringify(url.pathname==='/api/session'?{authenticated:false,login_method:'password'}:{fixture:true,private:'isolated fixture',version}));return;
 }
 const name=url.pathname==='/'?'index.html':decodeURIComponent(url.pathname.slice(1));
 if(failReport&&name==='report-ui.js'){res.destroy();return;}
 const target=path.resolve(root,name);
 if(!target.startsWith(root+path.sep)){res.writeHead(404);res.end();return;}
 try{const data=version==='old'?oldFile(name):await fs.readFile(target);res.setHeader('Content-Type',mime[path.extname(name)]||'application/octet-stream');res.end(data);}
 catch{res.writeHead(404);res.end();}
});
(async()=>{
 await fs.mkdir(out,{recursive:true});await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const base=`http://127.0.0.1:${server.address().port}`;
 let browser,context,page;const checks=[],errors=[],boundaries=[],failedRequests=[];
 try{
  browser=await chromium.launch({headless:true,chromiumSandbox:true,...(process.env.CHROME_CHANNEL?{channel:process.env.CHROME_CHANNEL}:{}),...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
  context=await browser.newContext({viewport:{width:390,height:844}});
  async function open(){const p=await context.newPage();p.setDefaultTimeout(12000);p.on('pageerror',e=>errors.push(e.message));p.on('requestfailed',r=>failedRequests.push({path:new URL(r.url()).pathname,error:r.failure()?.errorText}));const cdp=await context.newCDPSession(p);await cdp.send('Network.enable');await cdp.send('Network.setCacheDisabled',{cacheDisabled:true});await p.goto(base);await p.locator('#login-form').waitFor();await p.evaluate(()=>navigator.serviceWorker.ready);await p.waitForFunction(()=>navigator.serviceWorker.controller);return p;}
  // waitForFunction treats the returned Promise as truthy before it resolves.
  // Poll the awaited value on the test side, with the same bounded timeout.
  async function waitAsync(p,predicate,arg){const deadline=Date.now()+12000;do{if(await p.evaluate(predicate,arg))return;await p.waitForTimeout(100);}while(Date.now()<deadline);throw new Error('Timed out awaiting actual asynchronous browser state');}
  async function cacheIs(p,name){await waitAsync(p,async expected=>{const keys=(await caches.keys()).filter(k=>k.startsWith('ai-company-shell-'));return keys.length===1&&keys[0]===expected;},name);}
  async function waitUpdate(p){await p.evaluate(async()=>{const r=await navigator.serviceWorker.getRegistration();await r.update();});await waitAsync(p,async()=>{const r=await navigator.serviceWorker.getRegistration();return r?.waiting?.state==='installed';});}
  const currentApp=await fs.readFile(path.join(root,'app.js'));
  const currentCache=(await fs.readFile(path.join(root,'sw.js'),'utf8')).match(/const CACHE='([^']+)'/)[1];
  async function controllers(){const found=[];for(const worker of context.serviceWorkers()){try{found.push(await worker.evaluate(async()=>({cache:CACHE,clients:(await self.clients.matchAll({type:'window'})).length})));}catch{}}return found;}
  async function waitingOffline(active,waiting,expectedApp,name){
   // Keep another tab alive: neither forward nor rollback worker may activate.
   const responsePromise=page.waitForResponse(r=>new URL(r.url()).pathname==='/app.js');
   const reportPromise=page.waitForResponse(r=>new URL(r.url()).pathname==='/report-ui.js');
   await page.reload();const received=await responsePromise;assert.equal(hash(await received.body()),hash(expectedApp));await page.locator('#login-form').waitFor();
   const reportBytes=await(await reportPromise).body(),expectedReport=failReport||version==='old'?oldFile('report-ui.js'):await fs.readFile(path.join(root,'report-ui.js'));
   assert.equal(hash(reportBytes),hash(expectedReport),'partial/full module bytes match the intended cached/server version');
   const before=await controllers();assert.ok(before.some(w=>w.cache===active&&w.clients>=2),JSON.stringify(before));assert.ok(before.some(w=>w.cache===waiting&&w.clients===0),JSON.stringify(before));
   assert.ok(await page.evaluate(async()=>Boolean(navigator.serviceWorker.controller&&(await navigator.serviceWorker.getRegistration()).waiting)));
   await context.setOffline(true);await page.reload();await page.locator('#login-form').waitFor();assert.equal(await page.locator('.nav').count(),0);
   const after=await controllers();assert.ok(after.some(w=>w.cache===active&&w.clients>=2));
   assert.equal(await page.evaluate(async({cache})=>(await(await caches.open(cache)).match('/app.js')).text(),{cache:active}),expectedApp.toString());
   await page.screenshot({path:path.join(out,'journey-sw-'+name+'-offline.png'),fullPage:true});
   boundaries.push({name,active,waiting,controllers:after,app_sha256:hash(expectedApp),report_sha256:hash(reportBytes),public_login:true,private_navigation:0,http_cache_disabled:true});
   await context.setOffline(false);await page.reload();await page.locator('#login-form').waitFor();
  }
  page=await open();await cacheIs(page,'ai-company-shell-v8');
  const oldTab=await open();checks.push('baseline v8 controls two old tabs');
  await oldTab.getByLabel('비밀번호',{exact:true}).fill('isolated-unsent-input');
  version='new';await waitUpdate(page);
  assert.equal(await page.evaluate(async()=>{const r=await navigator.serviceWorker.getRegistration();return Boolean(r.active&&r.waiting);}),true);
  await oldTab.locator('[data-theme-choice="black"]').click();assert.equal(await oldTab.locator('html').getAttribute('data-theme'),'black');
  checks.push('new worker waits while old tabs remain usable');
  // First reload deliberately leaves the baseline report module in the old
  // cache. A new named export dependency would break this partial update too.
  failReport=true;await waitingOffline('ai-company-shell-v8',currentCache,currentApp,'upgrade-partial');failReport=false;
  await waitingOffline('ai-company-shell-v8',currentCache,currentApp,'upgrade-complete');
  assert.equal(await oldTab.getByLabel('비밀번호',{exact:true}).inputValue(),'isolated-unsent-input');assert.equal(writeCount,0,'old tab input is preserved and never submitted');
  checks.push('v8 controls two tabs during partial and complete online reload, then offline reload boots candidate public shell without new-module/import failure');
  await oldTab.close();await page.close();page=await open();await cacheIs(page,currentCache);
  const loaded=await page.evaluate(async()=>await(await fetch('/app.js')).text());assert.equal(hash(loaded),hash(currentApp));
  checks.push('closed old tabs; candidate activates with matching current app bytes');
  await page.evaluate(async()=>{await fetch('/api/private');await fetch('/api/logout',{method:'POST'});});
  const requestsBefore={writeCount,privateReads};
  assert.equal(await page.evaluate(async()=>{const keys=await caches.keys();for(const key of keys)for(const req of await(await caches.open(key)).keys())if(new URL(req.url).pathname.startsWith('/api/'))return true;return false;}),false);
  await context.setOffline(true);
  assert.equal(await page.evaluate(async()=>{try{await fetch('/api/private');return false;}catch{return true;}}),true);
  assert.equal(await page.evaluate(async()=>{try{await fetch('/api/logout',{method:'POST'});return false;}catch{return true;}}),true);
  await page.reload();await page.locator('#login-form').waitFor();assert.equal(await page.locator('.nav').count(),0);
  await context.setOffline(false);await page.reload();await page.locator('#login-form').waitFor();
  assert.deepEqual({writeCount,privateReads},requestsBefore);checks.push('API and logout are not cached or replayed; offline reload has no private navigation');
  const candidateTab=await open();version='old';await waitUpdate(page);
  await waitingOffline(currentCache,'ai-company-shell-v8',oldFile('app.js'),'rollback');
  checks.push('candidate controller with v8 waiting also boots baseline public shell through online and offline reload');
  await candidateTab.close();await page.close();page=await open();await cacheIs(page,'ai-company-shell-v8');
  const restored=await page.evaluate(async()=>await(await fetch('/app.js')).text());assert.equal(hash(restored),hash(oldFile('app.js')));
  assert.equal(await page.evaluate(async()=>Boolean(await caches.match('/journey-ui.js'))),false);
  checks.push('rollback v8 removes candidate resources and restores baseline app bytes');assert.deepEqual(errors,[]);
  await fs.writeFile(path.join(out,'journey-service-worker-validation.json'),JSON.stringify({status:'PASS',baseline,browser:browser.version(),sandbox:true,origin:'isolated localhost only',checks,boundaries,failedRequests,errors,hashes:{baseline_app:hash(oldFile('app.js')),candidate_app:hash(currentApp)},limitations:['Synthetic unauthenticated API, not real login verification','No operational cache or physical APK was touched']},null,2));
  console.log(JSON.stringify({status:'PASS',checks}));
 }catch(error){await page?.screenshot({path:path.join(out,'journey-service-worker-failure.png'),fullPage:true}).catch(()=>{});await fs.writeFile(path.join(out,'journey-service-worker-failure.json'),JSON.stringify({status:'FAIL',error:error.message,checks,boundaries,failedRequests,errors},null,2));throw error;}
 finally{await context?.close();await browser?.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
