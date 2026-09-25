import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';

const root=new URL('../../',import.meta.url).pathname.replace(/\/$/,'');
const app=await readFile(root+'/src/ai_company/web/app.js','utf8');
const journey=await import('data:text/javascript;base64,'+Buffer.from((await readFile(root+'/src/ai_company/web/app.js','utf8')).split('/* Journey projections */')[1].split('/* End journey projections */')[0].replaceAll('function ','export function ')).toString('base64'));
const manager=await import('data:text/javascript;base64,'+Buffer.from(await readFile(root+'/src/ai_company/web/manager-ui.js','utf8')).toString('base64'));
const extract=(start,end)=>{const a=app.indexOf(start),b=app.indexOf(end,a);assert.ok(a>=0&&b>a);return app.slice(a,b);};

// Regression derived by an independent reviewer from delayed navigation defects.
// Execute the actual product request function. Only its dependencies are mocked.
{
 let release;
 const state={connected:true,projectId:'p',view:'manager'};
 const location={hash:'#manager?project=p'};
 const context={workspaceCompatible:()=>true,state,location,busyForms:new Set(),recordParams:new URLSearchParams('project=p'),URLSearchParams,
  executionMessage:'fixture request',projectHref:journey.journeyHref,render(){},refresh:async()=>{},notify(){},
  api:()=>new Promise(resolve=>release=resolve)};
 vm.createContext(context);
 vm.runInContext(extract('async function requestExecutionPlan(){','function closeButton('),context);
 const pending=context.requestExecutionPlan();
 // Same-project hashchange has selected a historical run on the same manager view.
 location.hash='#manager?project=p&run=old-run&snapshot=old-snapshot';
 context.recordParams=new URLSearchParams(location.hash.split('?')[1]);
 release({}); await pending;
 assert.equal(location.hash,'#manager?project=p&run=old-run&snapshot=old-snapshot');
 console.log('PASS'+' 1: late PM request response final URL:',location.hash);
}

// Execute the entire unchanged submit listener, including the actual confirm branch.
{
 let release,submitListener;
 const state={connected:true,projectId:'p',view:'manager',overview:{project:{id:'p'}}};
 const location={hash:'#manager?project=p'};
 const button={disabled:false,textContent:'이 계획 확정'};
 const form={id:'plan-form',dataset:{project:'p',id:'new-plan',digest:'new-digest',base:'0',key:'test-key'}};
 const context={workspaceCompatible:()=>true,state,location,integrated:true,busyForms:new Set(),journeyHref:journey.journeyHref,
  executionReferenceMatches:manager.executionReferenceMatches,planReview:{plan:{}},
  document:{addEventListener(name,listener){assert.equal(name,'submit');submitListener=listener;},querySelector(){return null;}},
  $:selector=>selector.startsWith('button')?button:null,FormData:class{*[Symbol.iterator](){yield ['reviewed','on'];}},
  dialog:{contains:()=>true,close(){}},refresh:async()=>{},notify(){},
  api:()=>new Promise(resolve=>release=resolve)};
 vm.createContext(context);
 vm.runInContext(extract("document.addEventListener('submit',async event=>{","document.addEventListener('change',event=>{if(event.target.id!=='journey-run'"),context);
 const pending=submitListener({target:form,preventDefault(){}});
 // Browser back can change the hash while a native modal is open or after closing it.
 location.hash='#manager?project=p&run=old-run&snapshot=old-snapshot';
 release({run:{id:'new-run',project_id:'p',plan_id:'new-plan',plan_digest:'new-digest'}});
 await pending;
 assert.equal(location.hash,'#manager?project=p&run=old-run&snapshot=old-snapshot');
 console.log('PASS'+' 2: late confirmation response final URL:',location.hash);
}

// A pending hashchange must not be overwritten by a completion-triggered render.
{
 const source=app;
 const state={authenticated:true,connected:true,view:'progress',projectId:'old-project',overview:null};
 const location={hash:'#progress?project=new-project&run=new-run'};
 const params=new URLSearchParams('project=old-project&run=old-run');
 const document={activeElement:null,getElementById:()=>null,querySelector:()=>null};
 const empty={querySelectorAll:()=>[],classList:{toggle(){}}};
 const context={workspaceCompatible:()=>true,state,location,recordParams:params,integrated:true,document,app:{...empty},dialog:{...empty},
  syncScope(){},readView:()=>location.hash.slice(1).split('?')[0],journeyHref:journey.journeyHref,
  scopeParams:()=>({runId:params.get('run'),snapshotId:params.get('snapshot')}),
  workspaceGraph:{isInteracting:()=>false,capture(){}},busyForms:new Set(),openDetails:new Map(),
  history:{replaceState(a,b,target){location.hash=target;}},window:{scrollY:0,scrollTo(){}},
  shell:()=>'',authScreen:()=>'',pendingGraphRender:false,URLSearchParams};
 vm.createContext(context);
 const start=source.indexOf('function render(){'),end=source.indexOf('async function refresh(',start);
 vm.runInContext(source.slice(start,end),context);context.render();
 assert.equal(location.hash,'#progress?project=new-project&run=new-run');
 console.log('PASS 3: render before pending hashchange:',location.hash);
}

// A → B → A is still deliberate navigation, even when the final URL is identical.
{
 let release;
 const state={connected:true,projectId:'p',view:'project',navigationGeneration:0};
 const location={hash:'#project?project=p'};
 const context={workspaceCompatible:()=>true,state,location,busyForms:new Set(),recordParams:new URLSearchParams('project=p'),URLSearchParams,
  executionMessage:'fixture request',projectHref:journey.journeyHref,render(){},refresh:async()=>{},notify(){},
  api:()=>new Promise(resolve=>release=resolve)};
 vm.createContext(context);
 vm.runInContext(extract('async function requestExecutionPlan(){','function closeButton('),context);
 const pending=context.requestExecutionPlan();
 state.navigationGeneration+=2;
 release({});await pending;
 assert.equal(location.hash,'#project?project=p');
 console.log('PASS'+' 4: PM response after away/back to the same settings URL:',location.hash);
}
