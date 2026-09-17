/* Independent diagram recovery UI checks; every API request is synthetic. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path');
(async()=>{
 const base=process.env.BASE_URL,out=process.env.UI_OUTPUT||'/tmp/ai-company-diagrams';
 assert.ok(base,'BASE_URL must refer to the temporary static server');
 await fs.mkdir(out,{recursive:true});
 const browser=await chromium.launch({headless:true,chromiumSandbox:true,...(process.env.CHROME_CHANNEL?{channel:process.env.CHROME_CHANNEL}:{}),...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
 const context=await browser.newContext({viewport:{width:390,height:844},serviceWorkers:'block'}),page=await context.newPage();
 page.setDefaultTimeout(12000);
 const project='a'.repeat(32),snapshot='plan:independent',fingerprint='b'.repeat(64),key='stored-prepared-A';
 const endpoint=`/api/projects/${project}/diagrams`,storageKey=`diagram-request:${project}:${snapshot}:${fingerprint}`;
 const url=base+'/diagram-view.html?'+new URLSearchParams({project,snapshot,fingerprint,resume:key});
 const item={project_id:project,snapshot_id:snapshot,snapshot_fingerprint:fingerprint,request_key:key,status:'prepared',created_at:1700000000};
 const posts=[],unexpected=[],errors=[];let postMode='forbidden',release=null,firstPost;
 const firstPosted=new Promise(resolve=>{firstPost=resolve;});
 page.on('pageerror',e=>errors.push(e.message));
 await context.route('**/*',async route=>{
  const request=route.request(),u=new URL(request.url());
  if(u.origin!==new URL(base).origin){unexpected.push(request.url());return route.abort();}
  if(!u.pathname.startsWith('/api/'))return route.continue();
  const json=(value,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(value)});
  if(u.pathname==='/api/session'&&request.method()==='GET')return json({authenticated:true,password_change_required:false,csrf_token:'synthetic-csrf'});
  if(u.pathname===endpoint&&request.method()==='GET')return json({project_id:project,items:[item]});
  if(u.pathname===endpoint&&request.method()==='POST'){
   posts.push(request.postDataJSON());assert.equal(request.headers()['x-csrf-token'],'synthetic-csrf');
   if(postMode==='held')await new Promise(resolve=>{release=resolve;firstPost();});
   if(postMode==='lost')return route.abort('failed');
   return json({error:{code:'forbidden',message:'권한을 다시 확인해 주세요.'}},403);
  }
  unexpected.push(`${request.method()} ${u.pathname}`);return route.abort();
 });
 try{
  // A tab has a different unknown request B. The explicit stored request A wins.
  await context.addInitScript(({storageKey})=>sessionStorage.setItem(storageKey,'unresolved-B'),{storageKey});
  await page.goto(url);await page.getByRole('button',{name:'같은 요청 확인',exact:true}).waitFor();
  assert.equal(posts.length,0,'opening a prepared history link must not generate');
  postMode='held';await page.getByRole('button',{name:'같은 요청 확인',exact:true}).click();
  await page.getByRole('button',{name:'생성 중',exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'생성 중',exact:true}).isDisabled(),true);
  await page.waitForFunction(()=>document.querySelector('#generate')?.disabled);
  await Promise.race([firstPosted,new Promise((_,reject)=>setTimeout(()=>reject(new Error('POST was not intercepted')),12000))]);
  assert.deepEqual(posts,[{snapshot_id:snapshot,fingerprint,idempotency_key:key}]);
  release();
  await page.getByText('권한을 다시 확인해 주세요.',{exact:true}).waitFor();
  assert.equal(await page.evaluate(k=>sessionStorage.getItem(k),storageKey),key,'a failed authorization response must retain the chosen request');
  postMode='lost';await page.getByRole('button',{name:'같은 요청 확인',exact:true}).click();
  await page.getByRole('button',{name:'같은 요청 확인',exact:true}).waitFor();
  assert.deepEqual(posts[1],posts[0]);
  await page.reload();await page.getByRole('button',{name:'같은 요청 확인',exact:true}).waitFor();
  postMode='forbidden';await page.getByRole('button',{name:'같은 요청 확인',exact:true}).click();
  await page.getByText('권한을 다시 확인해 주세요.',{exact:true}).waitFor();
  assert.equal(posts.length,3);assert.ok(posts.every(p=>JSON.stringify(p)===JSON.stringify(posts[0])));
  assert.equal(await page.locator('#history-list a').getAttribute('href'),'/diagram-view.html?'+new URLSearchParams({project,snapshot,fingerprint,resume:key}).toString());
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.screenshot({path:path.join(out,'diagram-independent-prepared-recovery-390.png'),fullPage:true});
  assert.deepEqual(unexpected,[]);assert.deepEqual(errors,[]);
  await fs.writeFile(path.join(out,'diagram-independent-validation.json'),JSON.stringify({fixture:true,api:'synthetic intercept; all unlisted API and external requests blocked',browser:await browser.version(),sandbox:true,checks:['explicit prepared history wins over a different session key','opening history does not generate; in-flight generation disabled','authorization and network failure preserve exact request through reload','mobile Korean recovery actions; no execution/approval API']},null,2));
  console.log('PASS: independent prepared history, ambiguity recovery, request isolation');
 }finally{if(release)release();await context.close();await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
