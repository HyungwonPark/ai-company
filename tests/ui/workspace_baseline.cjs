/* Existing production UI, synthetic records translated from exactly the preview dataset. */
const {chromium}=require('playwright');
const fs=require('node:fs/promises'),path=require('node:path'),assert=require('node:assert/strict');
const {selectTheme,settledScreenshot}=require('./capture.cjs');
const d=require('../../src/ai_company/web/workspace-preview/data.js');
const web=path.resolve(__dirname,'../../src/ai_company/web'),out=process.env.UI_OUTPUT||'/tmp/ai-company-workspace-preview',origin='http://127.0.0.1:47994';
const projects=d.projects.map(p=>({...p,status:'PLANNING',created_at:1789570000,updated_at:1789571000,pending_approval_count:p.id===d.approval.project_id?1:0,recent_pm_request:{state:'completed'},recent_run:p.id===d.approval.project_id?{id:'fixture-run-02',state:'running',mode:'fixture'}:null}));
function overview(p){const active=p.id==='fixture-pilot';const roles=active?d.roles.map(r=>({...r,id:r.id,key:r.id,plan_id:'fixture-old-plan',status:r.wait?'WAITING_QUOTA':'RUNNING',current_task_id:'task-'+r.id})):[];return {
 project:{...p,harness_version:1,request_revision:2,harness_content:'두 역할 독립 진행'},roles,tasks:roles.map(r=>({id:r.current_task_id,role_id:r.id,title:r.task,status:r.status,wait_reason:r.wait,resume_at:r.wait?1789575000:null,stage:'developer'})),
 plans:active?[{id:'fixture-old-plan',digest:'d'.repeat(64),request_revision:1,status:'confirmed',mode:'fixture',base_harness_version:1,content:{summary:'역할 상태 집계와 독립 검사',roles:d.roles.map(r=>({key:r.id,name:r.name,responsibility:r.responsibility,goal:r.task,allowed_paths:[r.path],acceptance:['빈 입력·모든 분류·알 수 없는 상태·입력 보존'],depends_on:[]})),completion_criteria:['같은 후보 검사·독립 검수']}}]:[],
 runs:active?[{id:'fixture-run-02',plan_id:'fixture-old-plan',plan_digest:'d'.repeat(64),state:'running',mode:'fixture',roles:Object.fromEntries(roles.map(r=>[r.key,{task_id:r.current_task_id,status:r.status}]))}]:[],
 pm_requests:[{id:'fixture-pm-question',request_revision:2,state:'completed',requested_configuration:{provider:'codex',model:d.pm.model,reasoning_effort:d.pm.effort}}],
 messages:[{id:'fixture-goal',role:'user',content:p.goal,created_at:1789570000},...(active?[{id:'fixture-question',role:'assistant',content:d.pm.question+'\n'+d.pm.body,created_at:1789571000}]:[])],
 approvals:active?[{...d.approval,environment:'fixture',cost_usd:0,expires_at:2000000000,verification:d.approval.evidence.join('\n')}]:[],reports:[],documents:{},harnesses:[],delegations:[],execution_specs:[],
 collaboration:{source:'fixture',cursor:1,nodes:roles.map(r=>({...r,kind:'role',task_ids:[r.current_task_id],wait_reason:r.wait,assignment:{provider:r.provider,requested:{model:r.model,reasoning_effort:r.effort},observed:{status:'unavailable'}}})),transfers:active?[{...d.transfer,from:'implementation',to:'tests',kind:'dependency',status:'received',cursor:1,created_at:1789570880,artifact_ref:d.transfer.path,artifact_sha:d.transfer.sha,run_id:d.transfer.run}]:[]},
 readiness:{mode:'fixture',pm:'plan_proposed'},translation_summary:{status:'pending',counts:{}}
};}
(async()=>{await fs.mkdir(out,{recursive:true});const browser=await chromium.launch({headless:true,chromiumSandbox:true,...(process.env.CHROME_CHANNEL?{channel:process.env.CHROME_CHANNEL}:{})});const context=await browser.newContext({viewport:{width:390,height:844},serviceWorkers:'block'});const page=await context.newPage();page.setDefaultTimeout(6000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await context.route('**/*',async route=>{const req=route.request(),url=new URL(req.url());assert.equal(url.origin,origin);assert.equal(req.method(),'GET','baseline never writes');const json=value=>route.fulfill({contentType:'application/json',body:JSON.stringify(value)});
 if(url.pathname==='/api/session')return json({authenticated:true,username:'edward',login_method:'password',csrf_token:'fixture'});
 if(url.pathname==='/api/projects')return json({projects});
 if(url.pathname==='/api/execution-catalog')return json({entries:[]});
 if(url.pathname.endsWith('/execution-specs'))return json({execution_specs:[]});
 const match=url.pathname.match(/^\/api\/projects\/([^/]+)\/overview$/);if(match)return json(overview(projects.find(p=>p.id===match[1])));
 if(url.pathname.startsWith('/api/'))throw Error('Unexpected baseline API '+url.pathname);
 const file=path.resolve(web,'.'+(url.pathname==='/'?'/index.html':url.pathname));assert.ok(file.startsWith(web+path.sep));try{return route.fulfill({body:await fs.readFile(file),contentType:({'.html':'text/html','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml','.woff2':'font/woff2'})[path.extname(file)]||'application/octet-stream'});}catch(e){if(e.code==='ENOENT')return route.fulfill({status:404,body:''});throw e;}
 });
 try{for(const theme of ['light','black'])for(const view of ['projects','manager','progress','approvals']){await page.goto(origin+'/#'+view+(view==='projects'?'':'?project=fixture-pilot'));await page.locator('.nav').waitFor();await page.getByRole('heading',{name:view==='projects'?'프로젝트':view==='manager'?'매니저':view==='progress'?'진행':'승인',exact:true}).first().waitFor();await selectTheme(page,theme);await settledScreenshot(page,{path:path.join(out,`workspace-baseline-${view}-${theme}-390.png`),fullPage:true});}assert.deepEqual(errors,[]);console.log('Same-data baseline: existing projects/manager/progress/approvals, Light/Black 390px, zero writes PASS');}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
