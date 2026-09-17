/* Independent routing interactions. Temporary real auth + injected fixture reads only. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path'),{createHash}=require('node:crypto');
(async()=>{
 const base=process.env.BASE_URL,fixtures=JSON.parse(process.env.GRAPH_FIXTURES),out=process.env.UI_OUTPUT||'/tmp/graph-edges-independent';
 assert.match(base,/^http:\/\/127\.0\.0\.1:\d+$/);await fs.mkdir(out,{recursive:true});
 const browser=await chromium.launch({headless:true,chromiumSandbox:true,...(process.env.CHROME_CHANNEL?{channel:process.env.CHROME_CHANNEL}:{}),...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
 const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce',serviceWorkers:'block'}),page=await context.newPage();page.setDefaultTimeout(12000);
 const endpoint=`/api/projects/${fixtures.project_id}/overview`,writes=[],violations=[],errors=[],checks=[];
 let original=null,injection=null,readCount=0;
 page.on('pageerror',error=>errors.push(error.message));
 await context.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url());
  if(url.origin!==base||request.method()!=='GET'&&!['/api/login','/api/password'].includes(url.pathname)){violations.push(`${request.method()} ${url.pathname}`);return route.abort();}
  if(request.method()!=='GET')writes.push(url.pathname);
  if(url.pathname===endpoint){const response=await route.fetch(),body=await response.json();if(response.ok())original=structuredClone(body);readCount++;return route.fulfill({response,json:injection?structuredClone(injection):body});}
  return route.continue();
 });
 const action=name=>page.locator(`[data-rg-action="${name}"]`);
 const close=async()=>{if(await action('clear').count())await action('clear').click();};
 const settle=()=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 const shot=async name=>{await page.evaluate(()=>document.fonts.ready);await page.screenshot({path:path.join(out,`graph-edges-independent-${name}.png`),fullPage:true});};
 const fingerprint=body=>{const graph=body.workspace_graph;graph.fingerprint=createHash('sha256').update(JSON.stringify(graph.snapshots)).digest('hex');for(const s of graph.snapshots)s.fingerprint=createHash('sha256').update(JSON.stringify(s.nodes)+JSON.stringify(s.edges)).digest('hex');return body;};
 async function refresh(){const count=readCount;await page.locator('.integrated-graph-refresh [data-action="refresh"]').click();await page.waitForFunction(()=>!document.querySelector('.integrated-graph-refresh [data-action="refresh"]')?.disabled);for(let i=0;readCount===count&&i<120;i++)await new Promise(resolve=>setTimeout(resolve,50));assert.ok(readCount>count);await settle();}
 async function selection(edge,snapshot){
  await page.locator('.rg-direction').waitFor();
  assert.equal((await page.locator('.rg-direction').textContent()).trim(),`${snapshot.nodes.find(n=>n.id===edge.from).name} → ${snapshot.nodes.find(n=>n.id===edge.to).name}`);
  assert.ok((await page.locator('.rg-detail').textContent()).includes(edge.reason));
  assert.equal(await page.locator('.rg-edge.is-selected').getAttribute('data-rg-edge-id'),edge.id);
  assert.ok((await page.locator('.rg-detail').textContent()).includes(snapshot.run_id));
 }
 async function pointerPoint(id,label){
  return page.evaluate(({id,label})=>{
   const group=[...document.querySelectorAll('.rg-edge')].find(g=>g.dataset.rgEdgeId===id),viewport=document.querySelector('.rg-viewport').getBoundingClientRect();
   const visible=p=>p.x>Math.max(0,viewport.left)+2&&p.x<Math.min(innerWidth,viewport.right)-2&&p.y>Math.max(0,viewport.top)+2&&p.y<Math.min(innerHeight,viewport.bottom)-2;
   if(label){const node=[...document.querySelectorAll('.rg-edge-label')].find(n=>n.dataset.rgLabel===id),r=node.querySelector('rect').getBoundingClientRect(),p={x:r.x+r.width/2,y:r.y+r.height/2};return node.getAttribute('visibility')!=='hidden'&&visible(p)&&document.elementFromPoint(p.x,p.y)?.closest('[data-rg-label]')?.dataset.rgLabel===id?p:null;}
   const target=group.querySelector('.rg-edge-line'),matrix=target.getScreenCTM(),length=target.getTotalLength();
   const others=[...document.querySelectorAll('.rg-edge-line')].filter(p=>p!==target).map(p=>{const m=p.getScreenCTM(),len=p.getTotalLength();return Array.from({length:161},(_,i)=>p.getPointAtLength(len*i/160).matrixTransform(m));});
   let best=null;
   for(let i=3;i<98;i++){const p=target.getPointAtLength(length*i/100).matrixTransform(matrix);if(!visible(p))continue;const hit=document.elementFromPoint(p.x,p.y);if(!hit?.matches('.rg-edge-hit'))continue;const distance=Math.min(...others.flat().map(q=>Math.hypot(q.x-p.x,q.y-p.y)));if(distance>2&&(!best||distance>best.distance))best={x:p.x,y:p.y,distance};}
   return best;
  },{id,label});
 }
 async function revealPointer(id,labelOnly=false){
  const wire=page.locator(`.rg-edge-hit[data-rg-edge="${id}"]`);
  // Move the camera through actual keyboard input; never dispatch click or alter
  // graph positions/styles. A leader line does not define the label hit box.
  for(const fraction of labelOnly?[null]:[null,.5,.25,.75]){
   const move=await page.evaluate(({id,fraction})=>{
    const group=[...document.querySelectorAll('.rg-edge')].find(g=>g.dataset.rgEdgeId===id),viewport=document.querySelector('.rg-viewport').getBoundingClientRect();
    const label=[...document.querySelectorAll('.rg-edge-label')].find(n=>n.dataset.rgLabel===id);let point;
    if(fraction===null){if(label.getAttribute('visibility')==='hidden')return null;const rect=label.querySelector('rect').getBoundingClientRect();point={x:rect.x+rect.width/2,y:rect.y+rect.height/2};}
    else{const line=group.querySelector('.rg-edge-line');point=line.getPointAtLength(line.getTotalLength()*fraction).matrixTransform(line.getScreenCTM());}
    return {x:(Math.max(0,viewport.left)+Math.min(innerWidth,viewport.right))/2-point.x,y:(Math.max(0,viewport.top)+Math.min(innerHeight,viewport.bottom))/2-point.y};
   },{id,fraction});
   if(!move)continue;await wire.evaluate(el=>el.focus({preventScroll:true}));
   for(const [delta,negative,positive] of [[move.x,'ArrowLeft','ArrowRight'],[move.y,'ArrowUp','ArrowDown']]){
    const count=Math.round(Math.abs(delta)/24);assert.ok(count<=120,`unexpected pan distance for ${id}: ${count}`);
    for(let i=0;i<count;i++)await page.keyboard.press(delta<0?negative:positive);
   }
   await settle();
   const stroke=labelOnly?null:await pointerPoint(id,false);if(stroke)return {point:stroke,label:false};
   const labelPoint=await pointerPoint(id,true);if(labelPoint)return {point:labelPoint,label:true};
  }
  return null;
 }
 try{
  await page.goto(base+'/?workspace=integrated#projects');await page.getByLabel('비밀번호',{exact:true}).fill(process.env.TEST_PASSWORD);await page.getByRole('button',{name:'로그인',exact:true}).click();
  await page.getByLabel('현재 비밀번호',{exact:true}).fill(process.env.TEST_PASSWORD);await page.getByLabel('새 비밀번호',{exact:true}).fill('independent-edges-only-password');await page.getByLabel('새 비밀번호 확인',{exact:true}).fill('independent-edges-only-password');await page.getByRole('button',{name:'비밀번호 변경 후 계속'}).click();await page.locator('.nav').waitFor();
  await page.evaluate(id=>{location.hash='#progress?project='+id;},fixtures.project_id);await page.locator('.rg-node').first().waitFor();
  const source=original.workspace_graph.snapshots.find(s=>s.run_id===fixtures.run_ids[1]),snapshot=structuredClone(source),binding={project_id:source.project_id,plan_id:source.plan_id,plan_digest:source.plan_digest,run_id:source.run_id};
  const template=source.nodes.find(n=>n.kind==='role');
  snapshot.nodes=[['a','개발','role'],['b','검사','role'],['c','독립 검수','reviewer']].map(([id,name,kind])=>({...structuredClone(template),...binding,id:'ind-node-'+id,name,kind,source:'fixture',phase:'recorded',status:'RUNNING',handoffs:[]}));
  snapshot.edges=[['parallel-1','a','b','dependency'],['parallel-2','a','b','review_request'],['parallel-3','a','b','planned_dependency'],['reverse','b','a','dependency'],['forward','a','c','review_request'],['self','a','a','handoff'],['return','c','a','revision_return'],['backward','c','b','result_report']].map(([id,from,to,kind])=>({...binding,id:'ind-edge-'+id,from:'ind-node-'+from,to:'ind-node-'+to,kind,source:'fixture',phase:kind==='planned_dependency'?'planned':'recorded',status:'received',title:'독립 '+id,reason:'예시 관계 '+id+'의 고유한 전달 근거',created_at:1700000000,artifact_refs:[{kind:'예시 산출물',id:'artifact-'+id}],reference:{...binding,edge_id:'ind-edge-'+id}}));
  snapshot.history={total:snapshot.edges.length,complete:true};snapshot.warnings=[];injection=structuredClone(original);injection.workspace_graph.snapshots=injection.workspace_graph.snapshots.map(s=>s.id===snapshot.id?snapshot:s);injection.workspace_graph.default_snapshot_id=snapshot.id;injection.workspace_graph.observed_at+=10;injection.workspace_graph.cursor+=10;fingerprint(injection);
  await refresh();await page.locator('#rg-snapshot').selectOption(snapshot.id);
  const routeKinds=await page.locator('.rg-edge').evaluateAll(nodes=>nodes.map(n=>n.dataset.rgRouteKind));assert.ok(['same-row','forward','self','return','backward'].every(kind=>routeKinds.includes(kind)));
  const parallel=await page.locator('.rg-edge').evaluateAll(nodes=>nodes.filter(n=>n.dataset.rgEdgeId.includes('parallel')).map(n=>n.querySelector('.rg-edge-line').getAttribute('d')));assert.equal(new Set(parallel).size,3,'parallel deliveries must have distinct routes');
  for(const width of [1440,390,320])for(const theme of ['light','black']){
   await close();await page.setViewportSize({width,height:width>700?1000:844});await page.locator(`[data-theme-choice="${theme}"]`).click();await settle();await action('fit').click();await page.locator('.rg-viewport').scrollIntoViewIfNeeded();await settle();
   const hitIds=[],labelIds=[];
   for(const edge of snapshot.edges){
    let point=await pointerPoint(edge.id,false);if(point){await page.mouse.click(point.x,point.y);await selection(edge,snapshot);hitIds.push(edge.id);await close();await page.locator('.rg-viewport').scrollIntoViewIfNeeded();}
    // Every rendered label, including a label previously covered by a later
    // edge's wide transparent hit stroke, must resolve to its own relationship.
    if(await page.locator(`.rg-edge-label[data-rg-label="${edge.id}"]`).getAttribute('visibility')!=='hidden'){
     point=await pointerPoint(edge.id,true);if(!point)point=(await revealPointer(edge.id,true))?.point;
     assert.ok(point,`${width}/${theme}: visible label ${edge.id} is covered or inaccessible after keyboard pan`);
     await page.mouse.click(point.x,point.y);await selection(edge,snapshot);labelIds.push(edge.id);await close();await page.locator('.rg-viewport').scrollIntoViewIfNeeded();
    }
    if(!hitIds.includes(edge.id)&&!labelIds.includes(edge.id)){
     const revealed=await revealPointer(edge.id);assert.ok(revealed,`${width}/${theme}: ${edge.id} (${edge.kind}, ${edge.from}→${edge.to}) has no physically selectable path or label after keyboard pan`);
     await page.mouse.click(revealed.point.x,revealed.point.y);await selection(edge,snapshot);(revealed.label?labelIds:hitIds).push(edge.id);await close();await page.locator('.rg-viewport').scrollIntoViewIfNeeded();
    }
   }
   assert.deepEqual([...new Set([...hitIds,...labelIds])].sort(),snapshot.edges.map(e=>e.id).sort(),`${width}/${theme}: every relationship needs a real pointer selection`);
   assert.ok(hitIds.length>=3,`${width}/${theme}: insufficient physically distinguishable paths: ${hitIds}`);
   assert.ok(labelIds.length>=2,`${width}/${theme}: insufficient physically selectable labels: ${labelIds}`);
   if(width===1440)assert.ok(['self','return','reverse','forward'].every(suffix=>hitIds.includes('ind-edge-'+suffix)),`route kinds hidden at desktop: ${hitIds}`);
   await page.locator('.rg-viewport').focus();await page.keyboard.press('Tab');const focused=await page.evaluate(()=>document.activeElement?.dataset.rgEdge);assert.ok(focused,'Tab must reach a real SVG relationship');await page.keyboard.press('Enter');await selection(snapshot.edges.find(e=>e.id===focused),snapshot);await close();await action('fit').click();await page.locator('.rg-viewport').scrollIntoViewIfNeeded();
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await shot(`${theme}-${width}`);checks.push({width,theme,physicalPaths:hitIds,physicalLabels:labelIds,keyboard:focused});
  }
  // A new read arrives during a physical drag. The pointer target must survive,
  // then the latest relationship and diagram link must use the new fingerprint.
  await page.setViewportSize({width:1440,height:1000});await settle();await action('fit').click();await action('pan').click();await page.locator('.rg-viewport').scrollIntoViewIfNeeded();
  const node=page.locator('[data-rg-node="ind-node-a"]'),box=await node.boundingBox();assert.ok(box);
  await page.mouse.move(box.x+box.width/2,box.y+32);await page.mouse.down();await page.mouse.move(box.x+box.width/2+35,box.y+76,{steps:5});
  const held=await node.evaluate(el=>{window.__independentHeldNode=el;return {left:el.style.left,top:el.style.top};});
  const newest=injection.workspace_graph.snapshots.find(s=>s.id===snapshot.id);newest.edges.find(e=>e.id==='ind-edge-return').reason='수정 반환의 최신 예시 근거';injection.workspace_graph.cursor+=1;injection.workspace_graph.observed_at+=10;fingerprint(injection);
  const count=readCount;await page.waitForFunction(()=>window.__independentHeldNode?.isConnected);for(let i=0;readCount===count&&i<180;i++)await new Promise(resolve=>setTimeout(resolve,50));assert.ok(readCount>count,'natural poll must occur while pointer is held');await settle();assert.equal(await page.evaluate(()=>window.__independentHeldNode?.isConnected),true);
  await page.mouse.up();await settle();await page.waitForFunction(fp=>document.querySelector('.rg-export')?.href.includes(fp),newest.fingerprint);
  assert.deepEqual(await node.evaluate(el=>({left:el.style.left,top:el.style.top})),held);
  await action('pan').click();await action('list').click();await page.locator('[data-rg-edge="ind-edge-return"]').click();await page.getByText('수정 반환의 최신 예시 근거',{exact:true}).waitFor();assert.ok((await page.locator('.rg-detail').textContent()).includes(snapshot.run_id));
  assert.deepEqual(violations,[]);assert.ok(writes.every(x=>['/api/login','/api/password'].includes(x)));assert.deepEqual(errors,[]);
  await shot('latest-return-list');
  await fs.writeFile(path.join(out,'graph-edges-independent-validation.json'),JSON.stringify({fixture:true,browser:await browser.version(),sandbox:true,checks,drag:'natural poll preserves pointer, position, latest relation and pinned diagram target',writes},null,2));console.log('PASS: independent graph routing physical labels/paths, keyboard, live drag update');
 }finally{await context.close();await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
