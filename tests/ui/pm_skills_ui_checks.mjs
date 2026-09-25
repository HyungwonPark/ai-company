import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const load=async name=>import('data:text/javascript;base64,'+Buffer.from(await readFile(new URL(`../../src/ai_company/web/${name}`,import.meta.url),'utf8')).toString('base64'));
const esc=value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const documents={text:(_id,_field,fallback)=>fallback,meta:()=>''};
const {createPlanUI}=await load('plan-ui.js');
const {createManagerUI}=await load('manager-ui.js');
const planUI=createPlanUI({esc,documents});
const managerUI=createManagerUI({esc,documents,badge:esc,label:esc,stamp:String,planContent:plan=>planUI.render(plan)});
const options={connected:true,composer:'',history:'',technical:'',archive:'',tab:'team'};
const role={key:'web',name:'화면 개발',responsibility:'첫 화면을 만듭니다',depends_on:[],allowed_paths:['src/web/'],acceptance:['모바일 확인']};
const base={id:'new-plan',digest:'new-digest',request_revision:1,status:'proposed',content:{summary:'첫 화면 만들기',roles:[role],completion_criteria:['실기기 검수']}};
const overview=plan=>({project:{id:'new-project',goal:'첫 화면',request_revision:1},plans:[plan],pm_requests:[],runs:[]});

assert.doesNotMatch(planUI.render(base),/작업 지침/,'v1/v2 plans without the new contract stay unchanged');
const empty={...base,content:{...base.content,skill_selection:{status:'0',outcome:'not_needed',roles:{web:[]}}}};
assert.match(planUI.render(empty),/현재 지침으로 진행합니다/);
assert.match(managerUI.render(overview(empty),options),/현재 지침으로 진행합니다/);
const failed={...base,content:{...base.content,skill_selection:{status:'0',outcome:'lookup_failed',reason:'공식 자료 조회 시간 초과',can_continue:true,roles:{web:[]}}}};
assert.match(managerUI.render(overview(failed),options),/조회 실패 · 공식 자료 조회 시간 초과/);
assert.match(planUI.render(failed),/기존 지침으로 진행할 수 있습니다/);
const uncertain={...base,content:{...base.content,skill_selection:{status:'no_additional_skill',outcome:'lookup_pending',reason:'이전 공개 조회 결과가 아직 확인되지 않았습니다.',can_continue:true,roles:{web:[]}}}};
assert.match(planUI.render(uncertain),/이 역할의 공개 조회 결과가 아직 확인되지 않았습니다/);
assert.match(managerUI.render(overview(uncertain),options),/이 역할의 공개 조회 결과가 아직 확인되지 않았습니다/);
const secondRole={...role,key:'test',name:'검사'};
const mixed={...base,content:{...base.content,roles:[role,secondRole],skill_selection:{
  status:'no_additional_skill',outcome:'no_matching_document',reason:'첫 역할의 문서를 찾지 못함',
  role_outcomes:{web:'no_matching_document',test:'not_searched'},roles:{web:[],test:[]}}}};
const mixedHTML=planUI.render(mixed);
assert.match(mixedHTML,/조사한 저장소에서 이 역할의 SKILL.md를 찾지 못했습니다/);
assert.match(mixedHTML,/이 역할의 공개 자료는 이번 조회 한도에서 조사하지 않았습니다/);
assert.doesNotMatch(mixedHTML,/첫 역할의 문서를 찾지 못함/,'global status cannot be presented as the second role result');
const revising={...base,status:'needs_revision',revision_action:'automatic'};
assert.match(managerUI.render(overview(revising),options),/PM 자동 수정 중/);
assert.match(managerUI.render(overview(revising),options),/기술 지적을 자동으로 보완/);

const skill={skill_id:'screen',name:'접근성 점검',reason:'키보드 이동과 모바일 버튼 크기를 확인합니다.',status:'reviewed',selected:true,
  source_url:'https://example.org/skill/<script>',version:'v1',bundle_sha256:'abc123',requirements:['R1'],dependencies:['Chrome'],permissions:['읽기'],delivery_id:'receipt-1'};
const selected={...base,content:{...base.content,skill_selection:{status:'delivered',roles:{web:[skill]}}}};
const limitReason='역할별 최대 3개 · 부족 역량 performance · 공개 후보 public-d 제외';
const capped={...base,content:{...base.content,skill_selection:{status:'selected',outcome:'review_pending',
  reason:'공개 후보 검토 전',role_outcomes:{web:'limit_reached',test:'review_pending',empty:'existing_sufficient'},
  role_reasons:{web:limitReason},roles:{web:[skill,{...skill,skill_id:'second',name:'두 번째'},
    {...skill,skill_id:'third',name:'세 번째'}],test:[{...skill,skill_id:'public-d',name:'공개 후보',selected:false,status:'review_pending'}],empty:[]}},
  roles:[role,{...role,key:'test',name:'검사'},{...role,key:'empty',name:'빈 역할'}]}};
for(const html of [planUI.render(capped),managerUI.render(overview(capped),options)]){
  assert.match(html,/부족 역량 performance · 공개 후보 public-d 제외/);
  assert.match(html,/공개 후보/);
  assert.doesNotMatch(html,/자료 조회 실패|조회 실패/);
}
const emptyRoleHTML=managerUI.render(overview(capped),options).split('<strong>빈 역할</strong>')[1].split('</li>')[0];
assert.match(emptyRoleHTML,/현재 지침으로 진행합니다/);
assert.doesNotMatch(emptyRoleHTML,/후보를 검토 중/);
const before=JSON.stringify(selected),planHTML=planUI.render(selected),teamHTML=managerUI.render(overview(selected),options);
assert.equal(JSON.stringify(selected),before,'rendering must not mutate the plan, digest, or selection');
for(const html of [planHTML,teamHTML]){
  assert.match(html,/접근성 점검/);assert.match(html,/키보드 이동과 모바일 버튼 크기/);
  assert.match(html,/선택됨/,'a selected plan remains visible without claiming execution delivery');
  assert.match(html,/출처/);assert.match(html,/파일 묶음 해시/);assert.match(html,/필요 권한/);
  assert.doesNotMatch(html,/<script>/,'remote metadata must remain escaped');
  assert.doesNotMatch(html,/품질 향상 완료|효과 확인 완료/,'a delivery receipt is not an effectiveness evaluation');
}
assert.match(teamHTML,/manager-agent-skills/);
assert.doesNotMatch(teamHTML,/전달 기록/,'immutable plan metadata cannot claim execution delivery');
const selectedWithDigest={...selected,content:{...selected.content,skill_selection:{...selected.content.skill_selection,digest:'s'.repeat(64)}}};
const run={id:'run-1',plan_id:'new-plan',plan_digest:'new-digest',state:'running',roles:{web:{task_id:'role-1'}}};
const task={id:'role-1',skill_delivery:{delivery_id:'actual-delivery',selection_digest:'s'.repeat(64),role_key:'web',
  documents:[{skill_id:'screen',bundle_sha256:'abc123'}],phase:'process_started',mode:'fixture'}};
const deliveredHTML=managerUI.render({...overview(selectedWithDigest),runs:[run],tasks:[task]},options);
assert.match(deliveredHTML,/실행 전달 · 모의 · 선택됨/);
assert.match(deliveredHTML,/전달 기록 · actual-delivery · 효과 미확인/);
const wrongRun=managerUI.render({...overview(selectedWithDigest),runs:[run],tasks:[{...task,id:'other-role'}]},options);
assert.doesNotMatch(wrongRun,/실행 전달|전달 기록/,'another task cannot supply delivery evidence');
const actualStatus={...base,content:{...base.content,skill_selection:{status:'selected',roles:{web:[{...skill,status:'approved_document',delivery_id:null}]}}}};
assert.match(planUI.render(actualStatus),/내용 검토 완료 · 선택됨/);
assert.match(managerUI.render(overview(actualStatus),options),/계획에 배정 · 선택됨/);
assert.match(teamHTML,/data-persist-key="team-skill:new-plan:web:접근성 점검"/,'expanded details have a stable key');
assert.doesNotMatch(teamHTML,/data-action="approve-skill"|data-action="install-skill"/,'recommendations do not create a new approval or install action');
const candidate={...base,content:{...base.content,skill_selection:{status:'recommended',roles:{web:[{...skill,status:'recommended',selected:false,delivery_id:null}]}}}};
assert.match(managerUI.render(overview(candidate),options),/추천 · 미선택/);
assert.doesNotMatch(managerUI.render(overview(candidate),options),/전달 기록/);
console.log('PASS: role skill summary/detail, no-skill and search-failure cases, escaped facts, evidence distinction, stable details');
