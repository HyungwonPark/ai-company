/* Authenticated response fixtures; visual/navigation checks never confirm a plan. */
const assert=require('node:assert/strict');
const {selectTheme,settledScreenshot}=require('./capture.cjs');
module.exports=async function managerChecks({page,fixtureId,output}){
  const overviewPath=`**/api/projects/${fixtureId}/overview`;
  const original='Plan this exact bounded goal with two independent roles. Implementation owns only src/ai_company/pilot_status.py. Tests own only tests/test_pilot_status.py. Do not deploy or merge.';
  let translated=true, revising=false, friendly=false;
  const writes=[];
  const observe=request=>{if(request.method()!=='GET'&&new URL(request.url()).pathname.startsWith('/api/'))writes.push(request.url());};
  page.on('request',observe);
  await page.route(overviewPath,async route=>{
    const response=await route.fetch(),base=await response.json();
    const roles=[{key:'implementation',name:'개발',responsibility:'상태를 집계하는 함수 구현',goal:'역할 상태 집계',acceptance:['원본 상태를 바꾸지 않음'],allowed_paths:['src/ai_company/pilot_status.py'],depends_on:[]},{key:'tests',name:'테스트',responsibility:'개발과 독립된 회귀 검사',goal:'상태 집계 검증',acceptance:['빈 입력·알 수 없는 상태 확인'],allowed_paths:['tests/test_pilot_status.py'],depends_on:[]}];
    const plan={id:'manager-preview',digest:'e'.repeat(64),request_revision:1,status:'proposed',mode:'fixture',base_harness_version:1,content:{summary:original,roles,completion_criteria:['같은 후보 커밋의 검사와 독립 검수 통과','배포·병합 없이 결과 보고']}};
    const doc={id:'plan:'+plan.id,project_id:fixtureId,source_digest:'source-preview',source_version:1,fields:{summary:original},translation:translated?{id:'translation-preview',source_digest:'source-preview',status:'completed',fields:{summary:'역할별 작업 상태를 한눈에 모아보는 기능'},model:'화면 검증용 번역 예시'}:{status:'waiting_quota',source_digest:'source-preview'}};
    await route.fulfill({response,json:{...base,project:{...base.project,name:'AI Company · 화면 검증',request_revision:revising?2:1},readiness:{...base.readiness,mode:'fixture'},plans:[plan],runs:[],pm_requests:[{request_revision:revising?2:1,state:revising?'pending':'completed',requested_configuration:{provider:'codex',model:'gpt-6-astra',reasoning_effort:'ultra'}}],messages:[{id:'master-preview',role:'user',content:friendly?'역할별로 누가 일하고 있고, 무엇을 기다리는지 한눈에 보고 싶어요.':original,created_at:1789447440},{id:'pm-preview',role:'assistant',content:friendly?'개발과 검사, 두 역할로 시작해 볼까요? 서로 기다리지 않고 진행할 수 있어요. 팀에서 각 역할의 책임을 확인하고 조정해 주세요.':original,created_at:1789447560}],roles:[],documents:{[doc.id]:doc},reports:[{id:'preview-report',title:'검사 기록',summary:'화면 검증용 보고서',source:'fixture'}],approvals:[{id:'preview-approval',status:'pending',title:'후보 승인 · 화면 검증용',artifact_sha:'a'.repeat(40)}]}});
  });
  try{
    await page.locator('.nav').getByRole('link',{name:'매니저',exact:true}).click();
    await page.reload();
    await page.getByRole('heading',{name:'매니저',exact:true}).waitFor();
    await page.locator('.manager-goal').getByText('역할별 작업 상태를 한눈에 모아보는 기능',{exact:true}).waitFor();
    assert.equal(await page.locator('.manager-agent').count(),2);
    assert.equal(await page.locator('.manager-stage-mark svg').count(),2,'no execution stage is marked before confirmation');
    assert.equal(await page.locator('.manager-history[open]').count(),0,'long source history is collapsed initially');
    assert.equal(await page.locator('.message-content').filter({hasText:original}).first().isVisible(),false);
    for(const theme of ['light','black']){
      await selectTheme(page,theme);
      for(const [device,width,height] of [['mobile',360,800],['small-mobile',320,740],['desktop',1440,1000]]){
        await page.setViewportSize({width,height});await page.evaluate(()=>window.scrollTo(0,0));
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`${theme} manager fits ${width}px`);
        if(width<=360)assert.equal(await page.locator('.manager-next button').evaluate(button=>button.getBoundingClientRect().bottom<=document.querySelector('.nav').getBoundingClientRect().top),true,'the primary action fits above mobile navigation on the first screen');
        await settledScreenshot(page,{path:`${output}/${theme}-${device}-manager-simple.png`,fullPage:true});
        if(device==='mobile'){
          await settledScreenshot(page,{path:`${output}/${theme}-mobile-manager-first-screen.png`});
          await page.getByRole('button',{name:'팀 2',exact:true}).click();
          assert.equal(await page.locator('.manager-team').evaluate(panel=>panel.getBoundingClientRect().top<120),true,'switching to team brings the selected content into view');
          await settledScreenshot(page,{path:`${output}/${theme}-mobile-manager-team.png`});
          await page.getByRole('button',{name:'대화',exact:true}).click();
        }
      }
    }
    await page.setViewportSize({width:360,height:800});
    await page.locator('[data-persist-key="manager-history"]>summary').click();
    assert.equal(await page.locator('.message-content').filter({hasText:original}).first().innerText(),original);
    await page.reload();await page.getByRole('heading',{name:'매니저',exact:true}).waitFor();
    assert.equal(await page.locator('.manager-history[open]').count(),0,'reload starts with the readable overview');
    await page.getByRole('button',{name:'역할 추천',exact:true}).click();
    assert.equal(await page.getByLabel('PM에게 전달할 내용').inputValue(),'이 목표에 필요한 역할과 각 역할의 책임을 추천해주세요.');
    await page.getByLabel('PM에게 전달할 내용').fill('입력한 목표는 테마 전환에도 유지합니다.');
    await page.getByRole('button',{name:'팀 2',exact:true}).click();
    assert.equal(await page.getByRole('region',{name:'PM과 대화'}).isVisible(),false);
    await page.getByRole('button',{name:'개발 역할 조정',exact:true}).click();
    assert.equal(await page.getByLabel('PM에게 전달할 내용').inputValue(),'입력한 목표는 테마 전환에도 유지합니다.\n개발 역할의 책임과 완료 조건을 함께 조정하고 싶어요.');
    await page.getByLabel('PM에게 전달할 내용').fill('입력한 목표는 테마 전환에도 유지합니다.');
    await selectTheme(page,'light');
    assert.equal(await page.getByLabel('PM에게 전달할 내용').inputValue(),'입력한 목표는 테마 전환에도 유지합니다.');
    await page.getByLabel('PM에게 전달할 내용').clear();
    revising=true;await page.reload();await page.getByRole('heading',{name:'매니저',exact:true}).waitFor();
    assert.equal(await page.getByRole('button',{name:'계획 검토·확정',exact:true}).count(),0,'an earlier team cannot be confirmed while a change request is pending');
    await page.getByRole('button',{name:'팀 2',exact:true}).click();
    await page.getByText('이전 제안 · 조정 중',{exact:true}).waitFor();
    assert.equal(await page.locator('.manager-agent').count(),2,'previous roles remain readable while PM revises them');
    revising=false;translated=false;await page.reload();await page.getByRole('heading',{name:'매니저',exact:true}).waitFor();
    assert.equal(await page.locator('.manager-goal').innerText(),'2개 역할 · 완료 조건 2개','translation waiting shows actual structured facts, not English prose');
    assert.equal(await page.getByRole('link',{name:/승인.*검토를/}).count(),1,'approval navigation remains available during translation wait');
    await page.locator('.manager-plan-detail>summary').click();
    await page.getByText('번역 사용량 대기',{exact:true}).waitFor();
    friendly=true;translated=true;await page.reload();await page.getByRole('heading',{name:'매니저',exact:true}).waitFor();
    for(const theme of ['light','black']){
      await selectTheme(page,theme);
      for(const [device,width,height] of [['desktop',1440,1000],['mobile',360,800]]){
        await page.setViewportSize({width,height});await page.evaluate(()=>window.scrollTo(0,0));
        await settledScreenshot(page,{path:`${output}/${theme}-${device}-pm-workspace.png`,fullPage:true});
        await page.getByRole('button',{name:'새 프로젝트',exact:true}).click();
        await page.getByLabel('이름',{exact:true}).fill('우리 팀 작업실');
        await page.getByLabel('목표',{exact:true}).fill('팀에서 누가 무엇을 하고 있는지 한눈에 보고 싶어요.');
        await settledScreenshot(page,{path:`${output}/${theme}-${device}-new-project.png`});
        await page.getByRole('button',{name:'대화상자 닫기',exact:true}).click();
      }
    }
    assert.deepEqual(writes,[],'reading, themes and expanding source records send no execution/approval requests');
    console.log('PASS: simplified manager, two themes, 320/360px, collapsed exact originals, preserved draft, translation wait and zero writes');
  }finally{
    page.off('request',observe);await page.unroute(overviewPath);await page.reload();await page.locator('.nav').waitFor();await selectTheme(page,'light');await page.setViewportSize({width:1440,height:1000});
  }
};
