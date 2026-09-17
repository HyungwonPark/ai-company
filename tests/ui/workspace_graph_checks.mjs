import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const src=await readFile(new URL('../../src/ai_company/web/workspace-graph-ui.js',import.meta.url),'utf8');
const {createWorkspaceGraph,validateGraphEnvelope,graphKey,graphLayout,placeGraphNodes,edgeGeometry}=await import('data:text/javascript;base64,'+Buffer.from(src).toString('base64'));
const binding={project_id:'p',plan_id:'plan',plan_digest:'a'.repeat(64),run_id:'run'};
const s={id:'snap',...binding,source:'fixture',mode:'execution',nodes:[{id:'a',...binding,kind:'role',name:'개발'},{id:'b',...binding,kind:'role',name:'검사'}],edges:[{id:'e',...binding,from:'a',to:'b',phase:'recorded'}]};
const envelope={schema_version:1,project_id:'p',cursor:1,observed_at:10,default_snapshot_id:'snap',snapshots:[s]};
assert.ok(validateGraphEnvelope(envelope,'p'));assert.equal(validateGraphEnvelope(envelope,'other'),false);
assert.equal(validateGraphEnvelope({...envelope,snapshots:[{...s,nodes:[{...s.nodes[0],run_id:'foreign'}]}]},'p'),false);
const graph=createWorkspaceGraph({esc:String});assert.ok(graph.ingest({workspace_graph:envelope},'p'));
assert.equal(graph.ingest({workspace_graph:{...envelope,cursor:0}},'p'),false);
assert.equal(graph.ingest({workspace_graph:{...envelope,observed_at:9}},'p'),false);
assert.ok(graph.ingest({workspace_graph:{...envelope,observed_at:11,snapshots:[{...s,edges:[...s.edges,...s.edges]}]}},'p'));
assert.notEqual(graphKey(s),graphKey({...s,run_id:'other'}));
const layout=graphLayout(s.nodes,true);assert.equal(layout.positions.a.y,layout.positions.b.y);assert.notEqual(layout.positions.a.x,layout.positions.b.x);
assert.deepEqual(graphLayout(s.nodes,true),graphLayout([...s.nodes].reverse(),true));
console.log('PASS: graph project/run binding, stale cursor/time rejection, duplicate inputs and stable parallel placement');

const oldNodes=[{id:'b',kind:'document'},{id:'d',kind:'document'}];
const before=graphLayout(oldNodes);const nextNodes=[{id:'a',kind:'document'},...oldNodes];
const after=placeGraphNodes(nextNodes,graphLayout(nextNodes),before.positions);
assert.deepEqual(after.b,before.positions.b);assert.deepEqual(after.d,before.positions.d);
assert.notDeepEqual(after.a,after.b);assert.notDeepEqual(after.a,after.d);
console.log('PASS: later-arriving nodes cannot overlap retained node positions');

// Crossing a role vertically must not move the edge to the opposite node port.
const coordinates=path=>path.match(/-?\d+(?:\.\d+)?/g).map(Number);
for(const kind of ['dependency','handoff','revision_return']){
 const edge={from:'from',to:'to',kind};
 const route=y=>coordinates(edgeGeometry(edge,{from:{x:0,y:100},to:{x:270,y}},206,156).path);
 const above=route(99),level=route(100),below=route(101);
 for(const next of [level,below]){
  assert.deepEqual(next.slice(0,2),above.slice(0,2),`${kind}: source port stays fixed`);
  assert.equal(next.at(-2),above.at(-2),`${kind}: target horizontal port stays fixed`);
  assert.ok(Math.abs(next.at(-1)-above.at(-1))<=2,`${kind}: target follows only the two-pixel node movement`);
  assert.ok(next.every((value,index)=>Math.abs(value-above[index])<=3),`${kind}: curve changes continuously at equal height`);
 }
}
assert.equal(graph.isInteracting('p'),false);
console.log('PASS: edge ports and curve remain continuous when connected roles cross vertically');
