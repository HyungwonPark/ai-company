/* The downloadable HTML must work via file:// without any API or CDN. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const fs=require('node:fs/promises');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
(async()=>{
 const out=process.env.UI_OUTPUT||'/tmp/ai-company-graph-ui';await fs.mkdir(out,{recursive:true});const file=path.join(out,'AI-Company-graph.html');
 execFileSync('python3',['scripts/package_graph_preview.py',file]);
 const browser=await chromium.launch({headless:true,chromiumSandbox:true,...(process.env.CHROME_CHANNEL?{channel:process.env.CHROME_CHANNEL}:{}),...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
 try{
  const context=await browser.newContext({viewport:{width:390,height:844},reducedMotion:'reduce'}),page=await context.newPage();const errors=[],network=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(/^https?:/.test(r.url()))network.push(r.url());});
  await page.goto(pathToFileURL(file).href);await page.locator('.rg-node').first().waitFor();
  assert.ok(await page.getByText('예시 · 운영 연결 없음',{exact:true}).count());
  await page.locator('.rg-node').first().click();assert.ok(await page.locator('.rg-detail.is-open').isVisible());await page.locator('[data-rg-action=clear]').click();
  await page.locator('[data-rg-action=list]').click();await page.locator('.rg-list [data-rg-edge]').first().click();assert.ok(await page.locator('.rg-detail.is-open').isVisible());await page.locator('[data-rg-action=clear]').click();
  await page.locator('[data-rg-action=graph]').click();
  for(const theme of ['light','black'])for(const width of [390,1440]){
   await page.setViewportSize({width,height:width>700?1000:844});if(await page.locator('html').getAttribute('data-theme')!==theme)await page.locator('#theme').click();
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.evaluate(()=>document.fonts.ready);
   await page.screenshot({path:path.join(out,`workspace-graph-offline-${theme}-${width}.png`),fullPage:true});
  }
  await page.locator('.preview-header a[href="#manager"]').click();await page.getByRole('heading',{name:'역할별 진행을 한눈에 보고 싶어요.'}).waitFor();
  await page.locator('.preview-header a[href="#approvals"]').click();await page.getByRole('heading',{name:'이전 예시 후보 수용',exact:true}).waitFor();assert.ok((await page.locator('#content').textContent()).includes('이전 예시 후보 수용'));
  assert.equal(await page.locator('button[data-decision]').count(),0);assert.deepEqual(network,[]);assert.deepEqual(errors,[]);
  console.log('PASS: downloaded file opens offline; shared graph node/edge details; Light/Black/mobile; no HTTP requests or decision writes');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
