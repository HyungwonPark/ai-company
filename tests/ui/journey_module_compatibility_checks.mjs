/* Real application boundary functions, with HTTP calls captured instead of sent. */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import vm from 'node:vm';
import {execFileSync} from 'node:child_process';
const root=new URL('../../',import.meta.url),source=await fs.readFile(new URL('src/ai_company/web/app.js',root),'utf8');
const extract=(a,b)=>{const start=source.indexOf(a),end=source.indexOf(b,start);assert.ok(start>=0&&end>start,'application compatibility boundary exists');return source.slice(start,end);};
const helpers=extract('function workspaceCompatible(){','function scopeParams(){');
const scope=extract('function selectedScope(){','function scopeName('),api=extract('async function api(','function themeControls(');
const oldSource=execFileSync('git',['show','75227480d27d3eb1cd57fa43a07a0ab986be3eb5:src/ai_company/web/workspace-graph-ui.js'],{cwd:root,encoding:'utf8'});
const currentSource=await fs.readFile(new URL('src/ai_company/web/workspace-graph-ui.js',root),'utf8');
const factory=async text=>(await import('data:text/javascript;base64,'+Buffer.from(text).toString('base64'))).createWorkspaceGraph;
const [oldFactory,currentFactory]=await Promise.all([factory(oldSource),factory(currentSource)]);
const writePaths=['/api/projects','/api/projects/p/messages','/api/projects/p/harness','/api/projects/p/execution-specs','/api/projects/p/plans/plan/confirm','/api/projects/p/approvals/approval/decisions'];
for(const [name,createGraph,report] of [['old graph',oldFactory,{execution(){}}],['old report',currentFactory,{}],['both old',oldFactory,{}]]){
 const calls=[],notices=[],params=new URLSearchParams('project=p&run=original&snapshot=original-snapshot');let reloads=0;
 const state={projectId:'p',view:'progress',overview:{project:{id:'p'}},drafts:{'p:message':'보관할 초안'},csrf:'fixture',connected:true};
 const context={workspaceGraph:createGraph({esc:String}),reportUI:report,integrated:true,state,recordParams:params,scopeParams:()=>({runId:params.get('run'),snapshotId:params.get('snapshot')}),busyForms:new Set(),dialog:{open:false},notify:text=>notices.push(text),location:{hash:'#progress?'+params,reload(){reloads++;}},TextEncoder,AbortController,setTimeout,clearTimeout,navigator:{onLine:true},fetch:async(path,options)=>{calls.push({path,method:options.method});return {ok:true,json:async()=>({authenticated:true})};}};
 vm.createContext(context);vm.runInContext(helpers+scope+api,context);
 assert.equal(context.workspaceCompatible(),false,name);assert.equal(context.selectedScope(),null);context.syncScope();assert.equal(params.get('run'),'original');
 const before=JSON.stringify(state);
 for(const path of writePaths)await assert.rejects(context.api(path,{method:'POST',body:{idempotency_key:'keep-original'}}),error=>error.code==='workspace_update_required');
 assert.equal(calls.length,0);assert.equal(JSON.stringify(state),before,'rejection does not clear a draft or change connectivity');
 for(const path of ['/api/session','/api/projects'])await context.api(path);
 for(const path of ['/api/login','/api/password','/api/logout'])await context.api(path,{method:'POST',body:{}});
 assert.equal(calls.length,5,'authentication and read operations remain available');
 context.busyForms.add('plan-form');context.reloadWorkspace();assert.equal(reloads,0,'in-flight submission not interrupted');context.busyForms.clear();context.dialog.open=true;context.reloadWorkspace();assert.equal(reloads,0,'open dialog not silently discarded');context.dialog.open=false;context.reloadWorkspace();assert.equal(reloads,1);assert.equal(context.location.hash,'#progress?'+params);assert.equal(state.drafts['p:message'],'보관할 초안');
 assert.match(context.moduleUpgrade(),/data-module-upgrade/);assert.doesNotMatch(context.moduleUpgrade(),/getScope|selectScope|execution\(/);
}
const context={workspaceGraph:currentFactory({esc:String}),reportUI:{execution(){}}};vm.createContext(context);vm.runInContext(helpers,context);assert.equal(context.workspaceCompatible(),true);
console.log('PASS: actual old/new graph contracts, 3 mixed combinations, exact scope, 18 blocked writes, auth/read access, manual recovery and in-flight/draft preservation');
