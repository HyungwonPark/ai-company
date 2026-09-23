import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
const src=await readFile(new URL('../../src/ai_company/web/workspace-graph-ui.js',import.meta.url),'utf8');
const {routeGraphEdges,graphLayout,nearestGraphEdge}=await import('data:text/javascript;base64,'+Buffer.from(src).toString('base64'));
const fixture=JSON.parse(await readFile(new URL('../../src/ai_company/web/graph-preview/fixture.json',import.meta.url),'utf8'));
const overlaps=(a,b)=>a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top;
let covered=0;
for(const small of [true,false])for(const snapshot of fixture.workspace_graph.snapshots){
 const l=graphLayout(snapshot.nodes,small),original=JSON.stringify([snapshot,l.positions]);
 const result=routeGraphEdges(snapshot.edges,l.positions,l.nodeWidth,l.nodeHeight);
 assert.equal(result.routes.size,snapshot.edges.length);
 assert.deepEqual(result,routeGraphEdges([...snapshot.edges].reverse(),l.positions,l.nodeWidth,l.nodeHeight));
 const labels=[];
 for(const [id,r] of result.routes){
  assert.equal(r.issue,null,id);assert.ok(r.label,id+' has a readable label');
  assert.ok(!labels.some(b=>overlaps(r.label,b)),id+' label avoids other names');labels.push(r.label);
  for(const other of result.routes.values()){
   const b=other.target,side=other.toSide,arrow={left:b.x-(side==='left'?24:side==='right'?4:10),right:b.x+(side==='right'?24:side==='left'?4:10),top:b.y-(side==='top'?24:side==='bottom'?4:10),bottom:b.y+(side==='bottom'?24:side==='top'?4:10)};
   assert.ok(!overlaps(r.label,arrow),id+' label preserves arrow direction');
  }
  for(const [node,p]of Object.entries(l.positions)){
   const box={left:p.x,top:p.y,right:p.x+l.nodeWidth,bottom:p.y+l.nodeHeight};
   assert.ok(!overlaps(r.label,box),id+' label avoids '+node);
   for(let i=1;i<r.points.length;i++){
    const a=r.points[i-1],b=r.points[i];assert.ok(a.x===b.x||a.y===b.y);
    const hit=a.x===b.x?a.x>box.left&&a.x<box.right&&Math.max(a.y,b.y)>box.top&&Math.min(a.y,b.y)<box.bottom:a.y>box.top&&a.y<box.bottom&&Math.max(a.x,b.x)>box.left&&Math.min(a.x,b.x)<box.right;
    assert.ok(!hit,id+' route avoids role '+node);
   }
  }
  covered++;
 }
 assert.equal(JSON.stringify([snapshot,l.positions]),original,'routing cannot mutate records or positions');
}
const positions={a:{x:0,y:100},b:{x:270,y:100},c:{x:0,y:500}};
const edges=[...Array.from({length:3},(_,i)=>({id:'parallel'+i,from:'a',to:'c',kind:'dependency'})),{id:'same',from:'a',to:'b',kind:'dependency'},{id:'self',from:'a',to:'a',kind:'handoff'},{id:'return',from:'c',to:'a',kind:'revision_return'}];
const {routes}=routeGraphEdges(edges,positions,206,156);
assert.equal(new Set(edges.slice(0,3).map(e=>JSON.stringify(routes.get(e.id).source))).size,3,'parallel source ports separated');
assert.equal(new Set(edges.slice(0,3).map(e=>JSON.stringify(routes.get(e.id).target))).size,3,'parallel target ports separated');
assert.equal(routes.get('same').type,'same-row');
assert.equal(routes.get('self').type,'self');assert.notDeepEqual(routes.get('self').source,routes.get('self').target);
assert.ok(routes.get('self').points.every(p=>p.x>=-54&&p.x<=244&&p.y>=100&&p.y<=256),'self handoff stays beside its own role');
assert.equal(routes.get('return').type,'return');assert.equal(routes.get('return').outerX,null,'return uses the nearest clear route, without a whole-graph detour');
const moved=routeGraphEdges(edges,{...positions,b:{x:270,y:800}},206,156,{basePositions:positions});
assert.equal(moved.routes.get('same').fromSide,routes.get('same').fromSide);
const near=new Map([['one',{points:[{x:0,y:0},{x:100,y:0}]}],['two',{points:[{x:0,y:10},{x:100,y:10}]}]]);
assert.equal(nearestGraphEdge(near,{x:40,y:1}).id,'one');assert.equal(nearestGraphEdge(near,{x:40,y:9}).id,'two');
console.log(`PASS: ${covered} stored relations, labels/role avoidance, stable ports, self/return/same-row, deterministic routing, input preservation and nearest stroke selection`);
for(const small of [true,false]){
 const nodes=[{id:'a',kind:'role'},{id:'b',kind:'role'},{id:'c',kind:'reviewer'}],layout=graphLayout(nodes,small);
 const repeated=[...Array.from({length:3},(_,i)=>({id:'parallel'+i,from:'a',to:'b',kind:'dependency'})),{id:'reverse',from:'b',to:'a',kind:'dependency'},...edges.filter(e=>!e.id.startsWith('parallel')&&e.id!=='same')];
 const routed=routeGraphEdges(repeated,layout.positions,layout.nodeWidth,layout.nodeHeight),labels=[];
 for(const [id,r]of routed.routes){assert.equal(r.issue,null);assert.ok(r.label,id+' duplicate has label');assert.ok(!labels.some(l=>overlaps(l,r.label)),id+' duplicate labels separated');labels.push(r.label);
  for(const p of Object.values(layout.positions))assert.ok(!overlaps(r.label,{left:p.x,top:p.y,right:p.x+layout.nodeWidth,bottom:p.y+layout.nodeHeight}));
  for(const other of routed.routes.values()){const b=other.target,side=other.toSide;assert.ok(!overlaps(r.label,{left:b.x-(side==='left'?24:side==='right'?4:10),right:b.x+(side==='right'?24:side==='left'?4:10),top:b.y-(side==='top'?24:side==='bottom'?4:10),bottom:b.y+(side==='bottom'?24:side==='top'?4:10)}));}
 }
}
console.log('PASS: duplicate same-row labels avoid each other, roles and arrowheads at both widths');

// Persisted IDs are generated afresh. Their ordering must not starve the short
// same-row label after flexible report/return labels consume the free space.
let varied=0;
for(let seed=0;seed<80;seed++)for(const small of [true,false])for(const snapshot of fixture.workspace_graph.snapshots){
 const id=value=>createHash('sha256').update(seed+':'+value).digest('hex');
 const nodes=snapshot.nodes.map(n=>({...n,id:id(n.id)})),edges=snapshot.edges.map(e=>({...e,id:id(e.id),from:id(e.from),to:id(e.to)})),layout=graphLayout(nodes,small);
 const result=routeGraphEdges(edges,layout.positions,layout.nodeWidth,layout.nodeHeight),labels=[];
 for(const [key,r]of result.routes){
  assert.equal(r.issue,null,`seed ${seed}: ${key}`);assert.ok(r.label,`seed ${seed}: ${key} label cannot disappear`);
  assert.ok(!labels.some(b=>overlaps(r.label,b)),`seed ${seed}: label overlap`);labels.push(r.label);
  for(const p of Object.values(layout.positions))assert.ok(!overlaps(r.label,{left:p.x,top:p.y,right:p.x+layout.nodeWidth,bottom:p.y+layout.nodeHeight}));
  for(const other of result.routes.values()){const b=other.target,side=other.toSide;assert.ok(!overlaps(r.label,{left:b.x-(side==='left'?24:side==='right'?4:10),right:b.x+(side==='right'?24:side==='left'?4:10),top:b.y-(side==='top'?24:side==='bottom'?4:10),bottom:b.y+(side==='bottom'?24:side==='top'?4:10)}));}
 }
 varied++;
}
console.log(`PASS: ${varied} placements with regenerated IDs retain every label without covering labels, roles or arrowheads`);

// A valid corridor must survive tiny moves even when another route becomes
// cheaper. Exercise both initial choices and repeated reversals with real IDs.
const recent=fixture.workspace_graph.snapshots.at(-1),wide=graphLayout(recent.nodes,false);
const pm=recent.nodes.find(n=>n.kind==='pm'),spec=recent.edges.find(e=>e.from===pm.id&&e.kind==='specification'&&recent.nodes.find(n=>n.id===e.to).name.includes('검사'));
const at=offset=>{const p=structuredClone(wide.positions);p[pm.id].x+=offset;return p;};
for(const first of [0,8,9]){
 let prior=routeGraphEdges(recent.edges,at(first),wide.nodeWidth,wide.nodeHeight,{basePositions:wide.positions}).routes;
 for(const offset of [8,9,8,9,8,9,8,9]){
  const original=JSON.stringify([...prior]),next=routeGraphEdges(recent.edges,at(offset),wide.nodeWidth,wide.nodeHeight,{basePositions:wide.positions,previousRoutes:prior}).routes;
  const a=prior.get(spec.id),b=next.get(spec.id),dx=Math.abs(b.source.x-a.source.x);
  assert.equal(b.points.length,a.points.length,'valid corridor retains bends');
  for(let i=0;i<a.points.length;i++)assert.ok(Math.hypot(b.points[i].x-a.points[i].x,b.points[i].y-a.points[i].y)<=dx+1e-8,'one pixel must not change corridor');
  assert.ok(Math.hypot(b.label.x-a.label.x,b.label.y-a.label.y)<=dx+1e-8,'one pixel must not move label to another segment');
  assert.equal(JSON.stringify([...prior]),original,'previous geometry remains immutable');
  assert.deepEqual(routeGraphEdges(recent.edges,at(offset),wide.nodeWidth,wide.nodeHeight,{basePositions:wide.positions,previousRoutes:next}).routes,next,'redraw after release is idempotent');
  prior=next;
 }
}
// Move an unrelated card into a previously valid straight corridor. Stability
// must not freeze a now-obstructed route or hide the need for rerouting.
const obstacleEdges=[{id:'test',from:'a',to:'b',kind:'dependency'}];
const clear={a:{x:0,y:0},b:{x:0,y:400},blocker:{x:200,y:180}};
const initial=routeGraphEdges(obstacleEdges,clear,100,100).routes;
const obstructed={...clear,blocker:{x:0,y:180}};
const rerouted=routeGraphEdges(obstacleEdges,obstructed,100,100,{basePositions:clear,previousRoutes:initial}).routes.get('test');
assert.equal(rerouted.issue,null);assert.notEqual(rerouted.path,initial.get('test').path);
for(let i=1;i<rerouted.points.length;i++){
 const a=rerouted.points[i-1],b=rerouted.points[i];
 for(const p of Object.values(obstructed))assert.ok(!(a.x===b.x?a.x>p.x&&a.x<p.x+100&&Math.max(a.y,b.y)>p.y&&Math.min(a.y,b.y)<p.y+100:a.y>p.y&&a.y<p.y+100&&Math.max(a.x,b.x)>p.x&&Math.min(a.x,b.x)<p.x+100),'rerouting avoids actual obstacle');
}
assert.ok(!overlaps(rerouted.label,{left:0,top:180,right:100,bottom:280}),'new obstacle also invalidates old name placement');
const removed=routeGraphEdges([],obstructed,100,100,{previousRoutes:initial});assert.equal(removed.routes.size,0,'removed relationships never survive through cached routes');
const replaced=routeGraphEdges([{...obstacleEdges[0],to:'blocker'}],clear,100,100,{previousRoutes:initial}).routes.get('test');
assert.notDeepEqual(replaced.target,initial.get('test').target,'same ID cannot retain old endpoint identity');
console.log('PASS: reported +8/+9 reversal from three initial corridors, immutable previous geometry, release redraw, real obstruction rerouting and replaced/removed records');
// A newly observed short connection gets the same allocation as a fresh graph;
// old labels must not consume all of its narrow mobile gutter.
let arrivals=0;
for(const small of [true,false])for(const snapshot of fixture.workspace_graph.snapshots){
 const l=graphLayout(snapshot.nodes,small),fresh=routeGraphEdges(snapshot.edges,l.positions,l.nodeWidth,l.nodeHeight);
 for(const arriving of snapshot.edges){
  const partial=routeGraphEdges(snapshot.edges.filter(e=>e.id!==arriving.id),l.positions,l.nodeWidth,l.nodeHeight);
  const next=routeGraphEdges(snapshot.edges,l.positions,l.nodeWidth,l.nodeHeight,{previousRoutes:partial.routes});
  assert.deepEqual(next,fresh,'new relationships reallocate ports and label capacity');arrivals++;
 }
}
console.log(`PASS: ${arrivals} newly observed relationship boundaries preserve every label`);
