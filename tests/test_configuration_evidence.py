"""Configuration observations cannot substitute for provider telemetry."""
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from ai_company.adapters.configuration_evidence import codex_turn_configuration, claude_control_configuration


class ConfigurationEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.sid='11111111-1111-1111-1111-111111111111'
        self.path=self.root/'sessions/2026/09/14'/('rollout-time-'+self.sid+'.jsonl');self.path.parent.mkdir(parents=True)
        self.records=[{'type':'session_meta','payload':{'id':self.sid,'cli_version':'0.154.0','cwd':str(self.root)}},
                      {'type':'turn_context','timestamp':datetime.fromtimestamp(100,timezone.utc).isoformat(),
                       'payload':{'turn_id':'old','cwd':str(self.root),'model':'other-model','effort':'low'}},
                      {'type':'turn_context','timestamp':datetime.fromtimestamp(200,timezone.utc).isoformat(),
                       'payload':{'turn_id':'current','cwd':str(self.root),'model':'gpt-6-astra','effort':'high'}}]

    def read(self):
        self.path.write_text('\n'.join(json.dumps(r) for r in self.records)+'\n')
        return codex_turn_configuration(self.sid,self.root,190,210,self.root)

    def test_only_current_execution_context_is_selected(self):
        result=self.read();self.assertEqual(result['status'],'observed')
        self.assertEqual([c['turn_id'] for c in result['contexts']],['current'])
        self.assertFalse(result['backend_model_verified'])

    def test_other_session_or_unsupported_cli_version_cannot_supply_evidence(self):
        for field,value in [('id','other-session'),('cli_version','0.999.0')]:
            original=self.records[0]['payload'][field];self.records[0]['payload'][field]=value
            self.assertEqual(self.read()['status'],'unavailable')
            self.records[0]['payload'][field]=original

    def test_different_worktree_context_is_not_accepted(self):
        self.records[-1]['payload']['cwd']='/another/worktree'
        self.assertEqual(self.read()['status'],'unavailable')

    def test_claude_capability_is_not_an_applied_setting(self):
        event={'type':'control_response','response':{'request_id':'probe','subtype':'success','response':{
            'models':[{'resolvedModel':'claude-opus-5','supportedEffortLevels':['high','xhigh']}],
            'account':{'do_not_copy':'private'}}}}
        result=claude_control_configuration(event,'probe','claude-opus-5')
        self.assertEqual(result['supported_efforts'],['high','xhigh'])
        self.assertIsNone(result['applied_ultracode']);self.assertIsNone(result['applied_effort'])
        self.assertNotIn('account',result)
        self.assertIsNone(claude_control_configuration(event,'another','claude-opus-5'))

    def test_model_self_report_is_never_configuration_evidence(self):
        self.assertIsNone(claude_control_configuration({'type':'assistant','message':{'text':'Ultracode is enabled'}},'probe','claude-opus-5'))
