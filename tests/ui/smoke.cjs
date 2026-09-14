/* Run only against an isolated --demo management server.
   NODE_PATH=/tmp/ai-company-ui-check/node_modules BASE_URL=http://127.0.0.1:PORT
   TEST_TOKEN=<isolated token> node tests/ui/smoke.cjs
   No project dependency or production service is modified. */
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const base = process.env.BASE_URL;
const token = process.env.TEST_TOKEN;
const output = process.env.UI_OUTPUT || '/tmp/ai-company-ui-check/artifacts';
if (!base || !token || !['localhost', '127.0.0.1', '[::1]'].includes(new URL(base).hostname)) {
  throw new Error('An isolated localhost BASE_URL and TEST_TOKEN are required.');
}
(async () => {
  const browser = await chromium.launch({headless:true, chromiumSandbox:true,
    ...(process.env.CHROME_CHANNEL ? {channel:process.env.CHROME_CHANNEL} : {}),
    ...(process.env.CHROMIUM_EXECUTABLE ? {executablePath:process.env.CHROMIUM_EXECUTABLE} : {})});
  const context = await browser.newContext({viewport:{width:1440,height:1000}});
  const page = await context.newPage();
  const failures = [];
  let offlineScenario = false;
  let expectedHTTPFailure = false;
  page.on('pageerror', error => failures.push(error.message));
  page.on('console', msg => { if(msg.type()==='error'&&((!offlineScenario&&!expectedHTTPFailure)||/content.security|violates|CSP/i.test(msg.text()))) failures.push(msg.text()); });
  await fs.mkdir(output, {recursive:true});
  try {
    await page.goto(base);
    await page.getByLabel('접근 토큰', {exact:true}).fill(token);
    await page.getByRole('button', {name:'작업 공간 열기'}).click();
    await page.getByRole('heading', {name:'각자의 흐름으로, 같은 목표를 향해'}).waitFor();
    await page.getByText('모의 예시 데이터', {exact:true}).waitFor();
    assert.equal(await page.locator('.role').count(), 4, 'four parallel fixture roles');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    const fixtureId = await page.locator('#project-select').inputValue();
    await page.screenshot({path:`${output}/desktop-progress.png`,fullPage:true});

    // Project-labelled response fixtures exercise the renderer only, not Dispatcher recovery.
    let recoveryState = 'WAITING_QUOTA';
    await page.route(`**/api/projects/${fixtureId}/overview`, async route=>{
      const response=await route.fetch();const snapshot=await response.json();
      const role=snapshot.roles[0];
      Object.assign(role,{status:recoveryState,wait_reason:'모의 주입: 계정 제한 후 복귀 예약',resume_at:Date.now()/1000+600,
        handoffs:recoveryState==='RUNNING'?[{source:'fixture',from:'fixture-a',to:'fixture-b',reason:'모의 제한 이관'}]:[]});
      for(const task of snapshot.tasks.filter(task=>task.role_id===role.id))task.status=recoveryState;
      await route.fulfill({response,json:snapshot});
    });
    for(const [status,text] of [['WAITING_QUOTA','사용량 대기'],['WAITING_RETRY','재시도 대기'],['WAITING_CAPACITY','후보 복귀 대기'],['RUNNING','진행 중']]){
      recoveryState=status;
      await page.getByRole('button',{name:'새로고침'}).click();
      await page.locator('.role').first().getByText(text,{exact:true}).first().waitFor();
      await page.locator('.role').first().getByText('재확인 예약',{exact:false}).waitFor();
    }
    await page.locator('.role').first().getByText('담당자 이관 이력 1건',{exact:true}).waitFor();
    await page.unroute(`**/api/projects/${fixtureId}/overview`);
    await page.getByRole('button',{name:'새로고침'}).click();

    await page.locator('.nav').getByRole('link', {name:'매니저',exact:true}).click();
    const draft = '독립 작업은 계속 진행하고, PM 판단은 복귀 후 확인합니다.';
    await page.getByLabel('PM에게 전달할 내용').fill(draft);
    await page.locator('.nav').getByRole('link', {name:'보고서',exact:true}).click();
    await page.locator('.nav').getByRole('link', {name:'매니저',exact:true}).click();
    assert.equal(await page.getByLabel('PM에게 전달할 내용').inputValue(),draft,'draft survives navigation');
    await page.waitForTimeout(5300);
    assert.equal(await page.getByLabel('PM에게 전달할 내용').inputValue(),draft,'draft survives polling');
    await page.getByRole('button',{name:'메시지 저장'}).click();
    await page.locator('.message-content').filter({hasText:draft}).waitFor();
    await page.reload();
    await page.locator('.message-content').filter({hasText:draft}).waitFor();
    await page.getByLabel('PM에게 전달할 내용').fill('원래 프로젝트에 남는 초안');

    await page.locator('.nav').getByRole('link',{name:'프로젝트',exact:true}).click();
    await page.getByRole('button',{name:'새 프로젝트'}).click();
    const projectName = `UI persistence ${Date.now()}`;
    await page.getByLabel('프로젝트 이름',{exact:true}).fill(projectName);
    await page.getByLabel('목표와 완료 기준').fill('격리된 UI 회귀 검증: 대화와 하네스 보존을 확인한다.');
    await page.getByLabel('초기 역할 (선택)').fill('화면 검증: 모바일과 데스크톱 확인\n서버 검증: API 상태 보존 확인');
    await page.getByRole('button',{name:'프로젝트 만들기',exact:true}).click();
    await page.getByRole('heading',{name:projectName,exact:true}).waitFor();
    const secondId = await page.locator('#project-select').inputValue();
    assert.notEqual(secondId, fixtureId);
    await page.getByLabel('하네스 초안').fill('UI 회귀 검증 초안\n허용 경로: 격리된 테스트 작업 공간\n운영 실행 금지');
    await page.getByRole('button',{name:'초안 저장',exact:true}).click();
    await page.locator('#toast').filter({hasText:'하네스 초안을 저장했습니다. 아직 활성화되지 않았습니다.'}).waitFor();
    await page.reload();
    await page.locator('#project-select').selectOption(secondId);
    await page.getByRole('heading',{name:projectName,exact:true}).waitFor();
    await page.getByText('저장된 하네스 2개',{exact:true}).waitFor();
    await page.locator('.nav').getByRole('link',{name:'매니저',exact:true}).click();
    assert.equal(await page.getByLabel('PM에게 전달할 내용').inputValue(),'','project drafts are isolated');
    await page.locator('#project-select').selectOption(fixtureId);
    await page.getByText('모의 예시 데이터',{exact:true}).waitFor();

    // These plans were inserted in temporary state without any model call or worker.
    // A successful confirmation creates only a pending run; this is not a live E2E claim.
    const [confirmFixture, staleFixture] = JSON.parse(process.env.PLAN_FIXTURES || '[]');
    assert.ok(confirmFixture && staleFixture, 'isolated plan fixtures are required');
    await page.locator('#project-select').selectOption(confirmFixture.project_id);
    await page.locator('.plan').filter({has:page.locator(`[data-id="${confirmFixture.plan_id}"]`)}).waitFor();
    await page.getByRole('button',{name:'계획 검토·확정'}).click();
    await page.getByRole('heading',{name:'계획 확정 검토',exact:true}).waitFor();
    assert.equal(await page.locator('#plan-form').getAttribute('data-digest'),confirmFixture.digest);
    assert.equal(await page.locator('dialog').getByText('src/api/',{exact:true}).count(),1);
    await page.setViewportSize({width:360,height:800});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'plan dialog fits mobile');
    await page.screenshot({path:`${output}/mobile-plan-confirm.png`,fullPage:true});
    await page.setViewportSize({width:1440,height:1000});
    // Hash navigation while a dialog is open must discard that project's approval form.
    await page.evaluate(project=>{location.hash=`manager?project=${project}`;}, fixtureId);
    await page.waitForFunction(()=>!document.querySelector('#dialog').open);
    assert.equal(await page.locator('#project-select').inputValue(),fixtureId);
    await page.locator('#project-select').selectOption(confirmFixture.project_id);
    await page.getByRole('button',{name:'계획 검토·확정'}).click();
    await page.getByLabel('역할·허용 경로·완료 기준을 확인했습니다.').check();
    const confirmPath = `/api/projects/${confirmFixture.project_id}/plans/${confirmFixture.plan_id}/confirm`;
    const confirmBodies = [];
    let responseLost = false;
    await page.route(`**${confirmPath}`, async route=>{
      confirmBodies.push(route.request().postDataJSON());
      if(!responseLost){
        responseLost=true;
        const saved=await route.fetch();
        assert.equal(saved.status(),200,'server confirmed before response interruption');
        await route.fulfill({status:502,contentType:'application/json',body:JSON.stringify({error:{code:'fixture_response_lost',message:'모의 응답 중단: 같은 요청으로 다시 확인하세요.'}})});
      }else await route.continue();
    });
    expectedHTTPFailure=true;
    await page.getByRole('button',{name:'이 계획 확정',exact:true}).click();
    await page.locator('#plan-error').filter({hasText:'모의 응답 중단'}).waitFor();
    await page.getByRole('button',{name:'이 계획 확정',exact:true}).click();
    await page.waitForFunction(()=>!document.querySelector('#dialog').open);
    expectedHTTPFailure=false;
    await page.unroute(`**${confirmPath}`);
    assert.equal(confirmBodies.length,2);
    assert.deepEqual(confirmBodies[0],confirmBodies[1],'response retry keeps plan digest, base and idempotency key');
    await page.getByText('계획 확정',{exact:true}).waitFor();
    const confirmed = await page.evaluate(async project=>{
      const response=await fetch(`/api/projects/${project}/overview`);return response.json();
    },confirmFixture.project_id);
    assert.equal(confirmed.runs.length,1,'response loss does not duplicate the automatic run');
    assert.equal(confirmed.runs[0].mode,'fixture','fixture PM evidence never becomes a live run');
    await page.locator('.nav').getByRole('link',{name:'역할별 진행',exact:true}).click();
    await page.getByRole('region',{name:'자동 실행'}).waitFor();
    await page.getByText(confirmed.runs[0].id,{exact:true}).waitFor();

    // Switch view and project in one browser turn, before the queued hashchange fires.
    // Navigation must not let the old progress view overwrite the manager fragment.
    await page.evaluate(project=>{
      document.querySelector('.nav a[aria-label="매니저"]').click();
      const picker=document.querySelector('#project-select');
      picker.value=project;picker.dispatchEvent(new Event('change',{bubbles:true}));
    },staleFixture.project_id);
    await page.locator(`.plan[data-plan-id="${staleFixture.plan_id}"]`).waitFor();
    assert.equal(await page.locator('.nav a[aria-current=page]').getAttribute('aria-label'),'매니저','immediate project switch retains the requested view');
    await page.locator(`.plan[data-plan-id="${staleFixture.plan_id}"]`).getByRole('button',{name:'계획 검토·확정'}).click();
    await page.getByLabel('역할·허용 경로·완료 기준을 확인했습니다.').check();
    // Another authenticated client revises the request after the master opened the plan.
    const revised = await page.evaluate(async project=>{
      const session=await (await fetch('/api/session')).json();
      const response=await fetch(`/api/projects/${project}/messages`,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf_token},body:JSON.stringify({content:'UI fixture: change completion criteria after plan was opened'})});
      return response.status;
    },staleFixture.project_id);
    assert.equal(revised,200);
    expectedHTTPFailure=true;
    await page.getByRole('button',{name:'이 계획 확정',exact:true}).click();
    await page.locator('#plan-error').filter({hasText:'최신 계획을 다시 검토'}).waitFor();
    assert.equal(await page.getByRole('button',{name:'이 계획 확정',exact:true}).isDisabled(),true,'stale plan cannot be blindly retried');
    expectedHTTPFailure=false;
    await page.getByRole('button',{name:'취소',exact:true}).click();
    await page.getByRole('region',{name:'PM 요청 상태'}).waitFor();
    await page.locator('.nav').getByRole('link',{name:'보고서',exact:true}).first().click();
    await page.getByRole('heading',{name:'진행 상황을, 근거와 함께'}).waitFor();
    await page.locator('.nav').getByRole('link',{name:'승인',exact:true}).first().click();
    await page.getByRole('heading',{name:'검토할 실행만, 한곳에'}).waitFor();
    await page.locator('#project-select').selectOption(fixtureId);

    await page.locator('.nav').getByRole('link',{name:'승인',exact:true}).click();
    await page.getByRole('button',{name:'승인 검토'}).first().click();
    await page.getByRole('heading',{name:'실행 승인 기록',exact:true}).waitFor();
    await page.getByLabel('검토 의견 (선택)').fill('모의 UI 시나리오에서 대상·환경·기한을 확인했습니다. 실제 실행 아님.');
    await page.getByRole('button',{name:'승인 기록',exact:true}).click();
    await page.getByText('승인 기록됨',{exact:true}).first().waitFor();
    await page.reload();
    await page.getByText('승인 기록됨',{exact:true}).first().waitFor();

    assert.equal(await page.locator('#project-select').inputValue(),fixtureId,'approval reload retains fixture project');
    await page.getByText('모의 예시 데이터',{exact:true}).waitFor();
    await page.setViewportSize({width:360,height:800});
    for(const [view, title] of [['progress','역할별 진행'],['manager','매니저'],['reports','보고서'],['approvals','승인'],['project','프로젝트']]) {
      await page.locator('.nav').getByRole('link',{name:title,exact:true}).click();
      await page.waitForFunction(expected => document.querySelector('.nav a[aria-current=page]')?.getAttribute('href')?.startsWith(`#${expected}?`), view);
      assert.equal(await page.locator('#project-select').inputValue(),fixtureId,`${view} keeps selected fixture`);
      await page.getByText('모의 예시 데이터',{exact:true}).waitFor();
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`${view} fits 360px`);
      await page.screenshot({path:`${output}/mobile-${view}.png`,fullPage:true});
    }
    await page.locator('.nav').getByRole('link',{name:'매니저',exact:true}).click();
    offlineScenario = true;
    await context.setOffline(true);
    await page.getByText('연결 끊김',{exact:true}).waitFor();
    assert.equal(await page.getByRole('button',{name:'메시지 저장'}).isDisabled(),true,'offline writes disabled');
    await context.setOffline(false);
    await page.waitForFunction(()=>{const button=document.querySelector('#message-form button[type=submit]');return button&&!button.disabled;});
    offlineScenario = false;
    const stored = await page.evaluate(async()=>({local:localStorage.length,session:sessionStorage.length,
      cached:(await Promise.all((await caches.keys()).map(async key=>(await (await caches.open(key)).keys()).map(request=>new URL(request.url).pathname)))).flat()}));
    assert.equal(stored.local,0,'no bearer token in localStorage');
    assert.equal(stored.session,0,'no bearer token in sessionStorage');
    assert.equal(stored.cached.some(path=>path.startsWith('/api/')),false,'no authenticated API cache');
    await page.locator('.mobile-logout').click();
    await page.getByRole('heading',{name:'작업 공간에 연결',exact:true}).waitFor();
    // Resource errors caused by deliberately toggling offline are expected, CSP/JS errors are not.
    const unexpected=failures.filter(message=>!message.includes('ERR_INTERNET_DISCONNECTED')&&!message.includes('Failed to fetch'));
    assert.deepEqual(unexpected,[],'no browser JavaScript/CSP failures');
    console.log(JSON.stringify({status:'PASS',views:5,mobile_width:360,checks:['fixture labels','parallel role columns','message persistence','draft navigation/polling','project isolation','harness draft','bound approval persistence','stored plan confirmation (fixture)','confirmation response-loss idempotency','stale plan rejection','plan project switch','PM wait navigation','quota/retry/capacity/handoff rendering (response fixtures)','offline writes','no authenticated caches','no browser errors'],artifacts:output}));
  } catch(error) {
    await page.screenshot({path:`${output}/failure.png`,fullPage:true}).catch(()=>{});
    console.error(JSON.stringify({scope:'UI fixture failure',url:page.url(),project:await page.locator('#project-select').inputValue().catch(()=>null),active_view:await page.locator('.nav a[aria-current=page]').getAttribute('aria-label').catch(()=>null)}));
    throw error;
  } finally { await browser.close(); }
})().catch(error=>{console.error(error.stack||error.message);process.exitCode=1;});
