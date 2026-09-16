/* Authenticated response fixtures. Confirmation is intercepted, never executed. */
const assert=require('node:assert/strict');
const {selectTheme,settledScreenshot}=require('./capture.cjs');
module.exports=async function({page,fixtureId,output,setHTTPFixture}){
  const path=`**/api/projects/${fixtureId}/overview`,confirm=`**/api/projects/${fixtureId}/plans/reading-plan/confirm`;
  const source='d'.repeat(64),planDigest='e'.repeat(64),translation='a'.repeat(32);
  const body={summary:'Build a bounded feature',roles:[{key:'implementation',name:'Implementation',responsibility:'Implement safely',goal:'Preserve existing data',acceptance:['Do not deploy'],allowed_paths:['src/only.py'],depends_on:[]},{key:'tests',name:'Tests',responsibility:'Inspect independently',goal:'Verify the same candidate',acceptance:['Inspect failure cases'],allowed_paths:['tests/test_only.py'],depends_on:[]}],completion_criteria:['No merge without approval']};
  const originals={summary:body.summary,'role:0:name':'Implementation','role:0:responsibility':'Implement safely','role:0:goal':'Preserve existing data','role:0:acceptance:0':'Do not deploy','role:1:name':'Tests','role:1:responsibility':'Inspect independently','role:1:goal':'Verify the same candidate','role:1:acceptance:0':'Inspect failure cases','completion:0':'No merge without approval'};
  const korean={summary:'작은 기능 구현','role:0:name':'개발','role:0:responsibility':'안전하게 구현','role:0:goal':'기존 데이터 보존','role:0:acceptance:0':'배포하지 않음','role:1:name':'검사','role:1:responsibility':'독립적으로 검사','role:1:goal':'같은 후보 검증','role:1:acceptance:0':'실패 사례 검사','completion:0':'승인 없이 병합하지 않음'};
  const submitted=[];
  await page.route(path,async route=>{
    const response=await route.fetch(),base=await response.json();
    const plan={id:'reading-plan',digest:planDigest,request_revision:1,status:'proposed',base_harness_version:1,mode:'fixture',content:body};
    const doc={id:'plan:reading-plan',project_id:fixtureId,source_digest:source,source_version:planDigest,author_role:'pm',fields:originals,protected:{content:body,plan_digest:planDigest},translation:{id:translation,status:'completed',source_digest:source,fields:korean}};
    await route.fulfill({response,json:{...base,project:{...base.project,source:'master',request_revision:1},plans:[plan],runs:[],documents:{[doc.id]:doc},project_report:{source:'system',project_id:fixtureId,goal:'읽기용 한국어와 원문 보존',plan_id:plan.id,plan_digest:planDigest,run_id:'fixture-run',candidate_sha:null,digest:'f'.repeat(64),completed:[{title:'검사',status:'CONTRIBUTION_READY'}],in_progress:[{title:'개발',status:'WAITING_QUOTA'}],blockers:[{title:'개발',status:'WAITING_QUOTA',reason:'공유 한도 · 모의 주입',resume_at:2000000000}],next_actions:[{owner:'coordinator',text:'예약 시각에 재개합니다.',view:'progress'}],decisions:[{kind:'plan_confirmed',at:1000,delegated:true}],completion_criteria:body.completion_criteria,pm_report_ids:[]}}});
  });
  await page.route(confirm,async route=>{submitted.push(route.request().postDataJSON());await route.fulfill({status:409,contentType:'application/json',json:{error:{code:'translation_mismatch',message:'fixture: original changed'}}});});
  try{
    await page.locator('.nav').getByRole('link',{name:'매니저',exact:true}).click();await page.reload();
    await page.getByRole('button',{name:'계획 검토·확정',exact:true}).click();
    const dialog=page.getByRole('dialog'),reading=dialog.getByRole('region',{name:'계획 내용'});
    for(const scope of await reading.locator('.plan-scope>summary').all())await scope.click();
    await reading.getByText('역할 ID · implementation',{exact:true}).waitFor();
    for(const text of ['안전하게 구현','기존 데이터 보존','배포하지 않음','같은 후보 검증','승인 없이 병합하지 않음','src/only.py','tests/test_only.py'])await reading.getByText(text,{exact:true}).waitFor();
    assert.equal(await dialog.locator('#plan-form').getAttribute('data-digest'),planDigest);
    await dialog.getByRole('checkbox').check();
    await reading.getByRole('button',{name:'원문 보기',exact:true}).click();
    await reading.getByText('Implement safely',{exact:true}).waitFor();
    assert.equal(await dialog.getByRole('checkbox').isChecked(),false,'switching reading language asks for another deliberate review');
    assert.equal(await dialog.locator('#plan-form').getAttribute('data-translation-id'),'');
    await reading.getByRole('button',{name:'한국어 보기',exact:true}).click();
    await reading.getByText('안전하게 구현',{exact:true}).waitFor();
    for(const theme of ['light','black']){
      // Theme is changed before reopening so the modal never receives a background click.
      await dialog.getByRole('button',{name:'대화상자 닫기',exact:true}).click();await selectTheme(page,theme);
      await page.getByRole('button',{name:'계획 검토·확정',exact:true}).click();await page.setViewportSize({width:360,height:800});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
      await settledScreenshot(page,{path:`${output}/${theme}-plan-reading.png`});
    }
    setHTTPFixture(true);
    await dialog.getByRole('checkbox').check();await dialog.getByRole('button',{name:'이 계획 확정',exact:true}).click();
    await dialog.getByText('표시한 번역과 승인 원문의 연결이 달라졌습니다. 최신 원문과 번역을 다시 확인해주세요.',{exact:true}).waitFor();
    setHTTPFixture(false);
    assert.equal(submitted.length,1);
    const request=submitted[0];assert.equal(request.plan_digest,planDigest);assert.equal(request.base_harness_version,1);
    assert.deepEqual(request.displayed_translation,{id:translation,source_digest:source});
    assert.deepEqual(Object.keys(request).sort(),['base_harness_version','displayed_translation','idempotency_key','plan_digest']);
    assert.equal(await dialog.getByRole('button',{name:'이 계획 확정',exact:true}).isDisabled(),true);
    await dialog.getByRole('button',{name:'대화상자 닫기',exact:true}).click();
    await page.locator('.nav').getByRole('link',{name:'진행',exact:true}).click();await page.locator('.progress-tabs').getByRole('link',{name:'보고서',exact:true}).click();
    const report=page.getByRole('region',{name:'프로젝트 종합 보고'});
    await report.getByText('공유 한도 · 모의 주입',{exact:true}).waitFor();
    await report.getByText('위임 검증 · 실제 UI 확인과 구분',{exact:true}).waitFor();
    await page.getByRole('region',{name:'PM 작성 보고'}).getByText('PM이 작성한 종합 보고는 아직 없습니다.',{exact:true}).waitFor();
    for(const theme of ['light','black']){await selectTheme(page,theme);await settledScreenshot(page,{path:`${output}/${theme}-project-report.png`,fullPage:true});}
    console.log('PASS: complete Korean plan reading, original toggle, unchanged authority, translation-bound confirmation, system report and PM distinction');
  }finally{setHTTPFixture(false);await page.unroute(path);await page.unroute(confirm);await page.reload();await page.locator('.nav').waitFor();await selectTheme(page,'light');await page.setViewportSize({width:1440,height:1000});}
};
