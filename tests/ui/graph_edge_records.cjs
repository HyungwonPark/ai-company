/* Real temporary read API records, no injected graph and no operational/model work. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict'),fs=require('node:fs/promises'),path=require('node:path');
(async()=>{
 const base=process.env.BASE_URL,fixtures=JSON.parse(process.env.GRAPH_FIXTURES),out=process.env.UI_OUTPUT||'/tmp/graph-edge-records';
 assert.match(base,/^http:\/\/127\.0\.0\.1:\d+$/);await fs.mkdir(out,{recursive:true});
 const browser=await chromium.launch({headless:true,chromiumSandbox:true,...(process.env.CHROME_CHANNEL?{channel:process.env.CHROME_CHANNEL}:{}),...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
 const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce',serviceWorkers:'block'}),page=await context.newPage();page.setDefaultTimeout(15000);
 const violations=[],writes=[],errors=[],checks=[],captures=[];let latest=null,currentCase=null;
 const endpoint=`/api/projects/${fixtures.project_id}/overview`;
 page.on('pageerror',error=>errors.push(error.message));
 await context.route('**/*',async route=>{
  const request=route.request(),url=new URL(request.url());
  if(url.origin!==base||request.method()!=='GET'&&!['/api/login','/api/password'].includes(url.pathname)){violations.push(`${request.method()} ${url.pathname}`);return route.abort();}
  if(request.method()!=='GET')writes.push(url.pathname);
  if(url.pathname===endpoint){const response=await route.fetch();if(response.ok())latest=await response.json();return route.fulfill({response});}
  return route.continue();
 });
 const action=name=>page.locator(`[data-rg-action="${name}"]`),wire=id=>page.locator(`.rg-edge-hit[data-rg-edge="${id}"]`);
 const snapshotNodeKind=(snapshot,id)=>snapshot.nodes.find(node=>node.id===id)?.kind;
 const settle=()=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 async function close(){if(await action('clear').count())await action('clear').click();await settle();}
 async function detail(edge,snapshot,graph=true){
  await page.locator('.rg-direction').waitFor();
  const names=new Map(snapshot.nodes.map(node=>[node.id,node.name]));
  assert.equal((await page.locator('.rg-direction').textContent()).trim(),`${names.get(edge.from)} → ${names.get(edge.to)}`);
  assert.ok((await page.locator('.rg-detail').textContent()).includes(edge.reason||edge.title),edge.id+' original reason');
  assert.ok((await page.locator('.rg-detail').textContent()).includes(snapshot.run_id),edge.id+' original run');
  const reference=page.locator('#rg-detail-summary-source').locator('..').locator('pre');
  assert.deepEqual(JSON.parse(await reference.textContent()),edge.reference||edge.source_ref||{},edge.id+' original reference');
  if(graph)assert.equal(await page.locator('.rg-edge.is-selected').getAttribute('data-rg-edge-id'),edge.id);
  else assert.equal(await page.locator('.rg-list [data-rg-edge][aria-pressed="true"]').getAttribute('data-rg-edge'),edge.id);
 }
 async function styleState(id){return page.evaluate(id=>{
  const group=[...document.querySelectorAll('.rg-edge')].find(el=>el.dataset.rgEdgeId===id),line=group.querySelector('.rg-edge-line');
  const label=[...document.querySelectorAll('.rg-edge-label')].find(el=>el.dataset.rgLabel===id),markerId=line.getAttribute('marker-end').match(/#([^)]*)/)[1],marker=document.getElementById(markerId).querySelector('path');
  return {stroke:getComputedStyle(line).stroke,hitStroke:getComputedStyle(group.querySelector('.rg-edge-hit')).stroke,width:Number.parseFloat(getComputedStyle(line).strokeWidth),dash:getComputedStyle(line).strokeDasharray,markerId,markerFill:getComputedStyle(marker).fill,groupOpacity:Number(getComputedStyle(group).opacity),labelOpacity:Number(getComputedStyle(label).opacity),labelSelected:label.classList.contains('is-selected'),selected:group.classList.contains('is-selected'),related:[...document.querySelectorAll('.rg-node.is-related')].map(n=>n.dataset.rgNode).sort(),otherGroups:[...document.querySelectorAll('.rg-edge')].filter(el=>el!==group).map(el=>Number(getComputedStyle(el).opacity)),otherLabels:[...document.querySelectorAll('.rg-edge-label')].filter(el=>el!==label).map(el=>Number(getComputedStyle(el).opacity))};
 },id);}
 async function selectedStyle(edge,baseline){
  const state=await styleState(edge.id);assert.equal(state.markerId,'rg-arrow-selected');assert.equal(state.markerFill,state.stroke);assert.notEqual(state.stroke,baseline.stroke);assert.ok(state.width>baseline.width);assert.ok(state.hitStroke==='transparent'||/^rgba\([^)]*,\s*0\s*\)$/.test(state.hitStroke),'the 44px hit stroke must not paint over the visible path or arrow: '+state.hitStroke);
  assert.equal(state.labelSelected,true);assert.equal(state.groupOpacity,1);assert.equal(state.labelOpacity,1);assert.deepEqual(state.related,[...new Set([edge.from,edge.to])].sort());
  assert.ok(state.otherGroups.every(x=>x>0&&x<1));assert.ok(state.otherLabels.every(x=>x>0&&x<1));assert.equal(state.dash,baseline.dash,'selection preserves planned/recorded line meaning');
 }
 async function restoredStyle(id,baseline){await action('fit').focus();const state=await styleState(id);assert.equal(state.markerId,baseline.markerId);assert.equal(state.markerFill,baseline.markerFill);assert.equal(state.stroke,baseline.stroke);assert.equal(state.width,baseline.width);assert.equal(state.selected,false);assert.equal(state.labelSelected,false);assert.equal(state.groupOpacity,1);assert.equal(state.labelOpacity,1);assert.ok(state.otherGroups.every(x=>x===1));assert.ok(state.otherLabels.every(x=>x===1));assert.deepEqual(state.related,[]);}
 async function centerLabel(id,selected=false){
  await page.locator('.rg-viewport').evaluate(el=>el.scrollIntoView({block:'start'}));
  const move=await page.evaluate(({id,selected})=>{
   const label=[...document.querySelectorAll('.rg-edge-label')].find(n=>n.dataset.rgLabel===id),rect=label.querySelector('rect').getBoundingClientRect(),vp=document.querySelector('.rg-viewport').getBoundingClientRect();
   let target={left:rect.left,right:rect.right,top:rect.top,bottom:rect.bottom};
   if(selected&&innerWidth>700){const parts=[rect,...[...document.querySelectorAll('.rg-node.is-related,.rg-edge.is-selected .rg-edge-line')].map(el=>el.getBoundingClientRect())];target={left:Math.min(...parts.map(r=>r.left)),right:Math.max(...parts.map(r=>r.right)),top:Math.min(...parts.map(r=>r.top)),bottom:Math.max(...parts.map(r=>r.bottom))};}
   const panel=selected&&innerWidth<=700?document.querySelector('.rg-detail.is-open')?.getBoundingClientRect():null;
   const left=Math.max(0,vp.left),right=Math.min(innerWidth,vp.right),top=Math.max(0,vp.top),bottom=Math.min(innerHeight,vp.bottom,panel&&panel.top>top+60?panel.top:Infinity);
   return {x:(left+right)/2-(target.left+target.right)/2,y:(top+bottom)/2-(target.top+target.bottom)/2};
  },{id,selected});
  await wire(id).evaluate(el=>el.focus({preventScroll:true}));
  for(const [delta,negative,positive] of [[move.x,'ArrowLeft','ArrowRight'],[move.y,'ArrowUp','ArrowDown']]){const count=Math.round(Math.abs(delta)/24);assert.ok(count<=180,'bounded graph pan');for(let i=0;i<count;i++)await page.keyboard.press(delta<0?negative:positive);}
  await settle();
 }
 async function clickLabel(id){
  await centerLabel(id);
  const point=await page.evaluate(id=>{const label=[...document.querySelectorAll('.rg-edge-label')].find(n=>n.dataset.rgLabel===id),r=label.querySelector('rect').getBoundingClientRect();const x=(r.left+r.right)/2,y=(r.top+r.bottom)/2;return {x,y,visible:label.getAttribute('visibility')!=='hidden',hit:document.elementFromPoint(x,y)?.closest('[data-rg-label]')?.dataset.rgLabel};},id);
  assert.equal(point.visible,true,id+' missing label');assert.equal(point.hit,id,id+' label intercepted');await page.mouse.click(point.x,point.y);
 }
 async function fitContainsAll(snapshot){
  await action('fit').click();await settle();
  const result=await page.evaluate(()=>{
   const vp=document.querySelector('.rg-viewport').getBoundingClientRect(),outside=[];
   for(const el of document.querySelectorAll('.rg-node,.rg-edge-line,.rg-edge-label rect')){if(el.closest('.rg-edge-label')?.getAttribute('visibility')==='hidden')continue;const r=el.getBoundingClientRect();if(r.left<vp.left-1||r.right>vp.right+1||r.top<vp.top-1||r.bottom>vp.bottom+1)outside.push({kind:el.getAttribute('class')||el.tagName,left:r.left-vp.left,right:r.right-vp.right,top:r.top-vp.top,bottom:r.bottom-vp.bottom});}
   return {outside,edges:document.querySelectorAll('.rg-edge').length,labels:document.querySelectorAll('.rg-edge-label').length};
  });assert.deepEqual(result.outside,[],snapshot.run_id+' fit must include external routes and labels');assert.equal(result.edges,snapshot.edges.length);assert.equal(result.labels,snapshot.edges.length);
 }
 try{
  await page.goto(base+'/?workspace=integrated#projects');await page.getByLabel('비밀번호',{exact:true}).fill(process.env.TEST_PASSWORD);await page.getByRole('button',{name:'로그인',exact:true}).click();
  await page.getByLabel('현재 비밀번호',{exact:true}).fill(process.env.TEST_PASSWORD);await page.getByLabel('새 비밀번호',{exact:true}).fill('graph-records-only-password');await page.getByLabel('새 비밀번호 확인',{exact:true}).fill('graph-records-only-password');await page.getByRole('button',{name:'비밀번호 변경 후 계속'}).click();await page.locator('.nav').waitFor();
  await page.evaluate(id=>{location.hash='#progress?project='+id;},fixtures.project_id);await page.locator('.rg-node').first().waitFor();
  const before=structuredClone(latest),snapshots=fixtures.run_ids.map(id=>before.workspace_graph.snapshots.find(s=>s.run_id===id));assert.deepEqual(snapshots.map(s=>s.edges.length),[8,9]);
  assert.equal(snapshots[1].edges.filter(e=>e.kind==='handoff'&&e.from===e.to).length,2);
  assert.equal(snapshots[0].edges.filter(e=>snapshotNodeKind(snapshots[0],e.from)==='role'&&snapshotNodeKind(snapshots[0],e.to)==='pm').length,2);
  for(const width of [1440,390,320])for(const theme of ['light','black'])for(const [runIndex,snapshot] of snapshots.entries()){
   currentCase={width,theme,stage:'snapshot setup',snapshot:{id:snapshot.id,project_id:snapshot.project_id,plan_id:snapshot.plan_id,plan_digest:snapshot.plan_digest,run_id:snapshot.run_id,source:snapshot.source,nodes:snapshot.nodes.map(n=>({id:n.id,name:n.name,kind:n.kind,phase:n.phase,status:n.status})),edges:snapshot.edges.map(e=>({id:e.id,from:e.from,to:e.to,kind:e.kind,phase:e.phase,status:e.status}))}};
   await close();await page.setViewportSize({width,height:width>700?1000:844});await page.locator(`[data-theme-choice="${theme}"]`).click();await page.locator('#rg-snapshot').selectOption(snapshot.id);if(await action('graph').getAttribute('aria-pressed')!=='true')await action('graph').click();await settle();await fitContainsAll(snapshot);
   const selectedIds=[];
   for(const [index,edge] of snapshot.edges.entries()){
    currentCase={...currentCase,stage:'physical relation selection',edge:{id:edge.id,from:edge.from,to:edge.to,kind:edge.kind,index}};
    await close();await action('fit').click();await settle();const baseline=await styleState(edge.id);
    await clickLabel(edge.id);await detail(edge,snapshot);await selectedStyle(edge,baseline);selectedIds.push(edge.id);
    // The same edge is reachable by real keyboard activation and the list,
    // with its original source object and run binding intact in all three views.
    currentCase.stage='keyboard selection and emphasis';await close();await restoredStyle(edge.id,baseline);await wire(edge.id).evaluate(el=>el.focus({preventScroll:true}));await page.keyboard.press('Enter');await detail(edge,snapshot);await selectedStyle(edge,baseline);
    const to=snapshot.nodes.find(n=>n.id===edge.to),capture=edge.kind==='handoff'||edge.kind==='revision_return'||edge.kind==='result_report'&&(to.kind==='pm'||to.kind==='check');
    if(capture){
     currentCase.stage='enlarged selection capture';
     for(let n=0;n<7&&Number.parseInt(await page.locator('.rg-zoom').textContent(),10)<90;n++)await action('zoom-in').click();
     await centerLabel(edge.id,true);await detail(edge,snapshot);await selectedStyle(edge,baseline);await page.evaluate(()=>document.fonts.ready);
     const filename=`graph-record-${runIndex?'recent':'previous'}-${theme}-${width}-${edge.kind}-${index}.png`;await page.screenshot({path:path.join(out,filename),fullPage:false});captures.push({filename,edge_id:edge.id,run_id:snapshot.run_id,from:edge.from,to:edge.to,kind:edge.kind,zoom:await page.locator('.rg-zoom').textContent(),framing:width>700?'selected endpoints, path and label bounds':'enlarged relationship segment and readable detail; use fit for full overview'});
    }
    currentCase.stage='list record comparison';await close();await action('list').click();await page.locator(`.rg-list [data-rg-edge="${edge.id}"]`).click();await detail(edge,snapshot,false);
    assert.equal(await page.locator('.rg-list [data-rg-edge]').count(),snapshot.edges.length);await close();await action('graph').click();await restoredStyle(edge.id,baseline);
   }
   assert.deepEqual(selectedIds,snapshot.edges.map(e=>e.id));assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));checks.push({width,theme,run_id:snapshot.run_id,edge_ids:selectedIds,physical:'label centers',keyboard:'SVG Enter',list:'same original reference',selection:'path/arrow/label/endpoints and dim restore'});
  }
  // Browser-native touch delivery through CDP, never DOM-dispatched pointer events.
  await close();await page.setViewportSize({width:390,height:844});await page.locator('#rg-snapshot').selectOption(snapshots[1].id);await action('fit').click();await action('pan').click();await page.locator('.rg-viewport').evaluate(el=>el.scrollIntoView({block:'start'}));await settle();
  const touchNode=snapshots[1].nodes.find(n=>n.kind==='role'),touchEdge=snapshots[1].edges.find(e=>e.from===touchNode.id&&e.kind==='handoff');assert.ok(touchEdge);
  currentCase={...currentCase,width:390,stage:'native touch synchronization',edge:{id:touchEdge.id,from:touchEdge.from,to:touchEdge.to,kind:touchEdge.kind}};
  const geometry=()=>page.evaluate(({nodeId,edgeId})=>{
   const node=[...document.querySelectorAll('.rg-node')].find(n=>n.dataset.rgNode===nodeId),line=[...document.querySelectorAll('.rg-edge')].find(n=>n.dataset.rgEdgeId===edgeId).querySelector('.rg-edge-line'),label=[...document.querySelectorAll('.rg-edge-label')].find(n=>n.dataset.rgLabel===edgeId);
   const rect=label.querySelector('rect'),text=label.querySelector('text'),leader=label.querySelector('line'),number=(el,key)=>Number(el.getAttribute(key));
   const anchor={x:number(leader,'x1'),y:number(leader,'y1')},length=line.getTotalLength();let anchorDistance=Infinity;
   // Sample the rendered SVG curve, not a copy of the routing implementation.
   for(let at=0;at<=length;at+=.25){const point=line.getPointAtLength(at);anchorDistance=Math.min(anchorDistance,Math.hypot(point.x-anchor.x,point.y-anchor.y));}
   const end=line.getPointAtLength(length);anchorDistance=Math.min(anchorDistance,Math.hypot(end.x-anchor.x,end.y-anchor.y));
   return {left:node.style.left,top:node.style.top,path:line.getAttribute('d'),hit:line.nextElementSibling.getAttribute('d'),label:rect.outerHTML,leader:leader.outerHTML,
    alignment:{textX:number(text,'x'),textY:number(text,'y'),centerX:number(rect,'x')+number(rect,'width')/2,centerY:number(rect,'y')+number(rect,'height')/2,leaderX:number(leader,'x2'),leaderY:number(leader,'y2'),anchorDistance,width:number(rect,'width'),height:number(rect,'height'),text:text.textContent,visible:[label,rect,text,leader,line].every(el=>{const style=getComputedStyle(el);return style.visibility==='visible'&&style.display!=='none'&&Number(style.opacity)>0;})}};
  },{nodeId:touchNode.id,edgeId:touchEdge.id});
  const aligned=geometry=>{
   const a=geometry.alignment;assert.ok(a.visible&&a.width>0&&a.height>0&&a.text.trim(),'rendered relationship label stays visible');
   for(const [value,expected]of [[a.textX,a.centerX],[a.textY,a.centerY],[a.leaderX,a.textX],[a.leaderY,a.textY]])assert.ok(Math.abs(value-expected)<.01,'text, rectangle and leader endpoint stay aligned');
   // An anchor on the original orthogonal corner may be up to 2.83 units
   // from its rounded (8-unit quadratic) SVG bend; sampling adds at most .125.
   assert.ok(a.anchorDistance<=3,'leader anchor stays on its rendered relationship path');
  };
  const beforeTouch=await geometry();aligned(beforeTouch);const nodeRect=await page.locator(`[data-rg-node="${touchNode.id}"]`).boundingBox();assert.ok(nodeRect);const x=nodeRect.x+nodeRect.width/2,y=nodeRect.y+nodeRect.height/2;assert.ok(x>0&&x<390&&y>0&&y<844);
  const cdp=await context.newCDPSession(page);await cdp.send('Emulation.setTouchEmulationEnabled',{enabled:true,maxTouchPoints:1});
  await page.evaluate(()=>{window.__recordTouchTypes=[];window.__recordTouchMove=null;document.addEventListener('pointerdown',event=>window.__recordTouchTypes.push(event.pointerType),{once:true});document.addEventListener('pointermove',event=>{if(event.pointerType==='touch')window.__recordTouchMove={pointerType:event.pointerType,clientX:event.clientX,clientY:event.clientY};});});
  await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y,id:1}]});
  for(let step=1;step<=5;step++)await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x,y:y+step*4,id:1}]});
  // CDP acknowledgement can precede the browser's last input frame. This
  // document listener runs after the graph's bubbling pointermove handler,
  // so observing the final coordinates also establishes completed DOM updates.
  await page.waitForFunction(({x,y})=>window.__recordTouchMove?.pointerType==='touch'&&Math.abs(window.__recordTouchMove.clientX-x)<.1&&Math.abs(window.__recordTouchMove.clientY-y)<.1,{x,y:y+20});
  const duringTouch=await geometry();assert.notEqual(duringTouch.path,beforeTouch.path);assert.equal(duringTouch.path,duringTouch.hit);assert.notDeepEqual([duringTouch.left,duringTouch.top],[beforeTouch.left,beforeTouch.top],'native touch moves the actual node');aligned(duringTouch);
  await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});await settle();assert.deepEqual(await page.evaluate(()=>window.__recordTouchTypes),['touch']);const afterTouch=await geometry();aligned(afterTouch);assert.deepEqual(afterTouch,duringTouch);await cdp.send('Emulation.setTouchEmulationEnabled',{enabled:false});await cdp.detach();
  await action('pan').click();await clickLabel(touchEdge.id);await detail(touchEdge,snapshots[1]);await fs.writeFile(path.join(out,'graph-edge-touch-validation.json'),JSON.stringify({browser:'CDP Input.dispatchTouchEvent',pointerType:'touch',node_id:touchNode.id,edge_id:touchEdge.id,before:beforeTouch,during:duringTouch,after:afterTouch},null,2));
  const after=await page.evaluate(async endpoint=>(await fetch(endpoint)).json(),endpoint);assert.deepEqual(after.runs,before.runs);assert.deepEqual(after.approvals,before.approvals);assert.deepEqual(after.plans,before.plans);assert.deepEqual(violations,[]);assert.deepEqual(errors,[]);assert.ok(writes.every(p=>['/api/login','/api/password'].includes(p)));
  await fs.writeFile(path.join(out,'graph-edge-records-validation.json'),JSON.stringify({source:'real ephemeral fixture API; no graph injection/model/production work',browser:await browser.version(),sandbox:true,checks,captures,writes},null,2));console.log('PASS: all 17 real fixture relationships across six viewports, exact record selection and emphasis restoration');
 }catch(error){
  // Only this temporary fixture's public graph facts are retained. Never persist
  // cookies, request headers, environment variables, credentials or auth forms.
  const diagnostic={source:'ephemeral fixture API only',case:currentCase,error:{name:error.name,message:error.message},completedCases:checks};
  if(currentCase?.snapshot){
   try{diagnostic.render=await page.evaluate(()=>({viewport:{width:innerWidth,height:innerHeight},theme:document.documentElement.dataset.theme,graph:document.querySelector('[data-rg-root]')?.dataset.snapshot,zoom:document.querySelector('.rg-zoom')?.textContent,nodes:[...document.querySelectorAll('.rg-node')].map(el=>({id:el.dataset.rgNode,left:el.style.left,top:el.style.top,width:el.style.width,height:el.style.height})),routes:[...document.querySelectorAll('.rg-edge')].map(el=>({id:el.dataset.rgEdgeId,kind:el.dataset.rgRouteKind,path:el.querySelector('.rg-edge-line')?.getAttribute('d')})),labels:[...document.querySelectorAll('.rg-edge-label')].map(el=>({id:el.dataset.rgLabel,visibility:el.getAttribute('visibility'),rect:el.querySelector('rect')?.outerHTML,text:el.querySelector('text')?.textContent}))}));
    const svg=await page.locator('.rg-lines').first().evaluate(el=>el.outerHTML).catch(()=>null);if(svg)await fs.writeFile(path.join(out,'graph-edge-records-failure.svg'),svg);
    await page.screenshot({path:path.join(out,'graph-edge-records-failure.png'),fullPage:true});
   }catch(captureError){diagnostic.captureError=captureError.message;}
  }
  await fs.writeFile(path.join(out,'graph-edge-records-failure.json'),JSON.stringify(diagnostic,null,2));throw error;
 }finally{await context.close();await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
