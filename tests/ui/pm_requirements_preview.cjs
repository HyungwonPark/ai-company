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
   const skillDetail=page.locator('.manager-plan-detail .role-skills details summary').first();
   await skillDetail.focus();await page.keyboard.press('Enter');
   assert.equal(await page.locator('.manager-plan-detail .role-skills details').first().getAttribute('open'),'');
   assert.match(await page.locator('.manager-plan-detail .role-skills').first().textContent(),/화면 점검 · 예시/);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
   await page.screenshot({path:path.join(output,`pm-review-${theme}-${width}.png`),fullPage:true});
   const audit=await page.evaluate(()=>window.PREVIEW_AUDIT);
   assert.ok(audit.every(item=>item.method==='GET'));
   checks.push({width,theme,question_project:questions.id,review_project:review.id});
   await context.close();
  }
  const context=await browser.newContext({viewport:{width:390,height:844},serviceWorkers:'block',reducedMotion:'reduce'});
  const page=await context.newPage();page.setDefaultTimeout(10000);
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{if(/^https?:/.test(request.url()))network.push(request.url());});
  await page.goto(pathToFileURL(html).href+'#projects');
  await page.locator('.project-list-card').first().waitFor();
  const planSource=(await fs.readFile('src/ai_company/web/plan-ui.js','utf8'))
    .replace('export function createPlanUI','window.createPlanUI = function createPlanUI');
  await page.addScriptTag({content:planSource});
  await page.evaluate(()=>{
   const esc=value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
   const documents={text:(_id,_field,fallback)=>fallback,meta:()=>''};
   const skill=(name,selected=true)=>({name,status:selected?'approved_document':'review_pending',selected,
     reason:'역할에 맞는 지침',source_url:'https://example.org/skill',version:'v1',bundle_sha256:'fixture'});
   const roles=[{key:'web',name:'화면 개발'},{key:'test',name:'검사'}];
   const plan={id:'limit-fixture',content:{summary:'스킬 상한 확인',roles,skill_selection:{
     outcome:'review_pending',role_outcomes:{web:'limit_reached',test:'review_pending'},
     role_reasons:{web:'역할별 최대 3개 · 부족 역량 performance · 공개 후보 public-d 제외'},
     roles:{web:[skill('승인 지침 A'),skill('승인 지침 B'),skill('승인 지침 C')],test:[skill('공개 후보',false)]}}}};
   document.querySelector('#main').innerHTML=window.createPlanUI({esc,documents}).render(plan);
  });
  assert.equal(await page.locator('.role-skills').first().locator('.role-skill-list > li').count(),3);
  assert.match(await page.locator('.role-skills').first().textContent(),/부족 역량 performance · 공개 후보 public-d 제외/);
  assert.match(await page.locator('.role-skills').nth(1).textContent(),/공개 후보/);
  assert.doesNotMatch(await page.locator('#main').textContent(),/자료 조회 실패/);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await page.screenshot({path:path.join(output,'pm-skill-limit-light-390.png'),fullPage:true});
  checks.push({width:390,theme:'light',scenario:'skill limit and independent role',mode:'component fixture'});
  await context.close();
  assert.deepEqual(errors,[]);assert.deepEqual(network,[]);
  await fs.writeFile(path.join(output,'pm-preview-validation.json'),JSON.stringify({status:'PASS',scope:'packaged synthetic read-only UI; no model/API/approval',source_commit:manifest.source_commit,
    html_sha256:manifest.html_sha256,fixture_sha256:manifest.fixture_sha256,browser:browser.version(),sandbox:true,checks},null,2));
  console.log('PASS: packaged PM question/review in Light/Black 320/390/1440 with sandboxed browser');
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
