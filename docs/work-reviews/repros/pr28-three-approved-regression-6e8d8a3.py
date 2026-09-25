from types import SimpleNamespace
from unittest.mock import patch
from ai_company.automation import Automation
from ai_company.automation_contracts import PMPlanContent
from ai_company.skill_selection import validate_selection

def entry(skill_id, capability, status):
    return {'skill_id':skill_id,'name':skill_id,'source_url':'https://github.com/example/skills/blob/'+'a'*40+'/SKILL.md',
     'source_ref':'a'*40,'version':'a'*40,'bundle_sha256':'b'*64,'files':{'SKILL.md':'c'*64},
     'license':{},'compatibility':{'providers':['codex'],'runners':['session_cli']},
     'dependencies':[],'permissions':[],'status':status,'capabilities':[capability]}
trusted={f'local-{cap}':{'entry':entry(f'local-{cap}',cap,'approved_document')} for cap in ['accessibility','testing','security']}
external={**entry('public-d','performance','review_pending'),'research_origin':'public_search','research_match_terms':['performance']}
plan=PMPlanContent.model_validate({'summary':'Produce reviewed output','completion_criteria':['Output reviewed'],
 'roles':[{'key':'impl','name':'impl','responsibility':'Produce output','goal':'Produce reviewed output',
 'acceptance':['Output exists'],'allowed_paths':['impl/result.txt'],'depends_on':[],
 'required_capabilities':['accessibility','testing','security','performance']}, {'key':'qa','name':'qa','responsibility':'Review output','goal':'Review output','acceptance':['Reviewed'],'allowed_paths':['qa/result.txt'],'depends_on':[],'required_capabilities':['performance']}], 'skill_recommendations':{'impl':['public-d'],'qa':['public-d']}})
worker=SimpleNamespace(config=SimpleNamespace(skill_catalog=(),skill_public_sources=(),skill_search_terms=('performance',),
 agents=[SimpleNamespace(agent_id='developer',provider='codex')],policy=SimpleNamespace(candidates={'developer':['developer']})))
with patch('ai_company.skill_selection.load_trusted_catalog',return_value=trusted):
    result=Automation._select_skills(worker,plan,{'candidates':[external],'status':'review_pending','search_matches':1,
     'term_outcomes':{'performance':'review_pending'}}).skill_selection
assert len(result['roles']['impl'])==3
assert result['outcome']=='lookup_failed'
assert result['role_outcomes']['impl']=='review_pending'
assert result['roles']['qa']==[]
print('CONFIRMED', {key:result[key] for key in ['status','outcome','reason','role_outcomes']})
print('Approved roles survive; valid PM candidate causes false lookup failure because fourth assignment is appended before checking len == 3.')
