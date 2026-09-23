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
let version='old',writeCount=0,privateReads=0;
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
 const target=path.resolve(root,name);
 if(!target.startsWith(root+path.sep)){res.writeHead(404);res.end();return;}
 try{const data=version==='old'?oldFile(name):await fs.readFile(target);res.setHeader('Content-Type',mime[path.extname(name)]||'application/octet-stream');res.end(data);}
 catch{res.writeHead(404);res.end();}
});
(async()=>{
 await fs.mkdir(out,{recursive:true});await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const base=`http://127.0.0.1:${server.address().port}`;
 let browser,context;const checks=[],errors=[];
 try{
  browser=await chromium.launch({headless:true,chromiumSandbox:true,...(process.env.CHROME_CHANNEL?{channel:process.env.CHROME_CHANNEL}:{}),...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
  context=await browser.newContext({viewport:{width:390,height:844}});
  async function open(){const p=await context.newPage();p.setDefaultTimeout(12000);p.on('pageerror',e=>errors.push(e.message));await p.goto(base);await p.locator('#login-form').waitFor();await p.evaluate(()=>navigator.serviceWorker.ready);await p.waitForFunction(()=>navigator.serviceWorker.controller);return p;}
  async function cacheIs(p,name){await p.waitForFunction(async expected=>{const keys=(await caches.keys()).filter(k=>k.startsWith('ai-company-shell-'));return keys.length===1&&keys[0]===expected;},name);}
  async function waitUpdate(p){await p.evaluate(async()=>{const r=await navigator.serviceWorker.getRegistration();await r.update();});await p.waitForFunction(async()=>{const r=await navigator.serviceWorker.getRegistration();return r?.waiting?.state==='installed';});}
  let page=await open();await cacheIs(page,'ai-company-shell-v8');
  const oldTab=await open();checks.push('baseline v8 controls two old tabs');
  version='new';await waitUpdate(page);
  assert.equal(await page.evaluate(async()=>{const r=await navigator.serviceWorker.getRegistration();return Boolean(r.active&&r.waiting);}),true);
  await oldTab.locator('[data-theme-choice="black"]').click();assert.equal(await oldTab.locator('html').getAttribute('data-theme'),'black');
  checks.push('new worker waits while old tabs remain usable');
  await oldTab.close();await page.close();page=await open();await cacheIs(page,'ai-company-shell-v9');
  const currentApp=await fs.readFile(path.join(root,'app.js'));
  const loaded=await page.evaluate(async()=>await(await fetch('/app.js')).text());assert.equal(hash(loaded),hash(currentApp));
  assert.equal(await page.evaluate(async()=>Boolean(await(await caches.open('ai-company-shell-v9')).match('/journey-ui.js'))),true);
  checks.push('closed old tabs; v9 activates with matching current app and journey module');
  await page.evaluate(async()=>{await fetch('/api/private');await fetch('/api/logout',{method:'POST'});});
  const requestsBefore={writeCount,privateReads};
  assert.equal(await page.evaluate(async()=>{const keys=await caches.keys();for(const key of keys)for(const req of await(await caches.open(key)).keys())if(new URL(req.url).pathname.startsWith('/api/'))return true;return false;}),false);
  await context.setOffline(true);
  assert.equal(await page.evaluate(async()=>{try{await fetch('/api/private');return false;}catch{return true;}}),true);
  assert.equal(await page.evaluate(async()=>{try{await fetch('/api/logout',{method:'POST'});return false;}catch{return true;}}),true);
  await page.reload();await page.locator('#login-form').waitFor();assert.equal(await page.locator('.nav').count(),0);
  await context.setOffline(false);await page.reload();await page.locator('#login-form').waitFor();
  assert.deepEqual({writeCount,privateReads},requestsBefore);checks.push('API and logout are not cached or replayed; offline reload has no private navigation');
  version='old';await waitUpdate(page);await page.close();page=await open();await cacheIs(page,'ai-company-shell-v8');
  const restored=await page.evaluate(async()=>await(await fetch('/app.js')).text());assert.equal(hash(restored),hash(oldFile('app.js')));
  assert.equal(await page.evaluate(async()=>Boolean(await caches.match('/journey-ui.js'))),false);
  checks.push('rollback v8 removes v9 resources and restores baseline app bytes');assert.deepEqual(errors,[]);
  await fs.writeFile(path.join(out,'journey-service-worker-validation.json'),JSON.stringify({status:'PASS',baseline,browser:browser.version(),sandbox:true,origin:'isolated localhost only',checks,hashes:{baseline_app:hash(oldFile('app.js')),candidate_app:hash(currentApp)},limitations:['Synthetic unauthenticated API, not real login verification','No operational cache or physical APK was touched']},null,2));
  console.log(JSON.stringify({status:'PASS',checks}));
 }catch(error){await fs.writeFile(path.join(out,'journey-service-worker-failure.json'),JSON.stringify({status:'FAIL',error:error.message,checks,errors},null,2));throw error;}
 finally{await context?.close();await browser?.close();await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
