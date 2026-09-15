/* Render a saved result from ONE actual CLI call. The original worker failure stays
   recorded; a strict parser reprocessed the same raw output without another call.
   This scenario uses an authenticated response fixture, never runs a model, never
   writes an approval, and does not claim a production UI deployment. */
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
module.exports=async function recordedCliTranslationRendering({page,fixtureId,output}){
  const file=path.join(__dirname,'fixtures','recorded-translation-reprocessed-20260915.json');
  const recorded=JSON.parse(await fs.readFile(file,'utf8'));
  const provenance=recorded.recorded_translation_provenance;
  assert.equal(provenance.kind,'recorded_cli_reprocessing');
  assert.equal(provenance.new_model_calls,0);
  assert.equal(provenance.original_failed_status,'failed');
  assert.equal(provenance.original_failure_reason,'translation_output_not_json');
  assert.match(provenance.raw_sha256,/^[0-9a-f]{64}$/);
  const [doc]=Object.values(recorded.documents);
  const [approval]=recorded.approvals;
  assert.equal(doc.kind,'approval');
  assert.equal(doc.translation.status,'completed');
  assert.equal(doc.source_digest,doc.translation.source_digest);
  assert.equal(approval.status,'pending','the stored original approval remains pending');
  assert.equal(approval.subject_digest,doc.protected.subject_digest);
  const originalJSON=JSON.stringify({document:doc,approval});
  const caption='실제 CLI 1회 기록 · 원기록 실패 유지 · 같은 raw 재처리 · 추가 호출 없음';
  const overviewPath=`**/api/projects/${fixtureId}/overview`;
  const writes=[];
  const observeRequest=request=>{if(request.method()!=='GET'&&new URL(request.url()).pathname.startsWith('/api/'))writes.push(request.method()+' '+new URL(request.url()).pathname);};
  page.on('request',observeRequest);
  await page.route(overviewPath,async route=>{
    const response=await route.fetch(),base=await response.json();
    await route.fulfill({response,json:{...base,...recorded,
      // Only the enclosing UI workspace is mapped to the temporary HTTP fixture.
      // Original document/approval IDs, project references, text and digests stay unchanged.
      project:{...base.project,name:'저장된 실제 CLI 번역 · 화면 검증'},
      readiness:{...base.readiness,mode:'fixture',fixture_provenance:caption},
    }});
  });
  try{
    await page.reload();await page.locator('.nav').waitFor();
    await page.locator('.nav').getByRole('link',{name:'승인',exact:true}).click();
    await page.getByRole('heading',{name:doc.translation.fields.title,exact:true}).waitFor();
    await page.locator('.recorded-provenance').filter({hasText:caption}).waitFor();
    await page.getByText('모의 예시 데이터',{exact:true}).waitFor();
    // This verifies rendering of the recorded wording, not independent semantic quality.
    for(const field of ['action','impact','rollback'])await page.getByText(doc.translation.fields[field],{exact:true}).waitFor();
    assert.equal(await page.getByText(approval.environment,{exact:true}).count(),1);
    assert.equal(await page.getByText(`USD ${approval.cost_usd}`,{exact:true}).count(),1);
    assert.equal(await page.getByText(approval.artifact_sha,{exact:true}).count(),1);
    const language=page.locator(`[data-document-id="${doc.id}"]`);
    await language.getByText('원문·번역 출처 확인',{exact:true}).click();
    await language.getByText('번역 의미 검증: 독립 검증 전',{exact:true}).waitFor();
    await language.getByText('실제 CLI 저장 기록 재처리 · 원기록 failed 유지 · 추가 모델 호출 0회',{exact:true}).waitFor();
    await language.getByText(provenance.raw_sha256,{exact:true}).waitFor();
    for(const theme of ['light','black']){
      await page.locator(`[data-theme-choice="${theme}"]`).click();
      await page.setViewportSize({width:1440,height:1100});
      await page.getByRole('heading',{name:doc.translation.fields.title,exact:true}).waitFor();
      await page.evaluate(()=>document.fonts.ready);
      await page.screenshot({path:`${output}/${theme}-recorded-cli-reprocessed-korean-approval.png`,fullPage:true});
      await language.getByRole('button',{name:'원문 보기',exact:true}).click();
      await page.getByRole('heading',{name:doc.fields.title,exact:true}).waitFor();
      for(const field of ['impact','rollback'])await page.getByText(doc.fields[field],{exact:true}).waitFor();
      await page.screenshot({path:`${output}/${theme}-recorded-cli-reprocessed-original-approval.png`,fullPage:true});
      await language.getByRole('button',{name:'한국어 보기',exact:true}).click();
      await page.getByRole('heading',{name:doc.translation.fields.title,exact:true}).waitFor();
      await page.setViewportSize({width:360,height:900});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`${theme} recorded translation fits 360px`);
      await page.screenshot({path:`${output}/${theme}-mobile-recorded-cli-reprocessed-korean-approval.png`,fullPage:true});
    }
    assert.deepEqual(writes,[],'stored translation viewing and toggling never sends approval/model/worker requests');
    assert.equal(JSON.stringify({document:doc,approval}),originalJSON,'source and translation records are unchanged');
    console.log(JSON.stringify({scenario:'recorded_cli_reprocessed_translation_rendering',status:'PASS',scope:'authenticated response fixture; saved actual CLI result; no new model calls; no approval writes',original_failure_preserved:true,raw_sha256:provenance.raw_sha256,semantic_validation:doc.translation.semantic_validation}));
  }finally{
    page.off('request',observeRequest);await page.unroute(overviewPath);await page.reload();await page.locator('.nav').waitFor();await page.locator('[data-theme-choice="light"]').click();await page.setViewportSize({width:1440,height:1000});
  }
};
