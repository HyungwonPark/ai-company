/* Authenticated network-response fixtures test observation only. No model, worker,
   production approval, or translation completion is claimed by this scenario. */
const assert=require('node:assert/strict');
const {selectTheme, settledScreenshot} = require('./capture.cjs');
module.exports=async function({page,context,fixtureId,output,setNetworkFixture,setHTTPFixture}){
  const overviewPath=`**/api/projects/${fixtureId}/overview`;
  let cursor=10000,extra=[],translationState='completed',digest='d'.repeat(64),offline=false,staleOwner=false;
  let fixtureApproval;
  const translatedTitle='격리 환경만 검토 · 운영 배포는 허용하지 않음';
  const originalTitle='Review isolated environment only. Production deployment is not permitted.';
  const makeTransfer=(id,order,from,to,kind)=>({id,cursor:order,from,to,kind,task_id:'fixture-0',run_id:'fixture-run',title:`응답 fixture ${id}`,reason:'검증한 경우에만 전달합니다. <script>window.transferInjected=true</script>',status:'received',created_at:1760000000+order,artifact_refs:[{label:'검사 대상',sha:'a'.repeat(40),path:'src/example.py'}],generation:4,execution_id:'execution-4',source:{table:'fixture',id},mode:'fixture'});
  await page.route(overviewPath,async route=>{
    if(offline){await route.abort('internetdisconnected');return;}
    const response=await route.fetch(),snapshot=await response.json();
    const roles=snapshot.roles.map((role,i)=>({id:role.id,name:role.name,kind:'role',active:true,responsibility:role.responsibility,status:i===0?'WAITING_CAPACITY':'RUNNING',current_task_id:snapshot.tasks.find(t=>t.role_id===role.id)?.id,task_ids:snapshot.tasks.filter(t=>t.role_id===role.id).map(t=>t.id),wait_reason:i===0?'응답 fixture: 공유 사용량 회복 대기':null,resume_at:i===0?Date.now()/1000+600:null,handoffs:i===0?[{from:'old-session',to:'current-session',source:'fixture'}]:[],assignment:{agent_id:'fixture-agent',provider:'fixture',session_id:staleOwner?'old-session':'current-session',execution_id:'execution-4',generation:staleOwner?1:4,requested:{model:staleOwner?'old-request':'requested-model-only',reasoning_effort:'high',ultracode_enabled:false},observed:{status:'unavailable',model:null,reasoning_effort:null,backend_model_verified:false},quota_group:'same-shared-account',quota:{status:i===0?'COOLDOWN':'UNKNOWN'},harness_version:1}}));
    const special=(id,name,kind)=>({id,name,kind,active:true,status:'WAITING_PM',responsibility:'응답 fixture · 역할과 모델은 별도',task_ids:[],assignment:{requested:{model:kind==='translator'?'gpt-5.6-luna':'requested-model-only',reasoning_effort:kind==='translator'?'low':'ultra'},observed:{status:'unavailable'},session_id:`${id}-session`}});
    const nodes=[special('pm','프로젝트 매니저','pm'),...roles,special('check','통합 검사','check'),special('review','독립 검수','reviewer'),special('final','Astra 최종 검수','final'),special('translator','한국어 번역','translator')];
    const past=[makeTransfer('past-spec',9998,'pm',roles[0].id,'specification'),makeTransfer('past-repair',9999,'review',roles[0].id,'revision_return'),makeTransfer('past-translation',10000,'final','translator','translation_request')];
    snapshot.collaboration={cursor,source:'fixture',nodes,transfers:[...past,...extra.map(item=>makeTransfer(item.id,item.cursor,roles[0].id,'review',item.kind||'review_request'))]};
    snapshot.translation_summary={status:'fixture',candidate_model:'gpt-5.6-luna',reason:'사람이 작성한 응답 fixture / 실제 모델 호출 아님',counts:{completed:2,pending:1}};
    const approval=snapshot.approvals[0];fixtureApproval=approval;
    Object.assign(approval,{title:originalTitle,impact:'Only if the checks pass. Do not exceed USD 30. Keep pending otherwise.',rollback:'Restore the previous image; do not roll back the database.'});
    const report=snapshot.reports[0];report.title='Fixture source report';report.summary='Do not deploy. Keep pending unless checks pass. Path: src/example.py.';
    const doc=(id,fields,korean,protectedFields={})=>({id,kind:id.split(':')[0],project_id:fixtureId,source_version:2,source_digest:digest,original_language:'en',author_role:'fixture-author',source_ref:{table:'fixture',id},fields,protected:protectedFields,translation:{id:'translation-fixture-'+id,status:translationState,fields:korean,model:'fixture-human-authored',source_digest:'d'.repeat(64),version:1,requested_configuration:{model:'gpt-5.6-luna',reasoning_effort:'low'},observed_configuration:null,reason:'UI 응답 fixture · 실제 모델 번역 아님'}});
    snapshot.documents={
      [`approval:${approval.id}`]:doc(`approval:${approval.id}`,{title:approval.title,action:approval.action,impact:approval.impact,rollback:approval.rollback},{title:translatedTitle,action:'예시 기록만 검토',impact:'검사를 통과한 경우에만 허용합니다. USD 30을 넘기지 마세요. 그 외에는 pending을 유지합니다.',rollback:'이전 이미지만 복원합니다. 데이터베이스는 되돌리지 않습니다.',environment:'TRANSLATION MUST NOT REPLACE ENVIRONMENT',cost_usd:'999'}, {...approval}),
      [`report:${report.id}`]:doc(`report:${report.id}`,{title:report.title,summary:report.summary},{title:'번역된 예시 보고서',summary:'배포하지 마세요. 검사를 통과하지 않았다면 pending을 유지합니다. 경로: src/example.py. <script>window.translationInjected=true</script>'})
    };
    await route.fulfill({response,json:snapshot});
  });
  const refresh=async()=>{const response=page.waitForResponse(r=>r.url().endsWith(`/api/projects/${fixtureId}/overview`));await page.getByRole('button',{name:'새로고침'}).click();await response;};
  try{
    await page.reload();
    await page.locator('.nav').getByRole('link',{name:'진행',exact:true}).click();
    await page.getByText('네트워크 응답 fixture · 실제 협업 실행을 뜻하지 않습니다.',{exact:true}).waitFor();
    assert.equal(await page.locator('.nav a').count(),4,'four primary menus');
    assert.equal(await page.locator('.collab-node').count(),9,'dynamic roles plus PM/check/review/final/translator');
    assert.equal(await page.locator('.transfer-row').count(),3);
    assert.equal(await page.locator('.fresh-fact').count(),0,'initial persisted events are not animated');
    assert.ok(await page.locator('.collaboration-wire').count()>0,'desktop shows real directed edges');
    await page.locator('.transfer-row[data-collaboration-transfer="past-repair"]').focus();
    await page.keyboard.press('Enter');
    await page.getByRole('complementary',{name:'선택한 협업 상세'}).getByRole('heading',{name:'수정 반환',exact:true}).waitFor();
    assert.equal(await page.locator('.transfer-row[data-collaboration-transfer="past-repair"]').getAttribute('aria-pressed'),'true');
    assert.equal(await page.evaluate(()=>Boolean(window.transferInjected)),false);
    cursor=10001;extra=[{id:'new-request',cursor}];
    await refresh();
    await page.locator('.transfer-row[data-collaboration-transfer="new-request"]').waitFor();
    assert.ok(await page.locator('.fresh-fact').count()>0,'only a newly observed cursor receives finite emphasis');
    await page.waitForTimeout(2100);
    assert.equal(await page.locator('.fresh-fact').count(),0,'event emphasis ends');
    extra.push({id:'new-request',cursor},{id:'late-fact',cursor:9000,kind:'result_report'});
    await refresh();
    await page.locator('.transfer-row[data-collaboration-transfer="late-fact"]').waitFor();
    assert.equal(await page.locator('.transfer-row').count(),5,'duplicate stable ID is deduplicated');
    assert.equal(await page.locator('.fresh-fact').count(),0,'same cursor and late fact are not new animations');
    assert.equal(await page.locator('.transfer-row[data-collaboration-transfer="past-repair"]').getAttribute('aria-pressed'),'true','selection survives polling');
    const firstRole=page.locator('.collab-node[data-node-id]').filter({has:page.locator('.collab-kind',{hasText:'프로젝트 역할'})}).first();
    await firstRole.getByText('요청 모델 · requested-model-only',{exact:true}).waitFor();
    await firstRole.getByText('요청 추론 · high / 적용 미확인',{exact:true}).waitFor();
    assert.equal(await firstRole.getByText(/^관측 모델/).count(),0,'requested-only configuration is not rendered as observed');
    await firstRole.locator('[data-collaboration-node]').click();
    await page.getByRole('complementary',{name:'선택한 협업 상세'}).getByText('current-session',{exact:true}).waitFor();
    cursor=9990;staleOwner=true;await refresh();
    await page.getByText('이전 순서의 응답을 받았습니다. 마지막으로 확인한 상태를 유지합니다.',{exact:false}).waitFor();
    assert.equal(await page.getByText('요청 모델 · old-request',{exact:true}).count(),0,'older snapshot cannot revert owner');
    cursor=10001;staleOwner=false;await refresh();
    await page.getByRole('complementary',{name:'선택한 협업 상세'}).getByText('current-session',{exact:true}).waitFor();
    setNetworkFixture(true);offline=true;await context.setOffline(true);
    await page.locator('#connection').filter({hasText:'연결 끊김'}).waitFor();
    cursor=10002;extra.push({id:'during-disconnect',cursor});offline=false;await context.setOffline(false);
    await page.locator('.transfer-row[data-collaboration-transfer="during-disconnect"]').waitFor();
    assert.equal(await page.locator('.fresh-fact').count(),0,'reconnection baselines historical arrivals');
    setNetworkFixture(false);
    await page.locator('[data-collaboration-clear]').click();
    for(const theme of ['light','black']){
      await selectTheme(page, theme);
      await page.setViewportSize({width:1440,height:1000});
      await settledScreenshot(page, {path:`${output}/${theme}-desktop-collaboration-fixture.png`,fullPage:true});
      await page.locator('.transfer-row[data-collaboration-transfer="past-repair"]').click();
      const transferDetail=page.getByRole('complementary',{name:'선택한 협업 상세'});
      await transferDetail.getByRole('heading',{name:'수정 반환',exact:true}).waitFor();
      await transferDetail.screenshot({path:`${output}/${theme}-desktop-transfer-detail-fixture.png`});
      await page.locator('[data-collaboration-clear]').click();
      await page.locator('.progress-tabs').getByRole('link',{name:'보고서',exact:true}).click();
      await page.getByText('번역된 예시 보고서',{exact:true}).and(page.locator('.document-title')).waitFor();
      assert.equal(await page.evaluate(()=>Boolean(window.translationInjected)),false);
      await settledScreenshot(page, {path:`${output}/${theme}-desktop-korean-report-fixture.png`,fullPage:true});
      await page.locator('.document-toggle').first().click();
      await page.getByText('Fixture source report',{exact:true}).and(page.locator('.document-title')).waitFor();
      await page.locator('.document-toggle').first().click();
      await page.locator('.nav').getByRole('link',{name:'승인',exact:true}).click();
      await page.getByText(translatedTitle,{exact:true}).and(page.locator('.document-title')).waitFor();
      assert.equal(await page.getByText('TRANSLATION MUST NOT REPLACE ENVIRONMENT',{exact:true}).count(),0);
      assert.equal(await page.getByText(fixtureApproval.environment,{exact:true}).count(),1,'structured environment is original');
      assert.equal(await page.getByText(`USD ${fixtureApproval.cost_usd}`,{exact:true}).count(),1,'structured amount is original');
      for(const width of [360,320]){
        await page.setViewportSize({width,height:850});
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`${theme} approval fits ${width}px`);
      }
      await settledScreenshot(page, {path:`${output}/${theme}-mobile-korean-approval-fixture.png`,fullPage:true});
      await page.locator('.nav').getByRole('link',{name:'진행',exact:true}).click();
      await page.locator('.collaboration').waitFor();
      assert.equal(await page.locator('.collaboration-wires').isVisible(),false,'mobile uses readable directed rows, not scaled diagram');
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`${theme} collaboration fits 320px`);
      await settledScreenshot(page, {path:`${output}/${theme}-mobile-collaboration-fixture.png`,fullPage:true});
    }
    await page.emulateMedia({reducedMotion:'reduce'});
    cursor=10003;extra.push({id:'reduced-motion-event',cursor});await refresh();
    await page.locator('.transfer-row[data-collaboration-transfer="reduced-motion-event"]').waitFor();
    assert.equal(await page.locator('animateMotion').count(),0,'reduced motion uses static emphasis');
    await page.locator('.nav').getByRole('link',{name:'승인',exact:true}).click();
    await page.getByRole('button',{name:'승인 검토'}).first().click();
    assert.equal(await page.locator('#decision-form').getAttribute('data-translation-id'),'translation-fixture-approval:'+fixtureApproval.id);
    await page.getByLabel('검토 의견 (선택)').fill('네트워크 fixture: 승인 실행 없이 참조 전달만 확인');
    let decisionBody;
    const decisionPath=`**/api/projects/${fixtureId}/approvals/${fixtureApproval.id}/decisions`;
    await page.route(decisionPath,async route=>{decisionBody=route.request().postDataJSON();await route.fulfill({status:409,contentType:'application/json',json:{error:{code:'ui_fixture_only',message:'응답 fixture: 실제 승인 저장 안 함'}}});});
    setHTTPFixture(true);
    await page.getByRole('button',{name:'승인 기록',exact:true}).click();
    await page.locator('#decision-error').filter({hasText:'응답 fixture: 실제 승인 저장 안 함'}).waitFor();
    setHTTPFixture(false);
    assert.deepEqual(decisionBody.displayed_translation,{id:'translation-fixture-approval:'+fixtureApproval.id,source_digest:'d'.repeat(64)});
    assert.equal(decisionBody.subject_digest,fixtureApproval.subject_digest,'translation does not replace original approval binding');
    await page.unroute(decisionPath);await page.getByRole('button',{name:'취소',exact:true}).click();
    translationState='failed';await page.locator('.nav').getByRole('link',{name:'진행',exact:true}).click();await refresh();
    await page.locator('.nav').getByRole('link',{name:'승인',exact:true}).click();await page.getByText(originalTitle,{exact:true}).and(page.locator('.document-title')).waitFor();await page.getByText('번역 실패',{exact:true}).first().waitFor();
    translationState='completed';digest='e'.repeat(64);await page.locator('.nav').getByRole('link',{name:'진행',exact:true}).click();await refresh();
    await page.locator('.nav').getByRole('link',{name:'승인',exact:true}).click();await page.getByText(originalTitle,{exact:true}).and(page.locator('.document-title')).waitFor();await page.getByText('원문 갱신 · 이전 번역',{exact:true}).waitFor();
  }finally{
    setNetworkFixture(false);setHTTPFixture(false);await context.setOffline(false);await page.emulateMedia({reducedMotion:'no-preference'});await page.unroute(overviewPath);await page.reload();await page.locator('.nav').waitFor();await selectTheme(page, 'light');await page.setViewportSize({width:1440,height:1000});
  }
};
