"""Configuration observations cannot substitute for provider telemetry."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from ai_company.adapters.configuration_evidence import (codex_cli_identity, codex_configuration_reason,
    codex_turn_configuration, claude_control_configuration)


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

    def test_inspected_0157_rollout_profile_and_legacy_0154_are_explicit(self):
        old = self.read()
        self.assertEqual(old['profile'], 'codex-0.154-turn-context-v1')
        self.records[0]['payload']['cli_version'] = '0.157.0'
        newer = self.read()
        self.assertEqual(newer['status'], 'observed')
        self.assertEqual(newer['profile'], 'codex-0.157-turn-context-v1')
        self.assertIsNone(codex_configuration_reason(newer, session_id=self.sid,
            started_at=190, ended_at=210, model='gpt-6-astra', reasoning_effort='high'))
        newer.pop('profile')
        self.assertEqual(codex_configuration_reason(newer, session_id=self.sid,
            started_at=190, ended_at=210), 'missing_configuration_evidence')
        newer['cli_version'] = ['0.157.0']
        self.assertEqual(codex_configuration_reason(newer, session_id=self.sid,
            started_at=190, ended_at=210), 'unsupported_cli_version')
        old.pop('profile')
        self.assertIsNone(codex_configuration_reason(old, session_id=self.sid,
            started_at=190, ended_at=210))
        old['status'] = 'unavailable'
        self.assertEqual(codex_configuration_reason(old, session_id=self.sid,
            started_at=190, ended_at=210), 'missing_configuration_evidence')

    def test_redacted_e1_0157_envelope_is_observed_without_original_content(self):
        fixture = Path(__file__).parent / 'fixtures/codex-0.157-e1-redacted.jsonl'
        self.path.write_text(fixture.read_text().replace('__SESSION_ID__', self.sid)
                             .replace('__WORKTREE__', str(self.root)))
        at = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
        observed = codex_turn_configuration(self.sid, self.root, at, at + 2, self.root)
        self.assertEqual(observed['status'], 'observed')
        self.assertEqual(observed['profile'], 'codex-0.157-turn-context-v1')
        self.assertFalse(observed['backend_model_verified'])

    def test_inspected_0157_shape_rejects_conflict_and_missing_turn(self):
        self.records[0]['payload']['cli_version'] = '0.157.0'
        evidence = self.read()
        evidence['contexts'][0]['model'] = 'different'
        self.assertEqual(codex_configuration_reason(evidence, session_id=self.sid,
            started_at=190, ended_at=210, model='gpt-6-astra'), 'configuration_mismatch')
        evidence['contexts'][0]['model'] = 'gpt-6-astra'
        evidence['contexts'][0]['recorded_at'] = 220
        self.assertEqual(codex_configuration_reason(evidence, session_id=self.sid,
            started_at=190, ended_at=210), 'configuration_mismatch')
        self.records[-1]['payload']['turn_id'] = None
        self.assertEqual(self.read()['status'], 'unavailable')

    def test_worker_native_identity_checks_both_version_and_final_binary(self):
        native = self.root / 'native-codex'
        native.write_bytes(b'\x7fELF' + b'a' * 100)
        native.chmod(0o700)
        entry = self.root / 'codex'; entry.symlink_to(native)
        def version(argv, **kwargs):
            self.assertEqual(argv, [str(native), '--version'])
            return subprocess.CompletedProcess(argv, 0, 'codex-cli 0.157.0\n', '')
        identity, reason = codex_cli_identity(path=str(self.root), run=version)
        self.assertIsNone(reason)
        self.assertEqual(identity['path'], str(native))
        replacement = self.root / 'replacement'
        replacement.write_bytes(b'\x7fELF' + b'b' * 100)
        replacement.chmod(0o700)
        entry.unlink(); entry.symlink_to(replacement)
        changed, reason = codex_cli_identity(path=str(self.root), run=lambda argv, **kwargs:
            subprocess.CompletedProcess(argv, 0, 'codex-cli 0.999.0\n', ''))
        self.assertIsNone(changed)
        self.assertEqual(reason, 'unsupported_cli_version')
        script = self.root / 'script'; script.write_text('#!/bin/sh\nexit 0\n'); script.chmod(0o700)
        self.assertEqual(codex_cli_identity(str(script), run=version)[1],
                         'codex_native_executable_unverified')

    def test_unobserved_mixed_version_resume_is_blocked_before_call(self):
        self.read()  # 0.154.0 session metadata is stored in the isolated fixture.
        native = self.root / 'native-codex'
        native.write_bytes(b'\x7fELF' + b'c' * 100); native.chmod(0o700)
        identity, reason = codex_cli_identity(str(native), session_id=self.sid,
            codex_home=self.root, run=lambda argv, **kwargs:
                subprocess.CompletedProcess(argv, 0, 'codex-cli 0.157.0\n', ''))
        self.assertIsNone(identity)
        self.assertEqual(reason, 'codex_resume_profile_unverified')

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

    def test_worker_preflight_uses_native_cli_before_any_reservation(self):
        from ai_company.management import ManagementStore
        from ai_company.service_worker import preflight
        from ai_company.shared_calls import SharedCallLedger
        from test_execution_specs import catalog_config
        state = self.root / 'state'
        management = ManagementStore(state); management.close()
        ledger = SharedCallLedger.initialize(self.root / 'calls.sqlite', [
            ('codex', 'private-codex', 'shared-codex', 'AVAILABLE', None, None, 0, 0, 0, 0)])
        self.addCleanup(ledger.close)
        config = catalog_config().model_copy(update={'mode': 'live'})
        source = self.root / 'automation.json'; source.write_text(config.model_dump_json())
        with patch('ai_company.adapters.configuration_evidence.codex_cli_identity',
                   return_value=(None, 'unsupported_cli_version')) as probe:
            blocked = preflight(state, 'automation', source, None, ledger.path, False)
        self.assertEqual(blocked['reasons'], ['unsupported_cli_version'])
        probe.assert_called_once_with()
        self.assertEqual(ledger.db.execute('SELECT count(*) FROM reservations').fetchone()[0], 0)
