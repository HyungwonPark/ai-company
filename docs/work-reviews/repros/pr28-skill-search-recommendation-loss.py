import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from ai_company import skill_catalog
from ai_company.automation import Automation
from ai_company.automation_contracts import PMPlanContent

def role(key,cap):
    return {'key':key,'name':key,'responsibility':'Inspect output','goal':'Create output',
     'acceptance':['Output exists'],'allowed_paths':[key+'/result.txt'],'depends_on':[],
     'required_capabilities':[cap]}
plan=PMPlanContent.model_validate({'summary':'Create inspected outputs','roles':[role('ui','accessibility'),role('qa','testing')],
 'completion_criteria':['Both outputs reviewed']})
response=json.dumps({'items':[{'full_name':'found/agent-skills','html_url':'https://github.com/found/agent-skills'}]}).encode()
with TemporaryDirectory() as root:
    worker=SimpleNamespace(root=Path(root),clock=lambda:1000,config=SimpleNamespace(
       skill_catalog=(),skill_public_sources=(),skill_search_terms=('accessibility','testing'),
       agents=[],policy=SimpleNamespace(candidates={'developer':()})))
    with patch.object(skill_catalog,'_public_get',return_value=response) as get:
        selected=Automation._select_skills(worker,plan).skill_selection
    print(json.dumps(selected,ensure_ascii=False,indent=2))
    print('actual_requests=',get.call_count,'requested_urls=',[call.args[0] for call in get.call_args_list])
    assert selected['outcome']=='search_found_unpinned'
    assert selected['roles']=={'qa':[],'ui':[]}
    assert 'found/agent-skills' not in json.dumps(selected)
    print('CONFIRMED: successful GitHub search retains only count, no source identity or recommendation in plan')
