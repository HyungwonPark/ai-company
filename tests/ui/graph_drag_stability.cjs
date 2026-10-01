/* Real pointer movement, unmodified recent fixture and default graphLayout. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const fs=require('node:fs/promises');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
(async()=>{
 const out=process.env.UI_OUTPUT||'/tmp/graph-drag-stability';await fs.mkdir(out,{recursive:true});
 const file=path.join(out,'AI-Company-drag.html'),before=path.join(out,'AI-Company-drag-before.html');
 execFileSync('python3',['scripts/package_graph_preview.py',file]);
 const renderer=await fs.readFile('src/ai_company/web/workspace-graph-ui.js','utf8');
 const baseline=execFileSync('git',['show','0655de2:src/ai_company/web/workspace-graph-ui.js'],{encoding:'utf8'});
 await fs.writeFile(before,(await fs.readFile(file,'utf8')).replace(renderer.replaceAll('export function ','function '),baseline.replaceAll('export function ','function ')));
 const browser=await chromium.launch({headless:true,chromiumSandbox:true,...(process.env.CHROME_CHANNEL?{channel:process.env.CHROME_CHANNEL}:{}),...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
 const results=[],errors=[],requests=[];let activePage=null,diagnostic=null;
 try{
  for(const version of ['before','after'])for(const theme of ['light','black'])for(const width of [320,390,1440]){
   const context=await browser.newContext({viewport:{width,height:1100},reducedMotion:'reduce'}),page=await context.newPage();activePage=page;diagnostic={version,theme,width};
   page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(/^https?:/.test(r.url()))requests.push(r.url());});
   await page.goto(pathToFileURL(version==='before'?before:file).href);await page.locator('.rg-node').first().waitFor();await page.evaluate(()=>document.fonts.ready);
   if(theme==='black')await page.locator('#theme').click();
   const options=await page.locator('#rg-snapshot option').evaluateAll(xs=>xs.map(x=>x.value));await page.locator('#rg-snapshot').selectOption(options.at(-1));
   await page.locator('[data-rg-action=pan]').click();
   const ids=await page.evaluate(()=>{const s=window.GRAPH_FIXTURE.workspace_graph.snapshots.at(-1),pm=s.nodes.find(n=>n.kind==='pm'),edge=s.edges.find(e=>e.from===pm.id&&e.kind==='specification'&&s.nodes.find(n=>n.id===e.to).name.includes('검사'));return {pm:pm.id,edge:edge.id,run:s.run_id,records:JSON.stringify(window.GRAPH_FIXTURE)};});
   const node=page.locator('.rg-node').filter({has:page.locator('.rg-node-kind',{hasText:/^PM$/})});
   await node.scrollIntoViewIfNeeded();
   const box=await node.boundingBox(),start={x:box.x+box.width/2,y:box.y+35};
   const initial=await node.evaluate(n=>({x:parseFloat(n.style.left),y:parseFloat(n.style.top),width:parseFloat(n.style.width)})),zoom=box.width/initial.width;
   if(width===1440)assert.equal(initial.x,135,'exact default wide PM placement');
   const read=()=>page.evaluate(({pm,edge})=>{
    const n=[...document.querySelectorAll('.rg-node')].find(n=>n.dataset.rgNode===pm),g=[...document.querySelectorAll('[data-rg-edge-id]')].find(g=>g.dataset.rgEdgeId===edge),l=[...document.querySelectorAll('[data-rg-label]')].find(g=>g.dataset.rgLabel===edge);
    return {nodeX:parseFloat(n.style.left),nodeY:parseFloat(n.style.top),path:g.querySelector('.rg-edge-line').getAttribute('d'),hit:g.querySelector('.rg-edge-hit').getAttribute('d'),label:{x:+l.querySelector('text').getAttribute('x'),y:+l.querySelector('text').getAttribute('y')},pointer:window.dragPointer};
   },ids);
   await page.evaluate(()=>{document.addEventListener('pointermove',e=>{window.dragPointer={x:e.clientX,y:e.clientY,isTrusted:e.isTrusted,type:e.pointerType};});});
   await page.mouse.move(start.x,start.y);await page.mouse.down();
   const samples=[];
   for(const offset of [8,9,8,9,8,9,8,9]){
    const x=start.x+offset*zoom;
    await page.mouse.move(x,start.y);
    await page.waitForFunction(({x,y})=>Math.abs(window.dragPointer.x-x)<.02&&Math.abs(window.dragPointer.y-y)<.02,{x,y:start.y});
    const sample=await read();diagnostic={version,theme,width,offset,initial,zoom,start,sample};assert.ok(sample.pointer.isTrusted&&sample.pointer.type==='mouse');assert.ok(Math.abs(sample.nodeX-initial.x-offset)<.02,'real one-pixel node movement');assert.equal(sample.nodeY,initial.y);assert.equal(sample.path,sample.hit);
    samples.push({offset,...sample});
    if(samples.length<=2)await page.screenshot({path:path.join(out,`workspace-drag-${version}-${theme}-${width}-plus${offset}.png`),fullPage:true});
   }
   await page.mouse.up();await page.waitForTimeout(100);const released=await read();
   if(version==='after'){
    assert.equal(released.path,samples.at(-1).path,'release cannot reroute');assert.deepEqual(released.label,samples.at(-1).label);
    for(let i=1;i<samples.length;i++){
     const a=samples[i-1],b=samples[i],aa=a.path.match(/-?\d+(?:\.\d+)?/g).map(Number),bb=b.path.match(/-?\d+(?:\.\d+)?/g).map(Number);
     assert.equal(aa.length,bb.length,'same corridor and curve topology');assert.ok(Math.max(...aa.map((n,j)=>Math.abs(n-bb[j])))<=1.02,'route moves at most 1px');
     assert.ok(Math.hypot(a.label.x-b.label.x,a.label.y-b.label.y)<=1.02,'name moves at most 1px');
    }
    // A redraw and a real label click keep the same geometry and exact record.
    await page.locator('[data-rg-action=pan]').click();
    const target=page.locator(`[data-rg-label="${ids.edge}"]`);await target.click();
    await page.locator('.rg-detail.is-open').waitFor();assert.equal(await page.locator('.rg-edge.is-selected').getAttribute('data-rg-edge-id'),ids.edge);assert.ok((await page.locator('.rg-detail').textContent()).includes(ids.run));
    assert.equal((await read()).path,released.path);
   }else if(width===1440){
    assert.ok(Math.hypot(samples[0].label.x-samples[1].label.x,samples[0].label.y-samples[1].label.y)>100,'baseline reproduces reported label jump');
    assert.ok(samples[0].path.includes('194')&&samples[1].path.includes('274'),'baseline reproduces corridor jump');
   }
   assert.equal(await page.evaluate(()=>JSON.stringify(window.GRAPH_FIXTURE)),ids.records,'read-only gesture preserves records');
   results.push({version,theme,width,zoom,run:ids.run,edge:ids.edge,samples,released});await context.close();
  }
  assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);
  await fs.writeFile(path.join(out,'workspace-drag-stability-validation.json'),JSON.stringify({status:'PASS',baseline:'0655de2',browser:browser.version(),sandbox:true,source:'offline recent fixture; trusted Playwright mouse input; no production/model/API calls',results},null,2));
  console.log('PASS: baseline jump reproduced; Light/Black 320/390/1440 trusted +8/+9 mouse reversals, release, redraw and exact label selection');
 }catch(error){await fs.writeFile(path.join(out,'workspace-drag-failure-validation.json'),JSON.stringify({diagnostic,error:error.message},null,2));if(activePage&&!activePage.isClosed())await activePage.screenshot({path:path.join(out,'workspace-drag-failure.png'),fullPage:true});throw error;}finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
