from types import SimpleNamespace
from unittest.mock import patch
from ai_company.dispatcher import Dispatcher
from ai_company.harness.guidance import reference

phases=[]
base_spawns=[]
def external(fn,*args,**kwargs):
    return fn(*args,**kwargs)
def executor(*args,**kwargs):
    kwargs['on_spawn']({'pid':12345})
    raise AssertionError('callback unexpectedly returned')
fake=SimpleNamespace(queue=SimpleNamespace(get=lambda _: {'status':'RUNNING','attempt_count':1,'job_id':'j'}),
    _guidance_event=lambda record,phase,**facts: phases.append(phase),
    _external=external, clock=lambda:1000, executor=executor)
fake._guided_external=lambda *args,**kwargs: Dispatcher._guided_external(fake,*args,**kwargs)
agent=SimpleNamespace(agent_id='dev',provider='codex')
spec=SimpleNamespace(execution_scope='contribution',agents=[agent],
    policy=SimpleNamespace(configuration_evidence='fixture',max_runtime_seconds=120),
    task=SimpleNamespace(task_id='t'),mode='fixture',
    plan={'guidance':reference().model_dump(mode='json'),
      'skill_delivery':{'task_id':'t','delivery_id':'d','selection_digest':'s','role_key':'impl','documents':[]}})
state={'task_id':'t','stage':'developer','usage':{'runtime_seconds':0},'spec_digest':'x',
 'active':{'agent_id':'dev','job_id':'j','execution_id':'e','role':'developer','generation':0,
 'provider':'codex','task_digest':'x','policy_digest':'y'}}
try:
    # Source export omits ECC assets; isolate only that unrelated content read.
    with patch('ai_company.harness.guidance.augment', return_value=('prompt', 'document-hash')):
        Dispatcher._executor(fake,spec,state)('codex','/tmp','prompt',None,timeout_seconds=60,
            on_spawn=lambda identity:base_spawns.append(identity))
except RecursionError:
    print('CONFIRMED: RecursionError with guidance + skill_delivery at on_spawn')
    print('base_spawn_callbacks=',len(base_spawns),'phases=',phases)
else:
    raise AssertionError('Expected RecursionError')
