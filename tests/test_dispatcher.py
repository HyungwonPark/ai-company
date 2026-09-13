"""Deterministic PR #4 scenarios: private repositories/queues and an adjustable clock."""
import copy
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from ai_company.adapters.session_cli import SessionOutcome
from ai_company.contracts import Task, digest
from ai_company.dispatcher import Dispatcher
from ai_company.flow_contracts import AgentProfile, CheckCommand, FlowPolicy, FlowSpec
from ai_company.runtime import ExecutionBlocked
from ai_company.sessions import repository_snapshot


class Crash(BaseException):
    pass


class FixtureVerifier:
    def __init__(self):
        self.remote_ready = True
        self.run_id = 1
        self.fail_check = False

    def check(self, spec, state):
        return dict(head_sha=state['snapshot']['head_commit'], task_digest=digest(spec.task),
                    policy_digest=digest(spec.policy), checks=[dict(name='unit', success=not self.fail_check)],
                    passed=not self.fail_check, runtime_seconds=1, remote=None)

    def remote(self, spec, state):
        if self.remote_ready:
            return dict(source='fixture', head_sha=state['snapshot']['head_commit'],
                        task_digest=digest(spec.task), policy_digest=digest(spec.policy), run_id=self.run_id)


class FlowFixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); self.repo = self.root / 'repo'; self.repo.mkdir()
        self.git('init', '-q'); self.git('config', 'user.name', 'fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('remote', 'add', 'origin', 'https://github.com/owner/repo.git')
        (self.repo / 'src').mkdir(); (self.repo / 'src/code.txt').write_text('initial')
        self.commit()
        self.task = Task(task_id='flow-001', goal='Implement and review fixture', acceptance=('All checks pass',),
                         repository='owner/repo', base_sha=self.git('rev-parse', 'HEAD'),
                         allowed_paths=('src/',), required_checks=('unit',))
        agents = []
        for name, provider, effort, roles in [('pm','codex','ultra',('pm',)),
                ('astra','codex','high',('developer','reviewer')), ('claude','claude','xhigh',('developer','reviewer')),
                ('final','codex','ultra',('final',))]:
            model = 'gpt-6-astra' if provider == 'codex' else 'claude-opus-5'
            agents.append(AgentProfile(agent_id=name, provider=provider, requested_label=name, model=model,
                reasoning_effort=effort, ultracode_enabled=provider == 'claude', verified_ultracode=provider == 'claude',
                roles=roles, capabilities=('structured_result','read_repository','write_repository'),
                allowed_paths=('src/',), credential_ref=provider+'-account', quota_group=provider+'-shared',
                verified_model=model, verified_effort=effort, verification_evidence='fixture-only'))
        self.spec = FlowSpec(task=self.task, worktree=str(self.repo), agents=tuple(agents), approved_plan=True,
                    policy=FlowPolicy(candidates={'pm':('pm',),'developer':('astra','claude'),
                                'reviewer':('astra','claude'),'final':('final',)}),
                    checks={'unit':CheckCommand(argv=('true',))}, mode='fixture')
        self.now = 1000.; self.calls = []; self.script = []; self.verifier = FixtureVerifier()
        self.dispatcher = self.open(); self.addCleanup(lambda: self.dispatcher.close())

    def git(self,*args):
        return subprocess.run(['git','-C',str(self.repo),*args],check=True,capture_output=True,text=True).stdout.strip()

    def commit(self):
        self.git('add','src'); self.git('-c','commit.gpgsign=false','commit','-qm','fixture')

    def open(self):
        return Dispatcher(self.root/'state',clock=lambda:self.now,verifier=self.verifier,executor=self.execute)

    def restart(self):
        self.dispatcher.close(); self.dispatcher=self.open()

    def state(self):
        return self.dispatcher.get(self.task.task_id)

    def execute(self,agent,state,provider,worktree,prompt,session_id,**kwargs):
        self.calls.append(dict(agent=agent.agent_id,role=state['stage'],session_id=session_id,timeout=kwargs['timeout_seconds']))
        action=self.script.pop(0) if self.script else 'success'
        if isinstance(action,BaseException): raise action
        sid=session_id or 'session-'+str(len(self.calls))
        if isinstance(action,SessionOutcome):
            action.session_id=action.session_id or sid
            action.result={'duration_seconds':2,'total_cost_usd':0.1}
            return action
        if state['stage']=='developer':
            (self.repo/'src/code.txt').write_text('implementation '+str(len(self.calls))); self.commit()
        active=state['active']
        report=dict(execution_id=active['execution_id'],generation=active['generation'],role=state['stage'],
                    task_digest=digest(FlowSpec.model_validate(state['specification']).task),policy_digest=digest(FlowSpec.model_validate(state['specification']).policy),
                    candidate_sha=self.git('rev-parse','HEAD'),verification_digest=digest(state['verification']) if state['verification'] else None,
                    verdict='DONE' if state['stage']=='developer' else ('REVISE' if action=='reject' else 'PASS'),
                    findings=[dict(finding_id='R1',detail='Fix fixture',evidence='src/code.txt')] if action=='reject' else [],
                    resolved_findings=[f['finding_id'] for f in state['findings']],summary='Fixture stage result')
        if action=='self': sid=state['authors'][0]['session_id']
        result=dict(duration_seconds=2,total_cost_usd=.1,observed_models=[agent.model],
                    observed_efforts=[agent.reasoning_effort],observed_ultracode=[agent.ultracode_enabled],structured_output=report)
        if action=='missing_model': result['observed_models']=[]
        return SessionOutcome('success',session_id=sid,result=result)

    def submit(self, spec=None): return self.dispatcher.submit(spec or self.spec)
    def tick(self): self.dispatcher.run_once(); return self.state()
    def quota(self): return SessionOutcome('quota',reset_at=5000)
    def finish(self,limit=12):
        for _ in range(limit):
            s=self.tick()
            if s['resume_at'] is None: return s
        return s



class DispatcherTests(FlowFixture):
    def test_01_codex_to_claude_cancels_old_reservation(self):
        self.submit(); self.script=[self.quota()]; s=self.tick()
        self.assertEqual(s['active']['agent_id'],'claude'); old=s['executions'][0]
        self.assertEqual(self.dispatcher.queue.get(old['job_id'])['status'],'SUPERSEDED')
        self.assertIsNone(self.dispatcher.queue.get(old['job_id'])['resume_at'])
        s=self.tick(); self.assertEqual(self.calls[-1]['agent'],'claude'); self.assertIsNone(self.calls[-1]['session_id'])
        self.assertEqual(s['task_id'],self.task.task_id); self.assertEqual(s['usage']['executions'],2)

    def test_02_claude_to_codex(self):
        policy=self.spec.policy.model_copy(update={'candidates':dict(self.spec.policy.candidates,developer=('claude','astra'))})
        self.submit(self.spec.model_copy(update={'policy':policy})); self.script=[self.quota()]
        self.assertEqual(self.tick()['active']['agent_id'],'astra')

    def test_03_shared_account_is_not_independent_capacity(self):
        self.submit(); self.script=[self.quota()]; self.tick()
        s=self.state(); s['stage']='final'
        _,available,times=self.dispatcher._candidates(self.spec,s)
        self.assertEqual(available,[]); self.assertEqual(times,[5030])

    def test_04_everyone_limited_persists_without_busy_retry(self):
        self.submit(); self.script=[self.quota(),self.quota()]; self.tick(); s=self.tick()
        self.assertEqual(s['status'],'WAITING_CAPACITY'); self.assertEqual(s['resume_at'],5030)
        self.restart(); self.assertEqual(self.tick()['status'],'WAITING_CAPACITY'); self.assertEqual(len(self.calls),2)

    def test_05_pm_wait_does_not_stop_approved_independent_task(self):
        self.submit(self.spec.model_copy(update={'approved_plan':False})); self.script=[self.quota()]
        self.assertEqual(self.tick()['status'],'WAITING_PM')
        independent=self.spec.model_copy(update={'task':self.task.model_copy(update={'task_id':'independent'})})
        self.dispatcher.submit(independent); result=self.dispatcher.run_once()
        self.assertEqual(result['task_id'],'independent'); self.assertEqual(self.calls[-1]['agent'],'claude')

    def test_06_final_wait_only_blocks_final_gate(self):
        self.submit(); self.tick(); self.tick(); self.tick(); self.script=[self.quota()]
        self.assertEqual(self.tick()['status'],'WAITING_FINAL_REVIEW')
        independent=self.spec.model_copy(update={'task':self.task.model_copy(update={'task_id':'independent'})})
        self.dispatcher.submit(independent); self.dispatcher.run_once()
        self.assertEqual(self.calls[-1]['role'],'developer')

    def test_07_recovered_provider_does_not_steal_and_generic_worker_skips(self):
        self.submit(); self.script=[self.quota()]; self.tick(); self.now=6000
        self.assertEqual(self.dispatcher.queue.run_once(executor=lambda *a,**k:self.fail('duplicate'))['status'],'IDLE')
        self.tick(); self.assertEqual([c['agent'] for c in self.calls],['astra','claude'])

    def test_08_crash_after_transfer_materializes_once(self):
        self.submit(); self.script=[self.quota()]; self.tick(); generation=self.state()['generation']
        self.restart(); self.tick()
        self.assertEqual(self.state()['generation'],generation); self.assertEqual(len(self.calls),2)

    def test_08b_crash_after_spawn_never_replays(self):
        self.submit(); self.script=[Crash()]
        with self.assertRaises(Crash): self.tick()
        self.restart(); s=self.tick()
        self.assertEqual(s['status'],'NEEDS_RECONCILIATION'); self.assertEqual(len(self.calls),1)

    def test_08c_crash_after_result_consumes_once(self):
        self.submit()
        with patch.object(self.dispatcher,'_handle_job',side_effect=Crash()):
            with self.assertRaises(Crash): self.tick()
        self.restart(); self.assertEqual(self.tick()['stage'],'check')
        self.assertEqual(len(self.calls),1); self.assertEqual(self.state()['usage']['executions'],1)

    def test_09_alive_old_process_prevents_transfer(self):
        self.submit(); self.script=[self.quota()]
        with patch('ai_company.dispatcher.execution_alive',return_value=True): s=self.tick()
        self.assertEqual(s['status'],'BLOCKED'); self.assertEqual(s['generation'],1)

    def test_10_late_old_result_rejected(self):
        self.submit(); self.script=[self.quota()]; self.tick(); s=self.state(); stale=copy.deepcopy(s)
        stale['active']=s['executions'][0]
        with self.assertRaisesRegex(ExecutionBlocked,'superseded'):
            self.dispatcher.accept_report(stale,self.spec,{})

    def test_11_dirty_new_files_checkpoint_and_external_mutation(self):
        self.submit(); (self.repo/'src/new.txt').write_text('partial')
        # Running provider created a new file before quota; capture it in the session fact.
        s=self.state(); s['snapshot']=repository_snapshot(self.repo)
        with self.dispatcher.db: self.dispatcher._save(s,'fixture_change')
        self.script=[self.quota()]; s=self.tick()
        files=s['active']['handoff']['files']; self.assertEqual(files[0]['path'],'src/new.txt')
        self.assertEqual(Path(files[0]['content_ref']).read_text(),'partial')
        (self.repo/'src/new.txt').write_text('external')
        self.assertEqual(self.tick()['status'],'BLOCKED')

    def test_12_missing_session_quota_full_checkpoint_can_transfer(self):
        self.submit()
        def no_session(*args,**kwargs): return SessionOutcome('quota',reset_at=5000,result={'duration_seconds':1})
        self.dispatcher.executor=no_session
        self.assertEqual(self.tick()['active']['agent_id'],'claude')

    def test_13_auth_permission_sandbox_billing_do_not_failover(self):
        for category in ('authentication','permission','sandbox','billing','approval','code_error'):
            with self.subTest(category=category):
                task=self.task.model_copy(update={'task_id':category})
                self.dispatcher.submit(self.spec.model_copy(update={'task':task}))
                self.script=[SessionOutcome(category)]
                self.dispatcher.run_once()
                s=self.dispatcher.get(category)
                self.assertEqual(s['status'],'BLOCKED'); self.assertEqual(s['generation'],1)
                self.dispatcher.db.execute("UPDATE quota_groups SET state='AVAILABLE'");self.dispatcher.db.commit()

    def test_14_context_handoff_is_new_session_without_quota(self):
        self.submit(); self.script=[SessionOutcome('context_exhausted')]
        self.assertEqual(self.tick()['status'],'NEEDS_CONTEXT_HANDOFF')
        self.dispatcher.context_handoff(self.task.task_id); self.tick()
        self.assertIsNone(self.calls[-1]['session_id']); self.assertEqual(self.calls[-1]['agent'],'astra')
        self.assertEqual(self.dispatcher.db.execute("SELECT state FROM quota_groups WHERE group_id='codex-shared'").fetchone()[0],'AVAILABLE')

    def test_15_develop_check_independent_review_repair_final(self):
        self.submit(); self.tick(); self.tick(); self.script=['reject']; self.tick()
        self.assertEqual(self.state()['stage'],'developer'); self.assertEqual(self.state()['usage']['repairs'],1)
        self.assertEqual(self.finish()['status'],'DEMO_READY')
        self.assertEqual([c['role'] for c in self.calls],['developer','reviewer','developer','reviewer','final'])
        self.assertNotEqual(self.state()['authors'][0]['session_id'],self.state()['executions'][-1]['session_id'])

    def test_missing_remote_ci_never_completes(self):
        self.verifier.remote_ready=False; self.submit(); self.tick()
        self.assertEqual(self.tick()['status'],'WAITING_CHECKS');self.assertEqual(len(self.calls),1)

    def test_ci_change_invalidates_both_reviews(self):
        self.submit(); self.tick(); self.tick(); self.tick(); self.tick(); self.verifier.run_id=2
        s=self.tick(); self.assertEqual(s['stage'],'check'); self.assertEqual(s['reviews'],{})

    def test_candidate_change_blocks_old_approval(self):
        self.submit(); self.tick(); self.tick(); (self.repo/'src/code.txt').write_text('external');self.commit()
        self.assertEqual(self.tick()['status'],'BLOCKED')

    def test_self_review_session_rejected(self):
        policy=self.spec.policy.model_copy(update={'candidates':dict(self.spec.policy.candidates,reviewer=('astra',))})
        self.submit(self.spec.model_copy(update={'policy':policy})); self.tick(); self.tick();self.script=['self']
        self.assertEqual(self.tick()['status'],'BLOCKED')

    def test_unverified_model_not_silently_substituted(self):
        agents=tuple(a.model_copy(update={'verified_effort':None}) for a in self.spec.agents)
        self.submit(self.spec.model_copy(update={'agents':agents}));self.assertEqual(self.tick()['status'],'NO_ELIGIBLE_AGENT')
        self.assertEqual(self.calls,[])

    def test_runtime_model_metadata_required(self):
        self.submit();self.script=['missing_model'];self.assertEqual(self.tick()['status'],'BLOCKED')

    def test_waiting_time_excluded_and_same_session_once_after_restart(self):
        policy=self.spec.policy.model_copy(update={'candidates':dict(self.spec.policy.candidates,developer=('astra',))})
        self.submit(self.spec.model_copy(update={'policy':policy})); self.script=[self.quota()];self.tick()
        self.restart();self.now=5030;self.tick();self.assertEqual(self.calls[-1]['session_id'],'session-1')
        self.assertEqual(self.calls[-1]['timeout'],300);self.assertEqual(self.state()['usage']['runtime_seconds'],4)

    def test_policy_migration_rollback_preserves_usage_history(self):
        self.submit();self.script=[self.quota()];self.tick();s=self.state()
        replacement=self.spec.model_copy(update={'policy':self.spec.policy.model_copy(update={'version':'v2','max_executions':30})})
        result=self.dispatcher.migrate_policy(self.task.task_id,replacement,'explicit budget extension')
        self.assertEqual(self.state()['usage'],s['usage']);self.assertEqual(self.state()['reviews'],{})
        self.dispatcher.rollback_policy(result['migration_id'],'restore budget')
        self.assertEqual(self.state()['spec_digest'],digest(self.spec));self.assertEqual(self.state()['usage'],s['usage'])

    def test_shared_account_alias_cannot_escape_quota(self):
        agents=tuple(a.model_copy(update={'quota_group':'other'}) if a.agent_id=='final' else a for a in self.spec.agents)
        with self.assertRaises(ValueError): self.submit(self.spec.model_copy(update={'agents':agents}))

    def test_check_crash_retains_repository_guard_and_never_reruns(self):
        self.submit();self.tick()
        with patch.object(self.verifier,'check',side_effect=Crash()):
            with self.assertRaises(Crash):self.tick()
        self.restart();self.assertEqual(self.tick()['status'],'NEEDS_RECONCILIATION')
        self.assertTrue((Path(self.state()['snapshot']['git_common_dir'])/'ai-company-session-active.json').exists())

    def test_check_failure_requires_repair_without_quota_retry(self):
        self.submit();self.tick();self.verifier.fail_check=True;s=self.tick()
        self.assertEqual(s['stage'],'developer');self.assertEqual(s['usage']['repairs'],1)
        self.assertEqual(s['findings'][0]['finding_id'],'CHECK-unit')

    def test_repeated_same_session_quota_rewaits_with_usage_preserved(self):
        policy=self.spec.policy.model_copy(update={'candidates':dict(self.spec.policy.candidates,developer=('astra',))})
        self.submit(self.spec.model_copy(update={'policy':policy}));self.script=[self.quota(),self.quota()]
        self.tick();self.now=5030;s=self.tick()
        self.assertEqual(s['status'],'WAITING_CAPACITY');self.assertGreater(s['resume_at'],self.now)
        self.assertEqual(s['generation'],1);self.assertEqual(s['usage']['executions'],2)

    def test_subprocess_cli_quota_restart_transfer_and_review_loop(self):
        import json,sys
        from ai_company.adapters.session_cli import run_session
        cli=self.root/'fixture-cli'
        cli.write_text('''#!'''+sys.executable+'''
import json,pathlib,subprocess,sys
text=sys.stdin.read(); checkpoint=json.JSONDecoder().raw_decode(text[text.index('{'):])[0]
report=checkpoint['checkpoint']['expected_report']; role=report['role']; args=sys.argv
claude='-p' in args
marker=pathlib.Path('src/quota-marker')
if role=='developer' and not claude and not marker.exists():
 marker.write_text('partial changes before quota')
 print(json.dumps({'type':'thread.started','thread_id':'old-session'}))
 print(json.dumps({'type':'turn.failed','error':{'message':"You've hit your usage limit. Try again later."}}))
 sys.exit(1)
if role=='developer':
 pathlib.Path('src/code.txt').write_text('implemented by replacement')
 subprocess.run(['git','add','src'],check=True,stdout=subprocess.DEVNULL)
 subprocess.run(['git','-c','commit.gpgsign=false','commit','-qm','fixture replacement'],check=True,stdout=subprocess.DEVNULL)
report.update(candidate_sha=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),verdict='DONE' if role=='developer' else 'PASS',findings=[],resolved_findings=[],summary='fixture report')
if claude:
 print(json.dumps({'type':'system','subtype':'init','session_id':'new-'+role,'model':'claude-opus-5','effort':'xhigh','ultracode':True}))
 print(json.dumps({'type':'result','session_id':'new-'+role,'subtype':'success','is_error':False,'structured_output':report}))
else:
 print(json.dumps({'type':'thread.started','thread_id':'new-'+role}))
 print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(report)}}))
 print(json.dumps({'type':'turn.completed','model':'gpt-6-astra','effort':'ultra' if role=='final' else 'high'}))
''');cli.chmod(0o700)
        def executor(agent,state,provider,worktree,prompt,sid,**kwargs):
            return run_session(provider,worktree,prompt,sid,**kwargs,executable=str(cli),
                model=agent.model,reasoning_effort=agent.reasoning_effort,ultracode_enabled=agent.ultracode_enabled)
        self.dispatcher.executor=executor;self.submit();self.tick();self.restart();self.dispatcher.executor=executor;self.now+=4000
        self.assertEqual(self.finish()['status'],'DEMO_READY')
        old=self.state()['executions'][0]
        self.assertEqual(self.dispatcher.queue.get(old['job_id'])['status'],'SUPERSEDED')

    def test_dirty_changes_are_handed_off_but_cannot_be_approved(self):
        self.submit();original=self.dispatcher.executor
        def dirty(*args,**kwargs):
            outcome=original(*args,**kwargs)
            (self.repo/'src/uncommitted.txt').write_text('not in CI candidate')
            return outcome
        self.dispatcher.executor=dirty
        self.assertEqual(self.tick()['status'],'BLOCKED')
        self.assertIn('uncommitted',self.state()['reason'])
