/* API response fixtures only; no operating catalog registration or model call. */
const assert=require('node:assert/strict');
const {selectTheme,settledScreenshot}=require('./capture.cjs');
module.exports=async function({page,fixtureId,output,setHTTPFixture}){
  const catalogPath='**/api/execution-catalog',specPath=`**/api/projects/${fixtureId}/execution-specs`;
  const overviewPath=`**/api/projects/${fixtureId}/overview`,messagePath=`**/api/projects/${fixtureId}/messages`;
  const confirmPath=`**/api/projects/${fixtureId}/plans/execution-plan/confirm`;
  const selection={catalog_id:'bounded-demo',catalog_digest:'b'.repeat(64),allowed_paths:['src/only.py','tests/test_only.py'],candidates:{developer:['developer-a']},role_candidates:{implementation:['developer-a'],tests:['developer-a']},budget:{max_cost_usd:0.5}};
  const summary={repository:'fixture-company/example',base_branch:'fixture-main',base_sha:'c'.repeat(40),allowed_paths:selection.allowed_paths,candidates:{pm:['pm'],developer:['developer-a'],reviewer:['reviewer'],final:['final']},role_candidates:selection.role_candidates,agents:[{agent_id:'developer-a',model:'fixture-Claude',reasoning_effort:'xhigh',enabled:true,ultracode_enabled:true},{agent_id:'pm',model:'fixture-Astra',reasoning_effort:'ultra'},{agent_id:'reviewer',model:'fixture-Astra',reasoning_effort:'high'},{agent_id:'final',model:'fixture-Astra',reasoning_effort:'ultra'}],checks:{unit:{argv:['python','-m','unittest']}},budget:{max_cost_usd:1,max_runtime_seconds:300,max_executions:5,max_repairs:1},max_parallel:2,mode:'fixture',ci:{workflow_path:'.github/workflows/fixture.yml'},limitations:['명세 등록은 실행 승인이 아닙니다.']};
  const entry={...summary,catalog_id:selection.catalog_id,catalog_digest:selection.catalog_digest};
  const saved={id:'saved-spec',project_id:fixtureId,version:1,digest:'d'.repeat(64),catalog_id:entry.catalog_id,catalog_digest:entry.catalog_digest,selection,resolved:{...summary,budget:{...summary.budget,max_cost_usd:.5}},created_at:1000};
  const reference={version:1,digest:saved.digest,catalog_id:entry.catalog_id,catalog_digest:entry.catalog_digest};
  const content={summary:'두 역할로 정해진 범위를 검사합니다.',roles:[{key:'implementation',name:'개발',responsibility:'작은 기능',goal:'상태 집계',acceptance:['검사 통과'],allowed_paths:['src/only.py'],depends_on:[]},{key:'tests',name:'검사',responsibility:'독립 검사',goal:'회귀 확인',acceptance:['입력 보존'],allowed_paths:['tests/test_only.py'],depends_on:[]}],completion_criteria:['같은 후보 검증']};
  let phase='proposal';let proposedSelection={...selection,allowed_paths:['src/rejected.py']};const registrations=[],messages=[],confirmations=[];
  await page.route(catalogPath,route=>route.fulfill({json:{entries:[entry]}}));
  await page.route(specPath,async route=>{
    if(route.request().method()==='GET'){await route.fulfill({json:{execution_specs:phase==='proposal'?[]:[saved]}});return;}
    registrations.push(route.request().postDataJSON());
    if(registrations.length===1){proposedSelection=selection;await route.fulfill({status:400,json:{error:{code:'execution_spec_invalid',message:'fixture rejected paths'}}});return;}
    if(registrations.length===2){phase='uncertain-new';proposedSelection={...selection,allowed_paths:['src/newer.py']};await route.abort('failed');return;}
    phase='registered';
    assert.deepEqual(registrations[2],registrations[1],'lost response retains original key, base version and selection');
    await route.fulfill({status:201,json:{execution_spec:saved}});
  });
  await page.route(overviewPath,async route=>{
    const response=await route.fetch(),base=await response.json();
    const plan={id:'execution-plan',digest:'e'.repeat(64),base_harness_version:1,request_revision:1,status:phase==='registered'?'stale':'proposed',mode:'fixture',content:{...content,...(['proposal','uncertain-new'].includes(phase)?{execution_spec_proposal:proposedSelection}:{})},...(['newplan','uncertain-new'].includes(phase)?{execution_spec:reference}:{})};
    await route.fulfill({response,json:{...base,project:{...base.project,source:'master',request_revision:1,...(phase!=='proposal'?{execution_spec:reference}:{})},plans:[plan],runs:[],execution_specs:phase==='proposal'?[]:[saved],pm_requests:[{request_id:'fixture-pm',request_revision:1,state:'completed',...(['newplan','uncertain-new'].includes(phase)?{execution_spec:reference}:{})}],documents:{}}});
  });
  await page.route(messagePath,async route=>{messages.push(route.request().postDataJSON());phase='newplan';await route.fulfill({json:{message:{id:'fresh-pm-request'}}});});
  await page.route(confirmPath,async route=>{confirmations.push(route.request().postDataJSON());await route.fulfill({status:409,json:{error:{code:'plan_mismatch',message:'fixture confirmation intercepted'}}});});
  try{
    await page.goto(new URL('/#manager?project='+fixtureId,page.url()).href);await page.reload();
    await page.getByRole('link',{name:'실행 범위 확인'}).click();
    const panel=page.locator('#execution');await panel.locator(':scope > summary').click();
    await panel.getByText('fixture-company/example',{exact:true}).waitFor();
    assert.equal(await page.getByRole('button',{name:'계획 검토·확정',exact:true}).count(),0);
    assert.equal(registrations.length,0);assert.equal(messages.length,0);
    for(const theme of ['light','black']){
      await selectTheme(page,theme);
      for(const width of [320,390,1440]){
        await page.setViewportSize({width,height:900});
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'execution facts reflow on mobile');
        await settledScreenshot(page,{path:`${output}/${theme}-execution-${width}.png`,fullPage:true});
      }
    }
    setHTTPFixture(true);
    await panel.getByRole('button',{name:'명세 저장',exact:true}).click();
    await panel.getByText('제안한 범위·후보·예산이 카탈로그와 맞지 않습니다. PM과 범위를 조정해주세요.',{exact:true}).waitFor();
    assert.equal(await panel.getByRole('button',{name:'저장 결과 확인',exact:true}).count(),0,'a definite rejection releases its old intent');
    await panel.getByRole('button',{name:'명세 저장',exact:true}).click();
    await panel.getByRole('button',{name:'저장 결과 확인',exact:true}).waitFor();
    await page.reload();
    await panel.locator(':scope > summary').click();
    const oldRequest=panel.getByRole('region',{name:'이전 명세 요청'});
    await oldRequest.getByText('src/only.py',{exact:true}).waitFor();
    assert.equal(await oldRequest.getByText('src/newer.py',{exact:true}).count(),0,'uncertain request displays the original submitted selection');
    await panel.getByRole('button',{name:'저장 결과 확인',exact:true}).click();
    await panel.getByRole('button',{name:'PM에게 새 계획 요청',exact:true}).waitFor();
    assert.equal(registrations.length,3);assert.equal(messages.length,0,'saving a specification never submits or confirms another plan');
    assert.equal(registrations[1].base_version,0);assert.deepEqual(registrations[1].selection,selection);assert.notEqual(registrations[0].idempotency_key,registrations[1].idempotency_key);
    await panel.getByRole('button',{name:'PM에게 새 계획 요청',exact:true}).click();
    assert.equal(messages.length,1);assert.deepEqual(messages[0],{content:'등록한 실행 명세로 역할과 완료 조건을 다시 제안해 주세요.'});
    await page.getByRole('button',{name:'계획 검토·확정',exact:true}).click();
    const dialog=page.getByRole('dialog');await dialog.getByRole('region',{name:'확정할 실행 범위'}).getByText(saved.digest,{exact:true}).waitFor();
    assert.equal(confirmations.length,0);
    await dialog.getByRole('checkbox').check();await dialog.getByRole('button',{name:'이 계획 확정',exact:true}).click();
    await dialog.locator('#plan-error').filter({hasText:'계획'}).waitFor();
    assert.equal(confirmations.length,1);assert.equal(confirmations[0].execution_spec_digest,saved.digest);assert.equal(confirmations[0].plan_digest,'e'.repeat(64));
    console.log('PASS: execution catalog scope, separate saving/request/confirmation, lost-response idempotency, exact execution digest, Light/Black and 320px reflow');
  }finally{
    setHTTPFixture(false);for(const path of [catalogPath,specPath,overviewPath,messagePath,confirmPath])await page.unroute(path);
    await page.reload();await page.locator('.nav').waitFor();await selectTheme(page,'light');await page.setViewportSize({width:1440,height:1000});
  }
};
