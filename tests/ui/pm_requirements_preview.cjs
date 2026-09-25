/* Sandboxed browser check of the packaged synthetic PM question/review states. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const {createHash}=require('node:crypto');
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');

(async()=>{
 const html=path.resolve('docs/previews/pm-requirements/AI-Company-PM-preview.html');
 const manifest=JSON.parse(await fs.readFile(html.replace(/\.html$/,'.manifest.json'),'utf8'));
 const fixture=JSON.parse(await fs.readFile('docs/previews/pm-requirements/fixture.json','utf8'));
 assert.equal(sha(await fs.readFile(html)),manifest.html_sha256);
 assert.equal(sha(await fs.readFile('docs/previews/pm-requirements/fixture.json')),manifest.fixture_sha256);
 for(const [file,expected] of Object.entries(manifest.sources_sha256))assert.equal(sha(await fs.readFile(file)),expected,file);
 const questions=fixture.projects.find(item=>item.name==='PM 질문 · 예시');
 const review=fixture.projects.find(item=>item.name==='계획 검토 · 예시');
 assert.ok(questions&&review);
 const output=path.resolve(process.env.UI_OUTPUT||'/tmp/ai-company-pm-preview');
 await fs.mkdir(output,{recursive:true});
 const browser=await chromium.launch({headless:true,chromiumSandbox:true,
   ...(process.env.CHROME_CHANNEL?{channel:process.env.CHROME_CHANNEL}:{}),
   ...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
 const checks=[],errors=[],network=[];
 try{
  for(const width of [320,390,1440])for(const theme of ['light','black']){
   const context=await browser.newContext({viewport:{width,height:width>700?1000:844},serviceWorkers:'block',reducedMotion:'reduce'});
   const page=await context.newPage();page.setDefaultTimeout(10000);
   page.on('pageerror',error=>errors.push(error.message));
   page.on('request',request=>{if(/^https?:/.test(request.url()))network.push(request.url());});
   await page.goto(pathToFileURL(html).href+'#projects');
   await page.locator('.project-list-card').first().waitFor();
   await page.locator(`[data-theme-choice="${theme}"]`).click();
   await page.locator(`.project-list-card h2 a[href="#manager?project=${questions.id}"]`).click();
   await page.locator('#message-form').waitFor();
   const question=page.locator('.discussion-status[role="status"]');
   await question.getByText('답변 필요').waitFor();
   assert.match(await question.textContent(),/첫 버전에서 맡길 대표 업무는 무엇인가요/);
   assert.match(await question.textContent(),/웹사이트 제작부터 시작/);
   assert.equal(await page.locator('[data-action="review-plan"]').count(),0);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
   await page.screenshot({path:path.join(output,`pm-question-${theme}-${width}.png`),fullPage:true});
   await page.goto(pathToFileURL(html).href+`#manager?project=${review.id}`);
   await page.locator('#message-form').waitFor();
   assert.match(await page.locator('#main').textContent(),/계획 검토 중/);
   assert.equal(await page.locator('[data-action="review-plan"]').count(),0);
   await page.locator('.manager-plan-detail > summary').first().click();
   await page.getByText('요구사항 · 검증').click();
   assert.match(await page.locator('.manager-plan-detail').textContent(),/격리 브라우저에서 성공·실패 입력/);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
   await page.screenshot({path:path.join(output,`pm-review-${theme}-${width}.png`),fullPage:true});
   const audit=await page.evaluate(()=>window.PREVIEW_AUDIT);
   assert.ok(audit.every(item=>item.method==='GET'));
   checks.push({width,theme,question_project:questions.id,review_project:review.id});
   await context.close();
  }
  assert.deepEqual(errors,[]);assert.deepEqual(network,[]);
  await fs.writeFile(path.join(output,'pm-preview-validation.json'),JSON.stringify({status:'PASS',scope:'packaged synthetic read-only UI; no model/API/approval',source_commit:manifest.source_commit,
    html_sha256:manifest.html_sha256,fixture_sha256:manifest.fixture_sha256,browser:browser.version(),sandbox:true,checks},null,2));
  console.log('PASS: packaged PM question/review in Light/Black 320/390/1440 with sandboxed browser');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
