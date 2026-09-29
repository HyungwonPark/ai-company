/* R25-1b: read-only fixed-source reproduction. REPRODUCED means the reported bug was observed. Node 18+. No live browser/API/account is used. */
import fs from 'node:fs/promises';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
const {execFileSync}=await import('node:child_process');
const BASE='75227480d27d3eb1cd57fa43a07a0ab986be3eb5',HEAD='0bcf8d7e93f2d1bab446503ab9da0e080182bd94';
const inputs=process.env.R25_MIXED_INPUT?JSON.parse(await fs.readFile(process.env.R25_MIXED_INPUT,'utf8')):null;
const fixed=(key,ref,file)=>inputs?inputs[key]:execFileSync('git',['show',ref+':src/ai_company/web/'+file],{encoding:'utf8'});
const app=fixed('app',HEAD,'app.js');
const source = app.slice(app.indexOf('function selectedScope()'), app.indexOf('function scopeName('));
assert.ok(source.includes('workspaceGraph.selectScope'));
const rows=[];
const origin='https://isolated.invalid';
const oldGraph=fixed('oldGraph',BASE,'workspace-graph-ui.js');
const sw=fixed('sw',BASE,'sw.js');
const cached=new Map([[origin+'/workspace-graph-ui.js',oldGraph]]),handlers=new Map();
const caches={open:async()=>({put:async(request,response)=>{cached.set(request.url,await response.text());}}),match:async request=>cached.has(request.url)?new Response(cached.get(request.url)):undefined};
const network=async request=>{if(new URL(request.url).pathname==='/workspace-graph-ui.js')throw new Error('Only this static module is temporarily unreachable');return new Response(new URL(request.url).pathname==='/api/session'?JSON.stringify({authenticated:true}):app);};
vm.runInNewContext(sw,{URL,Response,caches,fetch:network,self:{location:{origin},addEventListener:(type,handler)=>handlers.set(type,handler)}});
async function request(path){let response;const waits=[];const req=new Request(origin+path);handlers.get('fetch')({request:req,respondWith:value=>{response=value;},waitUntil:value=>waits.push(value)});const resolved=await(response||network(req));await Promise.all(waits);return resolved.text();}
assert.equal(await request('/app.js'),app);
assert.equal(await request('/workspace-graph-ui.js'),oldGraph);
assert.deepEqual(JSON.parse(await request('/api/session')),{authenticated:true});
for(const [name,graphSource] of [['baseline graph',oldGraph],['candidate graph',fixed('currentGraph',HEAD,'workspace-graph-ui.js')]]) {
  const {createWorkspaceGraph}=await import('data:text/javascript;base64,'+Buffer.from(graphSource).toString('base64'));
  const workspaceGraph=createWorkspaceGraph({esc:String});
  const context=vm.createContext({integrated:true,state:{projectId:'p',view:'progress',overview:{project:{id:'p'}}},recordParams:new URLSearchParams('project=p'),workspaceGraph,scopeParams:()=>({runId:null,snapshotId:null})});
  vm.runInContext(source,context);
  const outcomes={};
  for(const call of ['selectedScope()','syncScope()']) {
    try{vm.runInContext(call,context);outcomes[call]='PASS';}catch(e){outcomes[call]=e.name+': '+e.message;}
  }
  rows.push({name,sha256:crypto.createHash('sha256').update(graphSource).digest('hex'),getScope:typeof workspaceGraph.getScope,selectScope:typeof workspaceGraph.selectScope,outcomes});
}
assert.match(rows[0].outcomes['selectedScope()'],/getScope is not a function/);
assert.match(rows[0].outcomes['syncScope()'],/selectScope is not a function/);
assert.equal(rows[1].outcomes['selectedScope()'],'PASS');
assert.equal(rows[1].outcomes['syncScope()'],'PASS');
console.log(JSON.stringify({status:'REPRODUCED',app_sha256:crypto.createHash('sha256').update(app).digest('hex'),method:'Actual fixed app selectedScope/syncScope and unchanged baseline/candidate graph factories; unchanged v8 SW with deterministic selective module network failure/cache fallback; Node VM, no browser or live API',scenario:{app:'candidate network bytes',graph:'baseline cached bytes after only graph network failure',session:'authenticated=true API still reachable'},rows},null,2));
