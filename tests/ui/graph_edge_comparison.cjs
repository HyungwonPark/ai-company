/* Same sanitized records, viewport and camera controls for the integration baseline and candidate. */
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const fs=require('node:fs/promises');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
(async()=>{
 const out=process.env.UI_OUTPUT||'/tmp/ai-company-edges';await fs.mkdir(out,{recursive:true});
 const after=path.join(out,'AI-Company-edges.html'),before=path.join(out,'AI-Company-edges-before.html');
 execFileSync('python3',['scripts/package_graph_preview.py',after]);
 const renderer=await fs.readFile('src/ai_company/web/workspace-graph-ui.js','utf8');
 const comparisonBaseline=process.env.GRAPH_COMPARE_BASELINE||'1b50332';
 const baseline=execFileSync('git',['show',comparisonBaseline+':src/ai_company/web/workspace-graph-ui.js'],{encoding:'utf8'});
 const html=await fs.readFile(after,'utf8');assert.ok(html.includes(renderer.replaceAll('export function ','function ')));
 let beforeHTML=html.replace(renderer.replaceAll('export function ','function '),baseline.replaceAll('export function ','function '));
 if(process.env.GRAPH_COMPARE_BASELINE){const css=await fs.readFile('src/ai_company/web/workspace-graph.css','utf8');assert.ok(beforeHTML.includes(css));beforeHTML=beforeHTML.replace(css,execFileSync('git',['show',comparisonBaseline+':src/ai_company/web/workspace-graph.css'],{encoding:'utf8'}));}
 await fs.writeFile(before,beforeHTML);
 const browser=await chromium.launch({headless:true,chromiumSandbox:true,...(process.env.CHROME_CHANNEL?{channel:process.env.CHROME_CHANNEL}:{}),...(process.env.CHROMIUM_EXECUTABLE?{executablePath:process.env.CHROMIUM_EXECUTABLE}:{})});
 const records=[],errors=[],requests=[];
 try{for(const theme of ['light','black'])for(const width of [320,390,1440])for(const run of ['recent','previous'])for(const [version,file]of [['before',before],['after',after]]){
  const context=await browser.newContext({viewport:{width,height:width>700?1100:900},reducedMotion:'reduce'}),page=await context.newPage();
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(/^https?:/.test(r.url()))requests.push(r.url());});
  await page.goto(pathToFileURL(file).href);await page.locator('.rg-node').first().waitFor();await page.evaluate(()=>document.fonts.ready);
  if(theme==='black')await page.locator('#theme').click();
  const options=await page.locator('#rg-snapshot option').evaluateAll(xs=>xs.map(x=>x.value));await page.locator('#rg-snapshot').selectOption(options.at(run==='recent'?-1:-2));
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'no horizontal page overflow');
  await page.screenshot({path:path.join(out,`workspace-edges-${version}-${run}-${theme}-${width}.png`),fullPage:true});
  await page.locator('[data-rg-action=fit]').click();
  await page.screenshot({path:path.join(out,`workspace-edges-${version}-${run}-fit-${theme}-${width}.png`),fullPage:true});
  const ids=await page.locator('.rg-edge-hit').evaluateAll(xs=>xs.map(x=>x.dataset.rgEdge).sort());
  records.push({version,run,theme,width,ids});await context.close();
 }
 for(let i=0;i<records.length;i+=2)assert.deepEqual(records[i].ids,records[i+1].ids,'same relationship records in paired images');
 assert.deepEqual(errors,[]);assert.deepEqual(requests,[]);
 await fs.writeFile(path.join(out,'workspace-edges-comparison-validation.json'),JSON.stringify({status:'PASS',baseline:comparisonBaseline,css_baseline:process.env.GRAPH_COMPARE_BASELINE?comparisonBaseline:'candidate',source:'sanitized offline fixture; no model or production calls',records},null,2));
 console.log('PASS: paired before/after screenshots at Light/Black 320/390/1440; identical relations; offline preview');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
