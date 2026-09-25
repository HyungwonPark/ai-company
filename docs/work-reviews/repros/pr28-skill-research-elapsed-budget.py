from pathlib import Path
from tempfile import TemporaryDirectory
from time import sleep
from unittest.mock import patch
from ai_company import skill_catalog
from ai_company.skill_selection import SkillResearchStore
policy={'max_searches':0,'max_fetches':3,'max_bytes':3*65536,'max_elapsed_ms':40,
 'max_model_calls':0,'max_tokens':0,'max_cost_microusd':0,'expires_at':2000}
def bounded_request(url,max_bytes,timeout):
    sleep(.03) # Each request completes within its 40ms timeout.
    return b'name: example\n'
with TemporaryDirectory() as root:
    store=SkillResearchStore(Path(root)/'research.sqlite',clock=lambda:1000)
    with patch.object(skill_catalog,'_public_get',side_effect=bounded_request):
        result=store.run_fetch('research','lookup','https://github.com/example/skills/blob/'+'a'*40+'/x/SKILL.md',
            policy=policy,reference_paths=('reference.md',),license_path='LICENSE',timeout=.04)
    snapshot=store.snapshot('research')
    print('status=',result['status'],'actual_elapsed_ms=',result['elapsed_ms'],
        'policy_elapsed_ms=',policy['max_elapsed_ms'],'reserved_ms=',snapshot['reserved_ms'],
        'fetches=',snapshot['fetches'])
    assert result['elapsed_ms'] > policy['max_elapsed_ms']
    assert snapshot['reserved_ms'] == policy['max_elapsed_ms']
    store.close()
