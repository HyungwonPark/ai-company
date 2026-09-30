const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
(async()=>{
 const base=process.env.BASE_URL,out=process.env.UI_OUTPUT||'/tmp/review-evidence';
 assert.equal(new URL(base).hostname,'127.0.0.1');await fs.mkdir(out,{recursive:true});
 const browser=await chromium.launch({headless:true,chromiumSandbox:true,...(process.env.CHROME_CHANNEL?{channel:process.env.CHROME_CHANNEL}:{}),...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
 const checks=[],screens=[],external=[],fonts=[];
 try{
  for(const width of [320,390,1440]){
   const context=await browser.newContext({viewport:{width,height:900},reducedMotion:'reduce'}),page=await context.newPage();
   page.on('request',r=>{if(new URL(r.url()).origin!==base)external.push(r.url())});
   const errors=[];page.on('pageerror',e=>errors.push(e.message));
   for(const theme of ['light','black'])for(const endpoint of ['review','review/manual']){
    const response=await page.goto(base+'/'+endpoint);assert.ok(response.headers()['content-security-policy'].includes("default-src 'none'"));await page.reload();await page.locator('#theme-'+theme).check();
    assert.equal(await page.evaluate(()=>getComputedStyle(document.body).backgroundColor),theme==='black'?'rgb(17, 21, 18)':'rgb(245, 245, 242)','CSP blocked stylesheet or theme');
    assert.equal(await page.locator('html').getAttribute('lang'),'ko');
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'page overflow');
    assert.equal(await page.locator('script,iframe,img,form').count(),0);
    const links=await page.locator('.contents a').evaluateAll(xs=>xs.map(x=>decodeURIComponent(x.hash.slice(1))));
    for(const id of links)assert.ok(await page.evaluate(id=>!!document.getElementById(id),id),`missing heading: ${id}`);
    if(width===390&&theme==='light'){const cdp=await context.newCDPSession(page);await cdp.send('DOM.enable');await cdp.send('CSS.enable');const {root}=await cdp.send('DOM.getDocument');const {nodeId}=await cdp.send('DOM.querySelector',{nodeId:root.nodeId,selector:'h1'});const result=await cdp.send('CSS.getPlatformFontsForNode',{nodeId});assert.ok(result.fonts.some(f=>f.familyName.includes('Noto Sans CJK')),'Korean font not rendered');fonts.push({endpoint,fonts:result.fonts});await cdp.detach();}
    await page.screenshot({path:path.join(out,`${endpoint.replace('/','-')}-${theme}-${width}.png`)});
    screens.push(`${endpoint.replace('/','-')}-${theme}-${width}.png`);
    await page.locator('.contents a').first().click();assert.ok(new URL(page.url()).hash);
    await page.locator('table').first().scrollIntoViewIfNeeded();
    const detail=`${endpoint.replace('/','-')}-${theme}-${width}-table.png`;
    await page.screenshot({path:path.join(out,detail)});screens.push(detail);
    checks.push({width,theme,endpoint,direct:true,refresh:true,anchors:links.length,no_overflow:true});
   }
   await page.goto(base+'/review');await page.keyboard.press('Tab');assert.equal(await page.locator(':focus').innerText(),'본문으로');await page.keyboard.press('Enter');assert.equal(await page.locator(':focus').getAttribute('id'),'document');
   await page.getByRole('navigation',{name:'문서'}).getByRole('link',{name:'매뉴얼',exact:true}).click();assert.equal(new URL(page.url()).pathname,'/review/manual');await page.goBack();assert.equal(new URL(page.url()).pathname,'/review');
   await page.locator('.provenance summary').click();assert.ok(await page.getByText('e659fc66cadfb5af77f85327314671b46a0383e0',{exact:true}).isVisible());
   for(const endpoint of ['review','review/manual']){
    await page.goto(base+'/'+endpoint);
    const normal=await page.evaluate(()=>Object.fromEntries(['h1','h2','table','.contents a','.brand'].map(s=>[s,parseFloat(getComputedStyle(document.querySelector(s)).fontSize)])));
    await page.evaluate(()=>document.documentElement.style.fontSize='32px');
    const enlarged=await page.evaluate(()=>Object.fromEntries(['h1','h2','table','.contents a','.brand'].map(s=>[s,parseFloat(getComputedStyle(document.querySelector(s)).fontSize)])));
    for(const selector of Object.keys(normal))assert.equal(enlarged[selector],normal[selector]*2,`200% text: ${selector}`);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'200% text overflow');
    await page.locator('.provenance summary').click();
    const zoom=`${endpoint.replace('/','-')}-${width}-text-200.png`;await page.screenshot({path:path.join(out,zoom)});screens.push(zoom);
    checks.push({width,endpoint,text_enlargement:200,computed_sizes:enlarged,no_overflow:true});
   }
   assert.deepEqual(errors,[]);await context.close();
  }
  const context=await browser.newContext(),page=await context.newPage();await page.goto(base+'/');
  if(process.env.REVIEW_SERVICE_WORKER){
   await page.evaluate(async()=>{await navigator.serviceWorker.register('/sw.js');await navigator.serviceWorker.ready});await page.reload();
   await page.goto(base+'/review');assert.equal(await page.locator('h1').innerText(),'AI Company 작업 현황');
   checks.push({existing_service_worker:'network page retained'});
  }
  const missing=await page.goto(base+'/review/not-published');assert.equal(missing.status(),404);
  const rejected=await page.request.post(base+'/review');assert.equal(rejected.status(),405);
  await context.close();assert.deepEqual(external,[]);
  if(process.env.REVIEW_PREVIEW){const offline=await browser.newContext({offline:true});const p=await offline.newPage();await p.goto(pathToFileURL(path.join(process.env.REVIEW_PREVIEW,'index.html')).href);await p.getByRole('navigation',{name:'문서'}).getByRole('link',{name:'매뉴얼',exact:true}).click();assert.equal(await p.locator('h1').innerText(),'AI Company 운영·개발 매뉴얼');checks.push({download_preview:'offline navigation passed'});await offline.close();}
  await fs.writeFile(path.join(out,'browser-result.json'),JSON.stringify({status:'PASS',browser:browser.version(),sandbox:true,checks,screens,fonts,external_requests:external,model_calls:0,scope:'temporary static documents; no production or master interaction'},null,2));
  console.log(JSON.stringify({status:'PASS',checks:checks.length,screens:screens.length,browser:browser.version(),sandbox:true}));
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
