"""Checkout provenance and tamper gates, without contacting GitHub."""
import base64
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from ai_company.contracts import digest
from ai_company.flow_contracts import RemoteCI
from ai_company.flow_evidence import Verifier
from ai_company.runtime import ExecutionBlocked
from test_dispatcher import FlowFixture


class EvidenceTests(FlowFixture):
    pass


def fixture_remote(test):
    workflow=b'approved CI'
    spec=test.spec.model_copy(update={'remote_ci':RemoteCI(pr_number=1,required_checks=('unit',),
                         trusted_workflow_digest=sha256(workflow).hexdigest())})
    state=test.submit(spec); head=state['snapshot']['head_commit']
    attestation=dict(head_sha=head,base_sha=spec.task.base_sha,tested_sha=head,
                     task_digest=digest(spec.task),policy_digest=digest(spec.policy),run_id=5)
    def api(path,raw=False):
        if path.endswith('/pulls/1'):return {'head':{'sha':head},'base':{'sha':head},'merge_commit_sha':None}
        if '/contents/' in path:return {'content':base64.b64encode(workflow).decode()}
        if '/check-runs?' in path:return {'check_runs':[dict(id=1,name='unit',app={'slug':'github-actions'},head_sha=head,status='completed',conclusion='success',details_url='https://github.com/owner/repo/actions/runs/5/job/1')]}
        if path.endswith('/runs/5'):return {'head_sha':head,'conclusion':'success'}
        if path.endswith('/runs/5/artifacts'):return {'artifacts':[dict(id=9,name='ai-company-evidence',expired=False,size_in_bytes=500)]}
        if path.endswith('/9/zip'):
            out=BytesIO()
            with ZipFile(out,'w') as z:z.writestr('evidence.json',json.dumps(attestation))
            return out.getvalue()
        raise AssertionError(path)
    verifier=Verifier(test.root)
    return spec,state,attestation,verifier,api


def test_attested_candidate_and_task_policy(test):
    spec,state,attestation,v,api=fixture_remote(test)
    with patch.object(v,'_api',side_effect=api):
        test.assertEqual(v.remote(spec,state)['source'],'github')
        attestation['policy_digest']='0'*64
        test.assertIsNone(v.remote(spec,state))


def test_missing_tested_sha_cannot_match_null_merge(test):
    spec,state,attestation,v,api=fixture_remote(test);attestation.pop('tested_sha')
    with patch.object(v,'_api',side_effect=api):test.assertIsNone(v.remote(spec,state))


def test_changed_workflow_rejected(test):
    spec,state,attestation,v,api=fixture_remote(test)
    spec=spec.model_copy(update={'remote_ci':spec.remote_ci.model_copy(update={'trusted_workflow_digest':'0'*64})})
    with patch.object(v,'_api',side_effect=api),test.assertRaises(ExecutionBlocked):v.remote(spec,state)


EvidenceTests.test_attested_candidate_and_task_policy=test_attested_candidate_and_task_policy
EvidenceTests.test_missing_tested_sha_cannot_match_null_merge=test_missing_tested_sha_cannot_match_null_merge
EvidenceTests.test_changed_workflow_rejected=test_changed_workflow_rejected
