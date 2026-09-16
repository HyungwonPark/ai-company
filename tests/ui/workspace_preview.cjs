/* Offline browser review. Every resource is intercepted, no running API is used. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const web=path.resolve(__dirname,'../../src/ai_company/web/workspace-preview');
const out=process.env.UI_OUTPUT||'/tmp/ai-company-workspace-preview';
const origin='http://127.0.0.1:47993';
(async()=>{
 await fs.mkdir(out,{recursive:true});
 const browser=await chromium.launch({headless:true,chromiumSandbox:true,...(process.env.CHROME_CHANNEL?{channel:process.env.CHROME_CHANNEL}:{}),...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
 const context=await browser.newContext({viewport:{width:1440,height:1050},serviceWorkers:'block'});
 const page=await context.newPage();page.setDefaultTimeout(6000);
 const errors=[],requests=[],checks=[];
 page.on('pageerror',e=>errors.push(e.message));
 await context.route('**/*',async route=>{
  const req=route.request(),url=new URL(req.url());requests.push(url.pathname);
  assert.equal(url.origin,origin);assert.equal(req.method(),'GET');assert.ok(!url.pathname.startsWith('/api/'));
  const file=path.resolve(web,'.'+(url.pathname==='/'?'/index.html':url.pathname));assert.ok(file.startsWith(web+path.sep));
  try{await route.fulfill({body:await fs.readFile(file),contentType:({'.html':'text/html','.js':'text/javascript','.css':'text/css'})[path.extname(file)]||'application/octet-stream'});}catch(e){if(e.code==='ENOENT')await route.fulfill({status:404,body:''});else throw e;}
 });
 const click=action=>page.locator(`[data-action="${action}"]`).first().click();
 const snapshot=async name=>{await page.evaluate(()=>document.fonts.ready);await page.screenshot({path:path.join(out,`workspace-${name}.png`),fullPage:true});};
 const overflow=async label=>{const x=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));assert.ok(x.scroll<=x.width,`${label} overflow ${JSON.stringify(x)}`);};
 const state=()=>page.evaluate(()=>JSON.parse(sessionStorage.getItem('ai-company:workspace-design:v1')));
 async function scenario(name){await page.locator('.lab-options').evaluate(el=>el.open=true);await page.locator('#lab-scenario').selectOption(name);await page.locator('.lab-options').evaluate(el=>el.open=false);}
 async function reset(){await page.evaluate(()=>sessionStorage.clear());await page.goto(origin);}
 try{
  await page.goto(origin);
  // Same fixture content for genuinely different layouts, Light/Black, all target widths.
  for(const width of [1440,390,320])for(const theme of ['light','black'])for(const concept of ['a','b','c']){
   await page.setViewportSize({width,height:width>700?1050:844});
   if(await page.locator('#workspace-lab').getAttribute('data-theme')!==theme)await click('theme');
   await page.locator(`button[data-concept="${concept}"]`).click();
   await overflow(`${width}/${theme}/${concept}`);
   await snapshot(`${concept}-${theme}-${width}`);
   assert.ok(await page.getByText('운영 연결 없음',{exact:false}).count());
  }
  checks.push('A/B/C × Light/Black × 320/390/1440: render, overflow, screenshots');
  await reset();await page.setViewportSize({width:390,height:844});
  await click('suggest');await page.locator('#lab-message').fill('알 수 없음으로 모으고 입력은 바꾸지 마세요.');
  await page.locator('#lab-message').evaluate(el=>{el.focus();el.setSelectionRange(4,4);});
  await page.reload();assert.equal(await page.locator('#lab-message').inputValue(),'알 수 없음으로 모으고 입력은 바꾸지 마세요.');
  assert.equal(await page.evaluate(()=>document.activeElement.id),'lab-message');
  assert.equal(await page.locator('#lab-message').evaluate(el=>el.selectionStart),4);
  await page.locator('#lab-message-form button[type=submit]').click();
  await page.locator('[data-action=save-spec]').waitFor();
  assert.equal((await state()).model.projects[0].runs.length,0);
  await click('save-spec');await page.locator('[data-action=request-plan]').waitFor();
  assert.equal((await state()).model.projects[0].runs.length,0);
  await click('request-plan');await page.locator('[data-action=review-plan]').waitFor();
  assert.equal((await state()).model.projects[0].runs.length,0);
  await click('review-plan');await page.locator('#lab-confirm-reviewed').check();
  await snapshot('a-light-390-plan');
  await page.locator('#lab-confirm-form').evaluate(form=>{form.requestSubmit();form.requestSubmit();});
  await page.locator('#lab-dialog').waitFor({state:'hidden'});
  assert.equal((await state()).model.projects[0].runs.length,1);
  await page.reload();assert.equal((await state()).model.projects[0].runs.length,1);
  await page.locator('.tabs [data-tab=team]').click();
  await page.getByText('실제 작업은 시작되지 않았습니다.',{exact:false}).waitFor();
  assert.equal(await page.locator('[data-action=artifact]').count(),0);
  await snapshot('a-light-390-confirmed');
  checks.push('PM reply → spec save (0 runs) → fresh plan (0 runs) → reviewed confirmation (1 run), reload stable');
  // Existing seed run has a received artifact; no fake observation of model identity.
  await reset();await page.locator('button[data-concept=b]').click();await click('artifact');
  assert.ok((await page.locator('#lab-dialog').textContent()).includes('fixture-run-02'));
  await page.keyboard.press('Escape');assert.equal(await page.evaluate(()=>document.activeElement.dataset.action),'artifact');
  await click('role');assert.ok((await page.locator('#lab-dialog').textContent()).includes('실제 적용 미확인'));await click('close');
  // Candidate subject stays immutable; choice is a separate fixture receipt.
  await page.locator('button[data-concept=c]').click();await click('review-decision');
  await page.locator('#lab-decision-reviewed').check();await page.locator('#lab-decision-form button[type=submit]').click();
  await page.getByText('예시 선택: 수용',{exact:true}).waitFor();
  const chosen=await state();assert.equal(chosen.model.decisions[0].subject_digest,'b'.repeat(64));
  assert.equal(chosen.model.decisions[0].scope,'preview_only');
  checks.push('artifact run/commit, requested/unverified model, modal Escape focus, candidate digest receipt');
  // Creation lost-response recovery across reload, no duplicate projects or starts.
  await reset();await click('projects');await scenario('error');await click('new');
  await page.locator('#lab-new-name').fill('나의 검증');await page.locator('#lab-new-goal').fill('역할 상태를 한눈에 확인하고 싶어요.');
  await page.locator('#lab-new-form button[type=submit]').click();
  await page.locator('.inline-error').filter({hasText:'응답'}).waitFor();
  assert.equal((await state()).model.projects.length,3);await page.reload();
  await click('new');assert.equal(await page.locator('#lab-new-name').inputValue(),'나의 검증');
  await page.locator('#lab-new-name').fill('수정한 입력은 별도 보관');
  await click('close');await click('recover');
  await page.getByRole('heading',{name:'나의 검증',exact:true}).waitFor();
  assert.equal((await state()).ui.drafts['new-name'],'수정한 입력은 별도 보관');
  assert.equal((await state()).model.projects.length,3);
  assert.equal((await state()).model.projects[2].runs.length,0);
  await page.locator('.tabs [data-tab=team]').click();await page.getByRole('heading',{name:'아직 시작하지 않았어요'}).waitFor();
  await page.locator('.tabs [data-tab=approval]').click();await page.getByRole('heading',{name:'요청이 없어요'}).waitFor();
  checks.push('creation response loss/reload/retry: one project, no execution, no cross-project roles/approvals');
  for(const name of ['empty','waiting','offline','long','translation','readonly','missing']){
   await reset();await scenario(name);
   if(name==='empty')await click('projects');
   if(name==='translation')await page.locator('.tabs [data-tab=approval]').click();
   if(['offline','readonly'].includes(name)){
    await page.locator('#lab-message').fill('보관할 초안');await page.locator('#lab-message-form button[type=submit]').click();
    assert.equal((await state()).model.events.length,0);assert.equal(await page.locator('#lab-message').inputValue(),'보관할 초안');
   }
   await overflow(name);await snapshot(`state-${name}-390`);
  }
  checks.push('empty, waiting, offline, long Korean, translation pending, read-only, bad link');
  // Keyboard-only entry and mobile-sized touch controls.
  await reset();await page.setViewportSize({width:320,height:844});
  await page.locator('[data-action=projects]').first().focus();await page.keyboard.press('Enter');
  await page.locator('[data-action=new]').first().focus();await page.keyboard.press('Enter');
  await page.keyboard.press('Tab');await page.keyboard.type('키보드 프로젝트');
  await page.keyboard.press('Tab');await page.keyboard.type('키보드로 PM과 대화합니다.');
  await page.keyboard.press('Tab');await page.keyboard.press('Enter');
  await page.getByRole('heading',{name:'키보드 프로젝트'}).waitFor();
  // 200% text magnification in a 720px viewport exercises reflow independently of DPR.
  await page.setViewportSize({width:720,height:1050});
  await page.addStyleTag({content:'#workspace-lab{font-size:32px} #workspace-lab button,#workspace-lab input,#workspace-lab textarea,#workspace-lab p{font-size:200%}'});
  await overflow('text magnification');await snapshot('text-zoom-200');
  await reset();await page.setViewportSize({width:390,height:844});
  const targets=await page.locator('#lab-stage button').evaluateAll(els=>els.filter(el=>{const b=el.getBoundingClientRect();return b.width<44||b.height<44}).map(el=>el.textContent));assert.deepEqual(targets,[]);
  const cdp=await context.newCDPSession(page);await cdp.send('DOM.enable');await cdp.send('CSS.enable');
  const doc=await cdp.send('DOM.getDocument');const node=await cdp.send('DOM.querySelector',{nodeId:doc.root.nodeId,selector:'.pm-question'});
  const fonts=await cdp.send('CSS.getPlatformFontsForNode',{nodeId:node.nodeId});
  assert.ok(fonts.fonts.some(f=>/Noto Sans CJK/.test(f.familyName)&&f.glyphCount>0),JSON.stringify(fonts));
  checks.push('keyboard creation, 200% text stress, 44px stage controls, actual Korean glyph font');
  assert.deepEqual(errors,[]);
  const evidence={source:'isolated synthetic browser',browser:browser.version(),sandbox:true,checks,fonts:fonts.fonts,api_requests:requests.filter(s=>s.startsWith('/api/')).length,page_errors:errors};
  await fs.writeFile(path.join(out,'workspace-validation.json'),JSON.stringify(evidence,null,2));console.log(JSON.stringify(evidence,null,2));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
