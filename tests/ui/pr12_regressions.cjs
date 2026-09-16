/* PR12 independent browser failures. Every API response is synthetic and
   intercepted; this test never contacts a server or confirms a real plan. */
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const {selectTheme, settledScreenshot} = require('./capture.cjs');
const web = path.resolve(__dirname, '../../src/ai_company/web');
const output = process.env.UI_OUTPUT || '/tmp/ai-company-pr12-independent';
const origin = 'http://127.0.0.1:47991'; // Fulfilled in-process; nothing listens here.
const A = 'a'.repeat(32), B = 'b'.repeat(32), CREATED = 'c'.repeat(32);
const projects = [
  {id:A,name:'가족 일정 모의 프로젝트',goal:'긴 한국어 목표로 우리 가족이 서로의 일정을 확인합니다.',status:'PLANNING',created_at:1000,pending_approval_count:1},
  {id:B,name:'독서 모의 프로젝트',goal:'독서 내용을 정리합니다.',status:'PLANNING',created_at:2000,pending_approval_count:0},
];
let scenario='planned';
function overview(project) {
  const roleContent = ['개발','검사'].map((name,index)=>({key:'role'+index,name,responsibility:name+'의 독립적인 책임을 확인합니다.',
    goal:'허용된 범위의 산출물',acceptance:['정해진 완료 조건을 검증합니다.'],allowed_paths:['fixture/'+index],depends_on:[]}));
  const plan={id:'fixture-plan',digest:'d'.repeat(64),request_revision:1,status:scenario==='running'?'confirmed':'proposed',
    mode:'fixture',base_harness_version:1,content:{summary:'가족 일정 확인 기능을 준비합니다.',roles:roleContent,completion_criteria:['같은 후보의 검사와 독립 검수']}};
  const tasks=scenario==='running'?roleContent.map((r,index)=>({id:'task'+index,role_id:'saved'+index,status:index?'WAITING_QUOTA':'RUNNING',
    stage:'developer',title:r.name+' 작업',wait_reason:index?'같은 계정 사용량 회복 대기':null,resume_at:index?2100:null})):[];
  const roles=tasks.map((t,index)=>({id:t.role_id,key:'role'+index,plan_id:plan.id,name:roleContent[index].name,
    responsibility:roleContent[index].responsibility,status:t.status,current_task_id:t.id,wait_reason:t.wait_reason}));
  return {project:{...project,source:'fixture',harness_version:1,request_revision:1,harness_content:'모의 하네스'},
    roles,tasks,plans:project.id===A?[plan]:[],runs:scenario==='running'?[{id:'fixture-run',plan_id:plan.id,plan_digest:plan.digest,state:'running',mode:'fixture',
      roles:{role0:{task_id:'task0',status:'RUNNING'},role1:{task_id:'task1',status:'WAITING_QUOTA'}}}]:[],
    pm_requests:[{request_revision:1,state:project.id===CREATED?'pending':'completed',updated_at:2000,
      requested_configuration:{provider:'codex',model:'gpt-6-astra',reasoning_effort:'ultra'}}],
    messages:[{id:'message-'+project.id,role:'user',content:project.goal,created_at:1000}],
    approvals:project.id===A?[{id:'approval-a',project_id:A,status:'pending',title:'이 프로젝트만의 승인 A',action:'모의 후보 검토',
      artifact_sha:'e'.repeat(40),subject_digest:'f'.repeat(64),environment:'fixture',cost_usd:12.5,expires_at:2000000000,
      impact:'원문 영향 조건',rollback:'원문 복구 조건',verification:'모의 검사만'}]:[],reports:[],documents:{},harnesses:[],delegations:[],
    collaboration:{source:'fixture',cursor:1,nodes:roles.map((r,index)=>({id:r.id,name:r.name,kind:'role',status:r.status,responsibility:r.responsibility,
      current_task_id:tasks[index].id,task_ids:[tasks[index].id],wait_reason:r.wait_reason,assignment:{requested:{model:'requested-long-model-name',reasoning_effort:'high'},
      observed:index?{status:'unavailable'}:{status:'observed',model:'observed-model',reasoning_effort:null,backend_model_verified:false}}})),transfers:[]},
    readiness:{mode:'fixture',pm:'plan_proposed'},translation_summary:{status:'blocked',counts:{},requested_configuration:{model:'gpt-5.6-luna'}}};
}

(async()=>{
  await fs.mkdir(output,{recursive:true});
  const browser=await chromium.launch({headless:true,chromiumSandbox:true,
    ...(process.env.CHROME_CHANNEL?{channel:process.env.CHROME_CHANNEL}:{}),
    ...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
  const context=await browser.newContext({viewport:{width:1440,height:1000},serviceWorkers:'block'});
  const page=await context.newPage();
  page.setDefaultTimeout(6000);
  const failures=[],writes=[],requests=[],creationReceipts=new Map();
  let loseCreationResponse=true,failOverview=false;
  page.on('pageerror',error=>failures.push(error.message));
  await context.route('**/*',async route=>{
    const request=route.request(),url=new URL(request.url());
    assert.equal(url.origin,origin,'no network destination outside the synthetic origin');
    requests.push({method:request.method(),path:url.pathname});
    const json=(value,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
    if(url.pathname==='/api/session')return json({authenticated:true,username:'fixture-user',login_method:'password',csrf_token:'synthetic-csrf'});
    if(url.pathname==='/api/projects'&&request.method()==='GET')return json({projects:projects.map(project=>({...project,
      recent_run:scenario==='running'&&project.id===A?{id:'fixture-run',state:'running',created_at:2200,mode:'fixture'}:null,
      recent_pm_request:{id:'request-'+project.id,state:project.id===CREATED?'pending':'completed',created_at:2000,mode:'fixture'}}))});
    if(url.pathname==='/api/projects'&&request.method()==='POST'){
      const body=request.postDataJSON();writes.push(body);
      assert.match(body.idempotency_key,/^[A-Za-z0-9_.:-]{8,128}$/);
      if(!creationReceipts.has(body.idempotency_key)){
        const project={id:CREATED,name:body.name,goal:body.goal,status:'PLANNING',created_at:3000,pending_approval_count:0};
        projects.push(project);creationReceipts.set(body.idempotency_key,{project,first_pm_requests:1});
      }
      if(loseCreationResponse){loseCreationResponse=false;return route.abort('connectionreset');}
      return json(creationReceipts.get(body.idempotency_key));
    }
    const match=url.pathname.match(/^\/api\/projects\/([^/]+)\/overview$/);
    if(match){
      if(failOverview)return route.abort('internetdisconnected');
      const project=projects.find(p=>p.id===match[1]);
      return project?json(overview(project)):json({error:{code:'not_found',message:'Unknown project'}},404);
    }
    if(url.pathname.startsWith('/api/'))throw new Error('Unexpected API operation: '+request.method()+' '+url.pathname);
    const file=path.resolve(web,'.'+(url.pathname==='/'?'/index.html':url.pathname));
    assert.ok(file.startsWith(web+path.sep));
    const types={'.html':'text/html','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml','.webmanifest':'application/manifest+json','.woff2':'font/woff2'};
    try{return route.fulfill({body:await fs.readFile(file),contentType:types[path.extname(file)]||'application/octet-stream'});}
    catch(error){if(error.code==='ENOENT')return route.fulfill({status:404,body:''});throw error;}
  });
  async function navigate(hash){await page.goto(origin+'/'+hash);await page.locator('.nav').waitFor();}
  async function noOverflow(label){
    const measurement=await page.evaluate(()=>({viewport:innerWidth,width:document.documentElement.scrollWidth,
      overflowing:[...document.querySelectorAll('body *')].map(element=>({element:element.tagName+'.'+element.className,
        right:element.getBoundingClientRect().right})).filter(item=>item.right>innerWidth+1).slice(0,6)}));
    assert.ok(measurement.width<=measurement.viewport,label+' '+JSON.stringify(measurement));
  }
  try {
    await navigate('');
    await page.getByRole('heading',{name:'프로젝트',exact:true}).waitFor();
    assert.equal(new URL(page.url()).hash,'#projects');
    assert.equal(await page.locator('#project-select').inputValue(),'');
    assert.deepEqual(await page.locator('.nav a').allTextContents(),['매니저','진행','승인','기록']);
    assert.equal(await page.locator('.project-list-card').count(),2);
    await page.getByLabel('찾기',{exact:true}).fill('독서');
    assert.equal(await page.locator('.project-list-card').count(),1);
    await page.getByLabel('찾기',{exact:true}).fill('존재하지않는검색');
    await page.getByRole('heading',{name:'검색 결과 없음'}).waitFor();
    await page.getByRole('button',{name:'검색 지우기'}).click();
    await page.getByRole('link',{name:'가족 일정 모의 프로젝트 매니저 열기'}).click();
    await page.getByRole('heading',{name:'매니저',exact:true}).waitFor();
    assert.equal(await page.locator('#project-select').inputValue(),A);
    await page.locator('.nav').getByRole('link',{name:'승인',exact:true}).click();
    await page.getByText('이 프로젝트만의 승인 A',{exact:true}).waitFor();
    await page.evaluate(()=>{location.hash='#approvals?project=missing-project';});
    await page.getByRole('heading',{name:'프로젝트를 열 수 없습니다'}).waitFor();
    assert.equal(new URL(page.url()).hash,'#approvals?project=missing-project');
    assert.equal(await page.getByText('이 프로젝트만의 승인 A',{exact:true}).count(),0);
    assert.equal(await page.locator('[data-decision]').count(),0);
    assert.equal(await page.locator('#project-select').inputValue(),'');
    await navigate('#project?project='+B);
    await page.getByLabel('하네스 초안').waitFor({state:'attached'});
    assert.equal(await page.locator('#project-select').inputValue(),B,'legacy settings link retains the explicit project');

    await navigate('#projects');
    await page.getByRole('button',{name:'새 프로젝트 +',exact:true}).click();
    await page.getByText('현재는 제한된 검증 프로젝트만 실행합니다.',{exact:true}).waitFor();
    await page.getByLabel('이름',{exact:true}).fill('응답 유실 모의 프로젝트');
    await page.getByLabel('목표',{exact:true}).fill('일반 목표를 입력해도 사용자의 목표를 바꾸지 않습니다.');
    await page.locator('#create-form button[type=submit]').click();
    await page.locator('#create-form [role=alert]').filter({hasText:'연결'}).waitFor();
    assert.equal(writes.length,1);
    await page.reload();await page.getByRole('button',{name:'이어서 작성'}).click();
    assert.equal(await page.getByLabel('이름',{exact:true}).inputValue(),writes[0].name);
    assert.equal(await page.getByLabel('목표',{exact:true}).inputValue(),writes[0].goal);
    await page.locator('#create-form button[type=submit]').click();
    await page.getByRole('heading',{name:'매니저',exact:true}).waitFor();
    assert.deepEqual(writes[0],writes[1],'reload retries the identical intent and payload');
    assert.equal(creationReceipts.size,1);
    assert.equal(creationReceipts.values().next().value.first_pm_requests,1);
    assert.equal(new URL(page.url()).hash,'#manager?project='+CREATED);
    await page.getByText('아직 PM 답변은 도착하지 않았습니다.',{exact:false}).waitFor();
    assert.equal(await page.evaluate(()=>sessionStorage.getItem('ai-company:create-intent:v1')),null,'a resolved creation intent is removed after success');
    await page.getByRole('button',{name:'새 프로젝트',exact:true}).click();
    assert.equal(await page.getByLabel('이름',{exact:true}).inputValue(),'','the next creation starts with a fresh intent');
    assert.equal(await page.getByLabel('목표',{exact:true}).inputValue(),'');
    assert.equal(await page.getByLabel('이름',{exact:true}).evaluate(element=>element.readOnly),false);
    await page.getByRole('button',{name:'대화상자 닫기',exact:true}).click();

    await navigate('#manager?project='+A);
    await page.getByText('독립 역할 2개 · 병렬 배치 예정',{exact:true}).waitFor({state:'attached'});
    assert.equal(await page.getByText(/2개 작업 진행 중/).count(),0);
    scenario='running';await page.reload();
    await page.getByText('1개 작업 진행 중 · 실행 기록 기준',{exact:true}).waitFor({state:'attached'});
    await page.getByText('같은 계정 사용량 회복 대기',{exact:true}).waitFor({state:'attached'});
    await page.getByText(/모델 관측.*observed-model.*추론 미확인/).waitFor({state:'attached'});
    assert.equal(writes.length,2,'reading states sends no additional write');

    for(const theme of ['light','black']){
      await selectTheme(page,theme);
      for(const width of [320,360,390,1440]){
        await page.setViewportSize({width,height:width===1440?1000:844});
        for(const screen of ['projects','manager']){
          await navigate('#'+screen+(screen==='manager'?'?project='+A:''));
          await page.getByRole('heading',{name:screen==='projects'?'프로젝트':'매니저',exact:true}).waitFor();
          await noOverflow(theme+' '+screen+' '+width);
          await settledScreenshot(page,{path:path.join(output,`${theme}-${width}-${screen}-fixture.png`),fullPage:true});
        }
      }
    }
    await page.setViewportSize({width:320,height:844});
    await navigate('#projects');
    const create=page.locator('.page-actions [data-action=create-project]');
    let focused=false;
    for(let index=0;index<35;index++){
      await page.keyboard.press('Tab');
      if(await create.evaluate(element=>element===document.activeElement)){focused=true;break;}
    }
    assert.equal(focused,true,'new project is reachable with keyboard alone');
    assert.equal(await create.evaluate(element=>{const s=getComputedStyle(element);return s.outlineStyle!=='none'&&parseFloat(s.outlineWidth)>0||s.boxShadow!=='none';}),true,'keyboard focus has a visible indicator');
    assert.ok((await create.boundingBox()).height>=44,'primary touch target meets the product 44px requirement');
    await page.keyboard.press('Enter');
    await page.getByLabel('이름',{exact:true}).waitFor();
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#dialog').evaluate(element=>element.open),false);
    assert.equal(await create.evaluate(element=>element===document.activeElement),true,'closing the dialog restores the initiating keyboard focus');
    const enlargementMeasurements=[];
    for(const theme of ['light','black']){
      await selectTheme(page,theme);
      await navigate('#projects');
      // Navigating to the same hash may retain the current document. Explicitly
      // reload so the second theme starts at 100%, not the previous 200%.
      await page.reload();
      await page.getByRole('heading',{name:'프로젝트',exact:true}).waitFor();
      // A text-only enlargement fixture: double computed text and line sizes
      // without reducing the CSS viewport or claiming a physical-device test.
      const enlarged=await page.evaluate(()=>{
        const heading=document.querySelector('.page-heading h1');
        const baseline=parseFloat(getComputedStyle(heading).fontSize);
        const elements=[...document.querySelectorAll('button,input,textarea,label,h1,h2,h3,p,a,strong,span,summary,small,time,dt,dd')];
        const sizes=elements.map(element=>{const s=getComputedStyle(element);return [element,parseFloat(s.fontSize),parseFloat(s.lineHeight)];});
        for(const [element,font,line] of sizes){element.style.fontSize=font*2+'px';if(Number.isFinite(line))element.style.lineHeight=line*2+'px';}
        return {baseline,enlarged:parseFloat(getComputedStyle(heading).fontSize)};
      });
      assert.equal(enlarged.enlarged,enlarged.baseline*2,'the text enlargement is exactly 200%');
      if(enlargementMeasurements.length)assert.equal(enlarged.baseline,enlargementMeasurements[0].baseline,'each theme starts with the same original text size');
      enlargementMeasurements.push({theme,...enlarged});
      await noOverflow(theme+' 320px text-only enlargement 200%');
      await page.screenshot({path:path.join(output,`${theme}-320-projects-text-200-fixture.png`),fullPage:true});
    }
    await page.setViewportSize({width:320,height:540});
    await page.emulateMedia({reducedMotion:'reduce'});
    await navigate('#manager?project='+A);
    await page.getByLabel('PM에게 전달할 내용').fill('작은 화면에서도 전송 전 초안을 보존합니다.');
    await page.getByRole('button',{name:'보내기',exact:true}).scrollIntoViewIfNeeded();
    await noOverflow('320px short viewport with draft');
    failOverview=true;await page.evaluate(()=>window.dispatchEvent(new Event('offline')));
    await page.locator('#connection').filter({hasText:'연결 끊김'}).waitFor();
    assert.equal(await page.getByRole('button',{name:'보내기',exact:true}).isDisabled(),true);
    assert.match(await page.locator('#connection').innerText(),/마지막/);
    assert.equal(await page.getByLabel('PM에게 전달할 내용').inputValue(),'작은 화면에서도 전송 전 초안을 보존합니다.');
    assert.deepEqual(failures,[]);
    await fs.writeFile(path.join(output,'result.json'),JSON.stringify({kind:'browser_response_fixture',cases:['projects','missing ID','creation retry','planned vs actual','two themes four widths','offline draft'],enlargementMeasurements,api_writes_intercepted:writes.length,real_api_writes:0,model_calls:0},null,2));
    console.log(JSON.stringify({scenario:'text_only_enlargement_fixture',enlargementMeasurements,real_api_writes:0,model_calls:0}));
    console.log('PASS PR12 independent browser fixtures; no real API writes or model calls');
  }catch(error){await page.screenshot({path:path.join(output,'failure.png'),fullPage:true}).catch(()=>{});throw error;}
  finally{await browser.close();}
})().catch(error=>{console.error(error.stack||error.message);process.exitCode=1;});
