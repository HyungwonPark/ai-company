/* Real authenticated v2 writes in a disposable API; model responses are synthetic. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');

(async()=>{
 const base=process.env.BASE_URL,control=process.env.CONTROL_URL,out=process.env.UI_OUTPUT||'/tmp/ai-company-pm-v2';
 assert.equal(new URL(base).hostname,'127.0.0.1');assert.equal(new URL(control).hostname,'127.0.0.1');
 await fs.mkdir(out,{recursive:true});
 const browser=await chromium.launch({headless:true,chromiumSandbox:true,
  ...(process.env.CHROME_CHANNEL?{channel:process.env.CHROME_CHANNEL}:{}),
  ...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
 const context=await browser.newContext({viewport:{width:390,height:844},serviceWorkers:'block',reducedMotion:'reduce'});
 const page=await context.newPage();page.setDefaultTimeout(15000);
 const errors=[],violations=[],checks=[],captures=[];let stage='login';
 page.on('pageerror',error=>errors.push(error.message));
 await context.route('**/*',route=>{const request=route.request(),url=new URL(request.url());
  if(url.origin!==base){violations.push(url.origin);return route.abort();}return route.continue();});
 const command=async(name,body={})=>{const response=await fetch(control+'/'+name,{method:'POST',headers:{'X-Fixture-Token':process.env.CONTROL_TOKEN,'Content-Type':'application/json'},body:JSON.stringify(body)});
  const value=await response.json();assert.equal(response.status,200,JSON.stringify(value));return value;};
 const overview=pid=>page.evaluate(async id=>(await(await fetch('/api/projects/'+id+'/overview')).json()),pid);
 const request=(url,body)=>page.evaluate(async({url,body})=>{const session=await(await fetch('/api/session')).json();const response=await fetch(url,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf_token},body:JSON.stringify(body)});return {status:response.status,body:await response.json()};},{url,body});
 const until=async(predicate,label)=>{for(let attempt=0;attempt<14;attempt++){const state=await command('step');if(predicate(state))return state;}throw new Error('Fixture worker did not reach '+label);};
 const go=async hash=>{await page.evaluate(value=>{location.hash=value;},hash);await page.waitForTimeout(120);};
 const capture=async name=>{await page.evaluate(()=>document.fonts.ready);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'mobile overflow: '+name);const filename='pm-v2-'+name+'.png';await page.screenshot({path:path.join(out,filename),fullPage:true});captures.push(filename);};
 const matrix=async name=>{for(const width of [320,390,1440])for(const theme of ['light','black']){await page.setViewportSize({width,height:width>700?1000:844});await page.locator(`[data-theme-choice="${theme}"]`).click();await capture(`${name}-${theme}-${width}`);}await page.setViewportSize({width:390,height:844});await page.locator('[data-theme-choice="light"]').click();};
 const createProject=async(name,goal)=>{await go('#projects');await page.locator('#project-create').click();await page.locator('#project-name').fill(name);await page.getByLabel('목표',{exact:true}).fill(goal);await page.locator('#create-form button[type=submit]').click();await page.locator('#message-form').waitFor();return new URLSearchParams(new URL(page.url()).hash.split('?')[1]).get('project');};
 try{
  await page.goto(base+'/');await page.getByLabel('비밀번호',{exact:true}).fill(process.env.TEST_PASSWORD);await page.getByRole('button',{name:'로그인',exact:true}).click();
  await page.getByLabel('현재 비밀번호',{exact:true}).fill(process.env.TEST_PASSWORD);await page.getByLabel('새 비밀번호',{exact:true}).fill('pm-v2-browser-fixture-password');await page.getByLabel('새 비밀번호 확인',{exact:true}).fill('pm-v2-browser-fixture-password');await page.getByRole('button',{name:'비밀번호 변경 후 계속'}).click();await page.locator('#project-create').waitFor();
  stage='goal and material question';
  const pid=await createProject('PM v2 검증','첫 업무를 정한 뒤 두 역할로 작은 기능을 만들고 검사하고 싶어요.');assert.ok(pid);
  await command('bind',{project_id:pid});let state=await until(s=>s.requests.some(r=>r.state==='answer_needed'),'answer_needed');
  assert.equal(state.plans.length,0);assert.equal(state.runs.length,0);assert.equal(state.role_tasks.length,0);
  await page.reload();await page.getByText('첫 버전의 대표 업무는 무엇인가요?').waitFor();await matrix('question');
  checks.push('browser goal creates v2 request; material question waits without a plan, run or role task');

  stage='lost answer response and durable retry';
  const answer='첫 버전은 작은 함수 개발입니다. 빈 입력을 포함해 독립 검사해주세요.';
  const endpoint=`/api/projects/${pid}/messages`;let intercepted=0;
  await page.route('**'+endpoint,async route=>{if(route.request().method()!=='POST'||intercepted++)return route.continue();await route.fetch();await route.abort('failed');});
  await page.locator('#message-content').fill(answer);await page.locator('#message-form button[type=submit]').click();
  await page.getByText('전송 결과를 확인하지 못했습니다.',{exact:false}).waitFor();
  let before=await overview(pid);assert.equal(before.messages.filter(m=>m.role==='user'&&m.content===answer).length,1);
  await page.unroute('**'+endpoint);await page.reload();
  if(await page.locator('#message-content').inputValue()!==answer)await page.locator('#message-content').fill(answer);
  await page.locator('#message-form button[type=submit]').click();
  await page.locator('#toast').filter({hasText:'메시지를 저장했습니다.'}).waitFor();
  let after=await overview(pid);assert.equal(after.messages.filter(m=>m.role==='user'&&m.content===answer).length,1);
  assert.equal(after.pm_requests.length,before.pm_requests.length,'same idempotency key produces one answer request');
  await command('restart');checks.push('lost POST response, browser reload, same-key retry and worker restart keep one answer');

  stage='new specification and independent REVISE';
  state=await until(s=>s.plans.some(p=>p.status==='reviewing'),'reviewing');
  const first=(await overview(pid)).plans.at(-1);assert.equal(first.contract_version>=2,true);
  const premature=await request(`/api/projects/${pid}/plans/${first.id}/confirm`,{plan_digest:first.digest,base_harness_version:first.base_harness_version,idempotency_key:'premature-v2-confirm'});
  assert.equal(premature.status,409);assert.equal((await overview(pid)).runs.length,0);
  await page.reload();assert.equal(await page.locator('[data-action="review-plan"]:visible').count(),0,'reviewing plan offers no confirmation button');
  state=await until(s=>s.plans.some(p=>p.review==='REVISE'),'review REVISE');
  assert.equal(state.runs.length,0);assert.equal(state.role_tasks.length,0);
  const userMessages=(await overview(pid)).messages.filter(m=>m.role==='user').length;
  checks.push('direct API and browser confirmation blocked before separate content review');

  stage='automatic technical repair and PASS';
  state=await until(s=>s.plans.some(p=>p.review==='PASS'&&p.status==='proposed'),'re-reviewed proposed plan');
  const current=(await overview(pid)).plans.at(-1);
  assert.equal(current.content.skill_selection.status,'selected');
  assert.equal(current.content.skill_selection.roles.impl[0].selected,true);
  assert.notEqual(current.digest,first.digest,'repair creates a new plan digest');
  assert.equal((await overview(pid)).messages.filter(m=>m.role==='user').length,userMessages,'technical repair adds no fake master answer');
  assert.equal((await overview(pid)).runs.length,0);assert.equal(state.role_tasks.length,0);
  const old=await request(`/api/projects/${pid}/plans/${first.id}/confirm`,{plan_digest:first.digest,base_harness_version:first.base_harness_version,idempotency_key:'stale-revision-confirm'});
  assert.equal(old.status,409);
  await page.reload();await page.locator('[data-action="review-plan"]:visible').waitFor();await matrix('review-pass');
  await page.locator('.manager-plan-detail > summary').first().click();
  const skillDetail=page.locator('.manager-plan-detail .role-skills details summary').first();
  await skillDetail.focus();await page.keyboard.press('Enter');
  assert.equal(await page.locator('.manager-plan-detail .role-skills details').first().getAttribute('open'),'');
  checks.push('REVISE returns to bounded PM repair and a new independent PASS; old plan cannot be confirmed');

  stage='exact test-client confirmation and two-role assignment';
  const confirmation=await request(`/api/projects/${pid}/plans/${current.id}/confirm`,{plan_digest:current.digest,base_harness_version:current.base_harness_version,idempotency_key:'pm-v2-browser-test-client-confirm'});
  assert.equal(confirmation.status,200,JSON.stringify(confirmation.body));
  const run=confirmation.body.run;assert.equal(run.plan_id,current.id);assert.equal(run.plan_digest,current.digest);
  state=await command('assign');assert.equal(state.runs.length,1);assert.deepEqual(state.runs[0].roles,['impl','test']);assert.equal(state.role_tasks.length,2);
  checks.push('test client confirms exact reviewed plan once; coordinator assigns two roles only afterward');

  stage='project scope and logout';
  await go(`#manager?project=${pid}`);await page.locator('#message-content').fill('첫 프로젝트에만 남는 초안');
  const second=await createProject('별도 프로젝트','다른 프로젝트의 스킬 선택을 검토합니다.');assert.notEqual(second,pid);
  assert.notEqual(await page.locator('#message-content').inputValue(),'첫 프로젝트에만 남는 초안');
  await page.locator('#message-content').fill('두 번째 프로젝트에만 남는 초안');
  await go(`#manager?project=${pid}`);assert.equal(await page.locator('#message-content').inputValue(),'첫 프로젝트에만 남는 초안');
  await go(`#manager?project=${second}`);assert.equal(await page.locator('#message-content').inputValue(),'두 번째 프로젝트에만 남는 초안');
  await command('bind',{project_id:second});state=await until(s=>s.plans.some(p=>p.status==='reviewing'),'other project reviewing');
  const beforeChange=(await overview(second)).plans.at(-1);
  const change=await request(`/api/projects/${second}/messages`,{content:'검사 역할의 작업 지침 선택을 바꿔주세요.',idempotency_key:'second-project-skill-change'});
  assert.equal(change.status,200);
  const stale=await request(`/api/projects/${second}/plans/${beforeChange.id}/confirm`,{plan_digest:beforeChange.digest,base_harness_version:beforeChange.base_harness_version,idempotency_key:'stale-skill-choice-confirm'});
  assert.equal(stale.status,409);assert.equal((await overview(second)).runs.length,0);
  const firstOverview=await overview(pid);assert.equal(firstOverview.runs[0].id,run.id,'other project cannot replace first project run');
  await page.locator('.logout:visible').click();await page.getByLabel('비밀번호',{exact:true}).waitFor();
  const retained=await page.evaluate(()=>Object.keys(sessionStorage).filter(key=>key.includes('message')||key.includes('draft')));
  assert.equal(retained.length,0,'logout clears private message drafts and keys');
  await page.getByLabel('비밀번호',{exact:true}).fill('pm-v2-browser-fixture-password');await page.getByRole('button',{name:'로그인',exact:true}).click();
  await go(`#manager?project=${second}`);await page.locator('#message-content').waitFor();assert.equal(await page.locator('#message-content').inputValue(),'');
  checks.push('new skill-selection request invalidates in-flight review; project drafts are scoped and logout removes keys');

  assert.deepEqual(errors,[]);assert.deepEqual(violations,[]);
  const evidence={status:'PASS',scope:'temporary real API + sandboxed Chrome + synthetic model and reviewer; automated test client, not master',
   project_id:pid,plan_id:current.id,plan_digest:current.digest,run_id:run.id,repair_of:first.id,
   checks,captures,errors,violations};
  await fs.writeFile(path.join(out,'pm-v2-flow.json'),JSON.stringify(evidence,null,2));
  console.log(JSON.stringify({status:evidence.status,checks:checks.length,captures:captures.length,run_id:run.id}));
 }catch(error){console.error('PM v2 browser stage:',stage);throw error;}finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
