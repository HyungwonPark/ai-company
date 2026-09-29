/* R25-1: read-only reproduction. REPRODUCED means the reported bug was observed, not a product PASS. Node 18+. No browser or operational origin is used. */
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const {execFileSync}=require('node:child_process');
const BASE='75227480d27d3eb1cd57fa43a07a0ab986be3eb5',HEAD='dbd868b1611ef374e9169c2254590d69d745b2dd';
const git=(ref,file)=>execFileSync('git',['show',ref+':src/ai_company/web/'+file],{encoding:'utf8'});
// Optional input is for an isolated review mirror; normal use reads fixed Git commits.
const s=process.env.PR25_REVIEW_INPUT_JSON?JSON.parse(fs.readFileSync(process.env.PR25_REVIEW_INPUT_JSON,'utf8')):{v8:git(BASE,'sw.js'),v9:git(HEAD,'sw.js'),app:git(HEAD,'app.js')};
const origin='https://isolated.invalid';
const cachesData=new Map();
const toURL=req=>new URL(typeof req==='string'?req:req.url,origin).href;
const cacheApi={
 async open(name){if(!cachesData.has(name))cachesData.set(name,new Map());const data=cachesData.get(name);return {
  async put(req,res){data.set(toURL(req),res.clone());},
  async match(req){return data.get(toURL(req))?.clone();},
  async addAll(paths){for(const p of paths)await this.put(p,await network({url:toURL(p)}));}
 };},
 async match(req){for(const c of cachesData.values()){const r=c.get(toURL(req));if(r)return r.clone();}},
 async keys(){return [...cachesData.keys()];},async delete(k){return cachesData.delete(k);}
};
let online=true;
async function network(req){if(!online)throw new TypeError('Network offline');const p=new URL(req.url).pathname;return new Response(p==='/app.js'?s.app:p==='/journey-ui.js'?'export const journeyHref=()=>{};':'public shell');}
function worker(source){const events={};vm.runInNewContext(source,{self:{location:{origin},addEventListener:(n,f)=>events[n]=f,clients:{claim:async()=>{}}},URL,Response,caches:cacheApi,fetch:network});return events;}
async function request(events,p){const req={method:'GET',url:toURL(p)};let response;const waits=[];events.fetch({request:req,respondWith:r=>response=r,waitUntil:r=>waits.push(r)});const intercepted=Boolean(response);const result=await (response||network(req));await Promise.all(waits);return {result,intercepted};}
(async()=>{
 const old=worker(s.v8),candidate=worker(s.v9);
 await cacheApi.open('ai-company-shell-v8');
 // Candidate install fills v9 while another live tab keeps the v8 worker active.
 const waits=[];candidate.install({waitUntil:p=>waits.push(p)});await Promise.all(waits);
 // An online reload under v8 obtains the new app and writes it into v8 cache.
 const current=await request(old,'/app.js');assert.match(await current.result.text(),/^import .*journey-ui/);
 assert.ok(await (await cacheApi.open('ai-company-shell-v8')).match('/app.js'));
 assert.ok(await (await cacheApi.open('ai-company-shell-v9')).match('/journey-ui.js'));
 online=false;
 const app=await request(old,'/app.js');assert.equal(app.intercepted,true);assert.match(await app.result.text(),/^import .*journey-ui/);
 await assert.rejects(()=>request(old,'/journey-ui.js'),/Network offline/);
 const fixedContext=await request(candidate,'/journey-ui.js');assert.equal(fixedContext.intercepted,true);
 console.log(JSON.stringify({status:'REPRODUCED',method:'Node VM execution of unchanged repository service workers; deterministic CacheStorage and offline network',sequence:['v9 cache populated but v8 remains controller','online reload caches candidate app.js under v8','offline reload retrieves candidate app.js from v8','new static import journey-ui.js is not intercepted by v8 and rejects despite v9 cache containing it'],control:'v9 controller intercepts and serves the same module offline',limitations:'Does not execute a real browser service-worker lifecycle or APK'}));
})().catch(e=>{console.error(e);process.exitCode=1;});
