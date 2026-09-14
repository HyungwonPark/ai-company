"""Bind successful CI to the approved workflow, definition, attempt and artifacts."""
import base64
from hashlib import sha256
from io import BytesIO
import json
from unittest.mock import patch
from zipfile import ZipFile

from ai_company.contracts import digest
from ai_company.flow_contracts import RemoteCI
from ai_company.flow_evidence import Verifier
from ai_company.runtime import ExecutionBlocked
from test_dispatcher import FlowFixture


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
