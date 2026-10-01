"""Opt-in host proof. Uses ONLY a generated CLI, fresh private repo and fixture queue."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

from ai_company.adapters.session_cli import run_session
from ai_company.contracts import Task
from ai_company.dispatcher import Dispatcher
from ai_company.flow_contracts import AgentProfile, CheckCommand, FlowPolicy, FlowSpec
from ai_company.sessions import RetryPolicy


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('init','tick','assert'))
    parser.add_argument('--state-dir',type=Path,required=True)
    args=parser.parse_args();root=args.state_dir.resolve();repo=root/'repo'
    if args.action=='init':
        if root.exists():raise SystemExit('Refusing to replace an existing validation directory')
        repo.mkdir(parents=True,mode=0o700);root.chmod(0o700)
        def git(*argv):return subprocess.check_output(['git','-C',str(repo),*argv],text=True,stderr=subprocess.DEVNULL).strip()
        git('init','-q');git('config','user.name','Timer fixture');git('config','user.email','fixture@example.invalid')
        git('remote','add','origin','https://github.com/fixture/flow-proof.git')
        (repo/'src').mkdir();(repo/'src/code.txt').write_text('initial')
        git('add','.');git('-c','commit.gpgsign=false','commit','-qm','fixture')
        profiles=[]
        for name,effort,roles in [('astra','high',('developer','reviewer')),('pm','ultra',('pm',)),('final','ultra',('final',))]:
            profiles.append(AgentProfile(agent_id=name,provider='codex',requested_label='fixture',model='gpt-6-astra',
                reasoning_effort=effort,roles=roles,capabilities=('structured_result','read_repository','write_repository'),
                allowed_paths=('src/',),credential_ref='fixture-account',quota_group='fixture-shared',
                verified_model='gpt-6-astra',verified_effort=effort,verification_evidence='generated CLI fixture'))
        task=Task(task_id='timer-proof',goal='Validate quota restart',acceptance=('Single saved-session resume',),
                  repository='fixture/flow-proof',base_sha=git('rev-parse','HEAD'),allowed_paths=('src/',),required_checks=('unit',))
        spec=FlowSpec(task=task,worktree=str(repo),agents=tuple(profiles),approved_plan=True,mode='fixture',
              policy=FlowPolicy(candidates={'pm':('pm',),'developer':('astra',),'reviewer':('astra',),'final':('final',)},
                                retry=RetryPolicy(reset_grace_seconds=1)),checks={'unit':CheckCommand(argv=('true',))})
        (root/'spec.json').write_text(spec.model_dump_json(indent=2))
        cli=root/'fixture-cli'
        cli.write_text('#!'+sys.executable+'\n'+'''import json,pathlib,subprocess,sys,time
root=pathlib.Path(__file__).parent
calls=root/'calls.json'
records=json.loads(calls.read_text()) if calls.exists() else []
records.append(sys.argv[1:]);calls.write_text(json.dumps(records))
text=sys.stdin.read(); doc=json.JSONDecoder().raw_decode(text[text.index('{'):])[0]
print(json.dumps({'type':'thread.started','thread_id':'timer-saved-session'}),flush=True)
if len(records)==1:
 print(json.dumps({'type':'turn.failed','error':{'code':'usage_limit_reached','reset_at':time.time()+3}}),flush=True)
 raise SystemExit(1)
pathlib.Path('src/code.txt').write_text('resumed once')
subprocess.run(['git','add','src'],check=True,stdout=subprocess.DEVNULL)
subprocess.run(['git','-c','commit.gpgsign=false','commit','-qm','resumed fixture'],check=True,stdout=subprocess.DEVNULL)
report=doc['checkpoint']['expected_report']
report.update(candidate_sha=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),verdict='DONE',findings=[],resolved_findings=[],summary='timer fixture')
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(report)}}),flush=True)
print(json.dumps({'type':'turn.completed','model':'gpt-6-astra','effort':'high'}),flush=True)
''');cli.chmod(0o700)
    if not (root/'spec.json').is_file():raise SystemExit('Missing fixture marker')
    spec=FlowSpec.model_validate_json((root/'spec.json').read_text())
    if spec.mode!='fixture' or spec.task.repository!='fixture/flow-proof':raise SystemExit('Not a fixture queue')
    def executor(agent,state,provider,worktree,prompt,sid,**kwargs):
        return run_session(provider,worktree,prompt,sid,**kwargs,executable=str(root/'fixture-cli'),
                           model=agent.model,reasoning_effort=agent.reasoning_effort,isolate_cgroup=True)
    dispatcher=Dispatcher(root/'queue',executor=executor)
    try:
        if args.action=='init':dispatcher.submit(spec)
        if args.action!='assert':dispatcher.run_once()
        state=dispatcher.get('timer-proof')
        if args.action=='assert':
            calls=json.loads((root/'calls.json').read_text())
            assert len(calls)==2,calls
            assert calls[1][:3]==['exec','resume','timer-saved-session'],calls
            assert state['stage']=='check',state['status']
            assert state['usage']['executions']==2,state['usage']
            assert state['usage']['runtime_seconds']<300,state['usage']
        print(json.dumps({k:state[k] for k in ('task_id','status','stage','resume_at','usage')}))
    finally:dispatcher.close()

if __name__=='__main__':main()
