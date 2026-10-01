"""Bind successful CI to the approved workflow, definition, attempt and artifacts."""
import base64
from hashlib import sha256
from io import BytesIO
import json
from unittest.mock import patch
from zipfile import ZipFile

from ai_company.contracts import digest
from ai_company.flow_contracts import RemoteCI
from ai_company.flow_evidence import Verifier, handoff
from ai_company.runtime import ExecutionBlocked
from test_dispatcher import FlowFixture
from ai_company.sessions import repository_snapshot


class HandoffRepositoryStateTests(FlowFixture):
    def bundle(self, name, *, new_policy=True):
        spec = self.spec.model_copy(update={'policy':self.spec.policy.model_copy(update={
            'configuration_evidence':'cli_configuration_v2' if new_policy else 'runtime_metadata'})})
        state = {'stage':'reviewer','task_id':spec.task.task_id,'generation':1,'last_completed_stage':'check',
                 'findings':[],'verification':None,'plan':{},'usage':{},'snapshot':{'clean':'untrusted-state-hint'}}
        return handoff(spec, state, {}, self.root/name)

    def test_clean_fingerprint_is_not_a_dirty_boolean(self):
        original = repository_snapshot(self.repo)
        bundle = self.bundle('clean')
        self.assertTrue(bundle['snapshot']['dirty_digest'])
        self.assertEqual(bundle['repository_state'], {'source':'runner_git_status',
            'snapshot_digest':digest(original),'head_commit':original['head_commit'],'clean':True})
        self.assertEqual(repository_snapshot(self.repo), original)

    def test_tracked_and_untracked_edits_are_reported_dirty_then_clean_after_commit(self):
        for name in ('src/code.txt','src/extra.txt'):
            (self.repo/name).write_text('changed')
            bundle = self.bundle(name.replace('/','-'))
            self.assertFalse(bundle['repository_state']['clean'])
            self.assertEqual(bundle['repository_state']['snapshot_digest'], digest(repository_snapshot(self.repo)))
            self.commit()
            self.assertTrue(self.bundle('committed-'+name.replace('/','-'))['repository_state']['clean'])

    def test_legacy_handoff_keeps_original_snapshot_shape(self):
        before = repository_snapshot(self.repo)
        bundle = self.bundle('legacy',new_policy=False)
        self.assertNotIn('repository_state',bundle)
        self.assertEqual(bundle['snapshot'],before)


class EvidenceTests(FlowFixture):
    def setUp(self):
        super().setUp()
        self.workflow=b'approved CI'
        self.spec=self.spec.model_copy(update={'remote_ci':RemoteCI(pr_number=1,required_checks=('unit',),
                                     trusted_workflow_digest=sha256(self.workflow).hexdigest())})
        self.saved=self.submit();self.head=self.saved['snapshot']['head_commit'];self.merge='b'*40
        self.attestation=dict(head_sha=self.head,base_sha=self.spec.task.base_sha,tested_sha=self.head,
                     task_digest=digest(self.spec.task),policy_digest=digest(self.spec.policy),run_id=5,run_attempt=1,
                     workflow_ref='owner/repo/.github/workflows/ci.yml@refs/pull/1/merge',workflow_sha=self.merge)
        check=dict(id=1,name='unit',app={'slug':'github-actions'},head_sha=self.head,status='completed',
                   conclusion='success',check_suite={'id':7},details_url='https://github.com/owner/repo/actions/runs/5/job/1')
        self.run=dict(id=5,head_sha=self.head,conclusion='success',status='completed',workflow_id=8,
                      path='.github/workflows/ci.yml',repository={'full_name':'owner/repo'},
                      event='pull_request',run_attempt=1,check_suite_id=7)
        self.job=dict(run_id=5,run_attempt=1,head_sha=self.head,name='unit',status='completed',conclusion='success',
                      check_run_url='https://api.github.com/repos/owner/repo/check-runs/1')
        self.artifact=dict(id=9,name='ai-company-evidence',expired=False,size_in_bytes=500,
                           workflow_run={'id':5,'head_sha':self.head})
        self.responses={
            'repos/owner/repo/pulls/1':{'head':{'sha':self.head},'base':{'sha':self.head},'merge_commit_sha':self.merge},
            'repos/owner/repo/actions/workflows/ci.yml':{'id':8,'path':'.github/workflows/ci.yml'},
            f'repos/owner/repo/contents/.github/workflows/ci.yml?ref={self.head}':{'content':base64.b64encode(self.workflow).decode()},
            f'repos/owner/repo/contents/.github/workflows/ci.yml?ref={self.merge}':{'content':base64.b64encode(self.workflow).decode()},
            f'repos/owner/repo/commits/{self.head}/check-runs?per_page=100':{'check_runs':[check]},
            'repos/owner/repo/actions/runs/5':self.run,
            'repos/owner/repo/actions/runs/5/attempts/1/jobs?per_page=100':{'jobs':[self.job]},
            'repos/owner/repo/actions/runs/5/artifacts':{'artifacts':[self.artifact]},
            'repos/owner/repo/actions/artifacts/9':self.artifact}
        self.verifier=Verifier(self.root)
        self.patcher=patch.object(self.verifier,'_api',side_effect=self.api);self.patcher.start();self.addCleanup(self.patcher.stop)

    def api(self,path,raw=False):
        if path.endswith('/9/zip'):
            out=BytesIO()
            with ZipFile(out,'w') as z:z.writestr('evidence.json',json.dumps(self.attestation))
            return out.getvalue()
        return self.responses[path]

    def remote(self):return self.verifier.remote(self.spec,self.saved)

    def test_attested_candidate_and_task_policy(self):
        result=self.remote();self.assertEqual(result['source'],'github');self.assertEqual(result['workflow_id'],8)
        self.attestation['policy_digest']='0'*64;self.assertIsNone(self.remote())

    def test_missing_tested_sha_cannot_match_null_merge(self):
        self.attestation.pop('tested_sha');self.assertIsNone(self.remote())

    def test_changed_workflow_rejected(self):
        self.spec=self.spec.model_copy(update={'remote_ci':self.spec.remote_ci.model_copy(update={'trusted_workflow_digest':'0'*64})})
        with self.assertRaises(ExecutionBlocked):self.remote()

    def test_unapproved_workflow_same_checks_and_attestation_rejected(self):
        self.run.update(workflow_id=99,path='.github/workflows/unapproved.yml')
        self.assertIsNone(self.remote())

    def test_matching_path_but_wrong_workflow_id_rejected(self):
        self.run['workflow_id']=99;self.assertIsNone(self.remote())

    def test_executed_definition_differs_even_when_head_definition_approved(self):
        self.responses[f'repos/owner/repo/contents/.github/workflows/ci.yml?ref={self.merge}']['content']=base64.b64encode(b'unapproved executed definition').decode()
        with self.assertRaises(ExecutionBlocked):self.remote()

    def test_check_url_cannot_claim_membership_in_another_run(self):
        self.job['check_run_url']='https://api.github.com/repos/owner/repo/check-runs/777'
        self.assertIsNone(self.remote())

    def test_check_from_prior_attempt_rejected(self):
        self.job['run_attempt']=0;self.assertIsNone(self.remote())

    def test_artifact_from_different_run_rejected_even_if_content_matches(self):
        self.artifact['workflow_run']['id']=77;self.assertIsNone(self.remote())

    def test_artifact_target_commit_mismatch_rejected(self):
        self.artifact['workflow_run']['head_sha']='c'*40;self.assertIsNone(self.remote())

    def test_attestation_must_record_executed_definition_and_attempt(self):
        for key in ('workflow_ref','workflow_sha','run_attempt'):
            value=self.attestation.pop(key)
            self.assertIsNone(self.remote(),key)
            self.attestation[key]=value

    def test_unsupported_event_cannot_claim_head_workflow(self):
        self.run['event']='pull_request_target';self.assertIsNone(self.remote())

    def test_unapproved_reusable_workflow_definition_is_not_implicitly_trusted(self):
        self.run['referenced_workflows']=[{'path':'other/repo/.github/workflows/build.yml@main','sha':'c'*40}]
        self.assertIsNone(self.remote())

    def test_run_source_and_target_must_match_approved_workflow(self):
        for key,value in (('path','.github/workflows/unapproved.yml'),
                          ('repository',{'full_name':'other/repo'}),
                          ('head_sha','c'*40),('id',77)):
            with self.subTest(field=key):
                original=self.run[key];self.run[key]=value
                self.assertIsNone(self.remote())
                self.run[key]=original

    def test_required_check_and_job_must_belong_to_successful_run(self):
        check=self.responses[f'repos/owner/repo/commits/{self.head}/check-runs?per_page=100']['check_runs'][0]
        check['check_suite']['id']=77
        self.assertIsNone(self.remote());check['check_suite']['id']=7
        for key,value in (('run_id',77),('head_sha','c'*40),('name','another-check'),
                          ('conclusion','failure'),('status','in_progress')):
            with self.subTest(field=key):
                original=self.job[key];self.job[key]=value
                self.assertIsNone(self.remote())
                self.job[key]=original

    def test_pr_or_run_changed_during_lookup_is_not_approved(self):
        for target in ('pr','run'):
            with self.subTest(target=target):
                calls={}
                def changing(path,raw=False):
                    result=self.api(path,raw)
                    calls[path]=calls.get(path,0)+1
                    if target=='pr' and path=='repos/owner/repo/pulls/1' and calls[path]>1:
                        return dict(result,head={'sha':'c'*40})
                    if target=='run' and path=='repos/owner/repo/actions/runs/5' and calls[path]>1:
                        return dict(result,run_attempt=2)
                    return result
                with patch.object(self.verifier,'_api',side_effect=changing):
                    self.assertIsNone(self.remote())
