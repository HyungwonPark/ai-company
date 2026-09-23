/* 실제 저장 함수의 응답 경계만 검사합니다. 브라우저·서버·모델 호출은 없습니다. */
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {webcrypto} from 'node:crypto';
import vm from 'node:vm';

const source=await readFile(new URL('../../src/ai_company/web/app.js',import.meta.url),'utf8');
const executionSource=await readFile(new URL('../../src/ai_company/web/execution-ui.js',import.meta.url),'utf8');
const {currentExecutionProposal,executionMessage}=await import('data:text/javascript;base64,'+Buffer.from(executionSource).toString('base64'));
const journeySource=await readFile(new URL('../../src/ai_company/web/journey-ui.js',import.meta.url),'utf8');
const {journeyHref}=await import('data:text/javascript;base64,'+Buffer.from(journeySource).toString('base64'));
function extract(start,end){
  const first=source.indexOf(start),last=source.indexOf(end,first);
  assert.ok(first>=0&&last>first,`실제 함수 추출 경계를 찾을 수 없습니다: ${start}`);
  return source.slice(first,last);
}
const functions=extract('function scopeParams(','async function api(')
  +extract('async function api(','function themeControls(')
  +extract('async function saveExecutionSpec(){','function closeButton(');
const selection={catalog_id:'catalog',catalog_digest:'a'.repeat(64),allowed_paths:['src/original.py']};
const newerSelection={...selection,allowed_paths:['src/newer.py']};
const response=(status=201,code='')=>({status,ok:status<400,json:async()=>code?{error:{code,message:code}}:{execution_spec:{version:1}}});

function harness(initialSelection=selection){
  let intent=null,refreshes=0;
  const requests=[],stored=[],replies=[];
  const state={connected:true,authenticated:true,csrf:'fixture-csrf',username:'fixture-master',projectId:'fixture-project',view:'project',
    overview:{project:{request_revision:0},plans:[]},
    executionEntries:[{catalog_id:selection.catalog_id,catalog_digest:selection.catalog_digest,repository:'fixture/repo'}]};
  const context={state,busyForms:new Set(),currentExecutionProposal,executionMessage,journeyHref,recordParams:new URLSearchParams('project=fixture-project'),structuredClone,crypto:webcrypto,
    location:{hash:'#project?project=fixture-project'},navigator:{onLine:true},
    TextEncoder,AbortController,Error,TypeError,URLSearchParams,setTimeout,clearTimeout,
    executionIntent:()=>intent,
    storeExecutionIntent(value,project){
      assert.equal(project,state.projectId);
      intent=value;
      if(value)stored.push(structuredClone(value));
    },
    render(){},notify(){},refresh:async()=>{refreshes++;},
    fetch:async(path,options)=>{
      requests.push({path,method:options.method,headers:options.headers,body:JSON.parse(options.body)});
      assert.ok(replies.length,'예상하지 않은 HTTP 요청입니다.');
      const reply=replies.shift();
      if(reply instanceof Error)throw reply;
      return typeof reply==='function'?reply():reply;
    },
  };
  vm.createContext(context);
  vm.runInContext(functions,context,{filename:'app.js:execution-save-extracted'});
  function propose(value){
    const revision=++state.overview.project.request_revision;
    state.overview.plans=[{id:'plan-'+revision,digest:String(revision).repeat(64),status:'proposed',request_revision:revision,
      content:{execution_spec_proposal:structuredClone(value)}}];
  }
  propose(initialSelection);
  return {context,state,requests,stored,replies,propose,intent:()=>intent,refreshes:()=>refreshes,
    save:()=>context.saveExecutionSpec(),requestPlan:()=>context.requestExecutionPlan()};
}

const oversizedSelection={...selection,allowed_paths:Array.from({length:160},(_,i)=>'src/'+'deep/'.repeat(85)+i+'.py')};
const oversized=harness(oversizedSelection);
await oversized.save();
assert.ok(new TextEncoder().encode(JSON.stringify(oversized.stored[0].body)).length>65536);
assert.equal(oversized.requests.length,0,'클라이언트 크기 검사는 HTTP 요청 전에 거부해야 합니다.');
assert.equal(oversized.intent(),null,'전송하지 않은 과대 입력은 미확인 요청으로 남으면 안 됩니다.');
assert.equal(oversized.refreshes(),1);
assert.match(oversized.state.executionError,/서버 제한/);
oversized.propose(newerSelection);
oversized.replies.push(response());
await oversized.save();
assert.equal(oversized.requests.length,1,'작은 새 제안은 기존 과대 입력에 막히지 않아야 합니다.');
assert.deepEqual(oversized.requests[0].body.selection,newerSelection);
assert.notEqual(oversized.requests[0].body.idempotency_key,oversized.stored[0].body.idempotency_key);
assert.equal(oversized.intent(),null);

for(const [status,code] of [
  [413,'body_too_large'],[400,'invalid_input'],[400,'execution_spec_invalid'],
  [409,'stale_execution_spec'],[409,'execution_catalog_unavailable'],
  [409,'execution_catalog_mismatch'],[409,'idempotency_conflict'],[409,'fixture_only'],
]){
  const test=harness();
  test.replies.push(response(status,code));
  await test.save();
  assert.equal(test.intent(),null,`${status}/${code}: 확정된 거부는 이전 요청을 해제해야 합니다.`);
  assert.equal(test.refreshes(),1);
  assert.equal(test.context.busyForms.size,0);
  test.propose(newerSelection);
  test.replies.push(response());
  await test.save();
  assert.equal(test.requests.length,2);
  assert.deepEqual(test.requests[1].body.selection,newerSelection);
  assert.notEqual(test.requests[1].body.idempotency_key,test.requests[0].body.idempotency_key);
  assert.equal(test.intent(),null);
}

for(const [name,failure] of [['network',new TypeError('fixture connection lost')],['server 500',response(500,'internal_error')]]){
  const test=harness();
  test.replies.push(failure);
  await test.save();
  const previous=test.intent();
  assert.ok(previous,`${name}: 결과 미확인 요청의 재시도 식별자를 유지해야 합니다.`);
  assert.equal(test.refreshes(),0);
  assert.equal(test.context.busyForms.size,0);
  if(name==='network')assert.equal(test.state.connected,false);
  test.propose(newerSelection);
  test.state.overview.project.execution_spec={version:2};
  test.state.connected=true;
  test.replies.push(response());
  await test.save();
  assert.equal(test.requests.length,2);
  assert.deepEqual(test.requests[1].body,test.requests[0].body,`${name}: 새 PM 제안과 현재 명세 변경이 이전 재시도 내용을 바꾸면 안 됩니다.`);
  assert.equal(test.requests[1].body.base_version,0);
  assert.deepEqual(test.requests[1].body.selection,selection);
  assert.equal(test.intent(),null,'성공 응답 후 이전 요청을 해제해야 합니다.');
  assert.equal(test.refreshes(),1);
  for(const request of test.requests){
    assert.equal(request.path,'/api/projects/fixture-project/execution-specs','명세 저장이 PM 요청이나 계획 확정을 전송하면 안 됩니다.');
    assert.equal(request.method,'POST');
    assert.equal(request.headers['X-CSRF-Token'],'fixture-csrf');
  }
}
console.log('PASS: 실제 명세 저장의 과대 입력 복구, 400/409/413 거부 해제, 통신 실패·500 응답의 원래 요청 보존 및 성공 후 해제');

for(const navigation of ['stay','same-project-view','other-project']){
  const test=harness();
  test.replies.push(()=>{
    if(navigation==='same-project-view'){
      test.state.view='approvals';
      test.context.location.hash='#approvals?project=fixture-project';
    }else if(navigation==='other-project'){
      test.state.projectId='other-project';
      test.context.location.hash='#project?project=other-project';
    }
    return response();
  });
  await test.requestPlan();
  assert.equal(test.requests.length,1,'새 PM 요청은 계획을 자동 확정하지 않습니다.');
  assert.equal(test.requests[0].path,'/api/projects/fixture-project/messages');
  assert.deepEqual(test.requests[0].body,{content:executionMessage});
  assert.equal(test.refreshes(),1);
  assert.equal(test.context.busyForms.size,0);
  if(navigation==='stay'){
    assert.equal(test.state.view,'manager','hash 변경 전에 현재 뷰를 매니저로 갱신해야 다시 렌더할 때 설정으로 돌아가지 않습니다.');
    assert.equal(test.context.location.hash,'#manager?project=fixture-project');
  }else if(navigation==='same-project-view'){
    assert.equal(test.state.view,'approvals','요청 중 이동한 같은 프로젝트의 승인 뷰를 보존해야 합니다.');
    assert.equal(test.context.location.hash,'#approvals?project=fixture-project');
  }else{
    assert.equal(test.state.projectId,'other-project');
    assert.equal(test.state.view,'project','요청 중 다른 프로젝트로 이동하면 그 화면을 보존해야 합니다.');
    assert.equal(test.context.location.hash,'#project?project=other-project');
  }
}
console.log('PASS: 실제 새 PM 요청의 매니저 전환과 요청 중 승인 뷰·다른 프로젝트 이동 보존');

const lateOffline=harness();
lateOffline.replies.push(()=>{lateOffline.context.navigator.onLine=false;return response();});
await lateOffline.save();
assert.equal(lateOffline.state.connected,false,'오프라인 전환 후 늦은 성공 응답은 쓰기 가능 상태를 복구하지 않습니다.');
assert.equal(lateOffline.intent(),null,'서버에 저장된 성공 결과는 보존합니다.');
