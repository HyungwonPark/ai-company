"""Independent Git fixtures and mocked GitHub mutations; never push to a real remote."""
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from ai_company.automation_git import AutomationGit
from ai_company.runtime import ExecutionBlocked


WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/automation-evidence.yml'


class Crash(BaseException):
    pass


class AutomationGitTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        self.git(self.source, 'init', '-q', '-b', 'main')
        self.git(self.source, 'remote', 'add', 'origin', 'https://github.com/owner/repo.git')
        (self.source / 'src').mkdir()
        (self.source / 'src/common.txt').write_text('base\n')
        ci = self.source / '.github/workflows'
        ci.mkdir(parents=True)
        (ci / WORKFLOW.name).write_bytes(WORKFLOW.read_bytes())
        self.commit(self.source)
        self.base = self.git(self.source, 'rev-parse', 'HEAD')
        self.config = SimpleNamespace(repository='owner/repo', source_clone=str(self.source),
                                      base_sha=self.base, base_branch='main', ci=SimpleNamespace(
            workflow_path='.github/workflows/automation-evidence.yml',
            workflow_digest=sha256(WORKFLOW.read_bytes()).hexdigest()))
        self.helper = AutomationGit(self.root / 'owned', self.config)
        self.manifest = {'base_sha': self.base, 'task_digest': 'a' * 64, 'policy_digest': 'b' * 64}

    @staticmethod
    def git(path, *args):
        return subprocess.check_output(['git', '-C', str(path), *args], text=True, stderr=subprocess.DEVNULL).strip()

    def commit(self, clone):
        self.git(clone, 'add', '.')
        self.git(clone, '-c', 'user.name=fixture', '-c', 'user.email=fixture@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '-qm', 'fixture')

    def contribution(self, key, path=None, content=None, base=None):
        base = base or self.base
        clone = self.helper.clone(key, base)
        file = clone / (path or 'src/' + key + '.txt')
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content or key + '\n')
        self.commit(clone)
        return {'clone': str(clone), 'base_sha': base, 'candidate_sha': self.git(clone, 'rev-parse', 'HEAD')}

    def candidate(self):
        item = self.contribution('writer')
        return self.helper.aggregate('integration', self.base, {'writer': item}, self.manifest)

    def test_clone_has_independent_objects_and_repeated_call_preserves_worker_edits(self):
        clone = self.helper.clone('role-a', self.base)
        self.assertTrue((clone / '.git').is_dir())
        self.assertNotEqual(self.git(clone, 'rev-parse', '--git-common-dir'),
                            str(self.source / '.git'))
        object_path = Path('objects') / self.base[:2] / self.base[2:]
        self.assertNotEqual((clone / '.git' / object_path).stat().st_ino,
                            (self.source / '.git' / object_path).stat().st_ino)
        self.assertFalse((clone / '.git/objects/info/alternates').exists())
        (clone / 'src/partial.txt').write_text('uncommitted work')
        self.assertEqual(self.helper.clone('role-a', self.base), clone)
        self.assertEqual((clone / 'src/partial.txt').read_text(), 'uncommitted work')
        with self.assertRaises(ExecutionBlocked):
            self.helper.clone('role-a', '1' * 40)

    def test_unowned_destination_is_not_adopted_or_overwritten(self):
        clone = self.helper.clone('known', self.base)
        record = self.helper._record('clone', 'known')
        record.unlink()
        with self.assertRaisesRegex(ExecutionBlocked, 'unowned'):
            self.helper.clone('known', self.base)
        self.assertEqual(self.git(clone, 'rev-parse', 'HEAD'), self.base)

    def test_clone_checkout_completed_before_record_crash_is_recovered(self):
        original = self.helper._save
        def crash_before_ready(path, value):
            if path.name.startswith('clone-') and value.get('ready'):
                raise Crash()
            return original(path, value)
        with patch.object(self.helper, '_save', side_effect=crash_before_ready):
            with self.assertRaises(Crash):
                self.helper.clone('interrupted', self.base)
        clone = self.helper.clone('interrupted', self.base)
        self.assertEqual(self.git(clone, 'rev-parse', 'HEAD'), self.base)
        self.assertTrue(self.helper._read(self.helper._record('clone', 'interrupted'))['ready'])

    def test_aggregation_is_idempotent_and_request_binds_exact_candidate_inputs(self):
        contributions = {key: self.contribution(key) for key in ('a', 'b')}
        clone, head = self.helper.aggregate('join', self.base, contributions, self.manifest)
        self.assertEqual((clone / 'src/a.txt').read_text(), 'a\n')
        self.assertEqual((clone / 'src/b.txt').read_text(), 'b\n')
        self.assertEqual(json.loads((clone / '.ai-company-ci/request.json').read_text()), self.manifest)
        self.assertEqual(self.helper.aggregate('join', self.base, contributions, self.manifest), (clone, head))
        self.assertEqual(self.git(clone, 'status', '--porcelain'), '')
        changed = dict(self.manifest, task_digest='c' * 64)
        with self.assertRaisesRegex(ExecutionBlocked, 'another candidate set'):
            self.helper.aggregate('join', self.base, contributions, changed)
        self.assertEqual(self.git(clone, 'rev-parse', 'HEAD'), head)

    def test_checkout_commit_crash_recovers_without_duplicate_merge(self):
        contribution = self.contribution('a')
        original = self.helper._save
        def crash_after_checkout(path, value):
            if path.name.startswith('aggregate-') and value.get('completed') and value.get('pending') is None:
                raise Crash()
            return original(path, value)
        with patch.object(self.helper, '_save', side_effect=crash_after_checkout):
            with self.assertRaises(Crash):
                self.helper.aggregate('recover', self.base, {'a': contribution}, self.manifest)
        pending = self.helper._read(self.helper._record('aggregate', 'recover'))['pending']
        clone, head = self.helper.aggregate('recover', self.base, {'a': contribution}, self.manifest)
        self.assertEqual(self.git(clone, 'rev-parse', 'HEAD^'), pending['after'])
        self.assertEqual(self.helper.aggregate('recover', self.base, {'a': contribution}, self.manifest), (clone, head))

    def test_conflict_leaves_index_clean_and_preserves_owned_role_outputs(self):
        a = self.contribution('a', 'src/common.txt', 'first\n')
        b = self.contribution('b', 'src/common.txt', 'second\n')
        with self.assertRaisesRegex(ExecutionBlocked, 'merge-tree'):
            self.helper.aggregate('conflict', self.base, {'a': a, 'b': b}, self.manifest)
        clone = self.helper.clone('aggregate:conflict', self.base)
        self.assertEqual(self.git(clone, 'status', '--porcelain'), '')
        self.assertEqual((clone / 'src/common.txt').read_text(), 'first\n')
        self.assertEqual((Path(b['clone']) / 'src/common.txt').read_text(), 'second\n')

    def test_dependencies_clone_owned_aggregate_and_final_base_remains_pr_base(self):
        a = self.contribution('a')
        context, context_sha = self.helper.aggregate('context', self.base, {'a': a},
            {'purpose': 'dependency_context', 'plan_digest': 'd' * 64})
        self.assertFalse((context / '.ai-company-ci/request.json').exists())
        b = self.contribution('dependent', base=context_sha)
        self.assertEqual((Path(b['clone']) / 'src/a.txt').read_text(), 'a\n')
        clone, head = self.helper.aggregate('final', self.base, {'a': a, 'b': b}, self.manifest)
        repair = self.contribution('repair', 'src/a.txt', 'repaired\n', base=head)
        repaired, _ = self.helper.aggregate('revision', head, {'a': repair}, self.manifest)
        self.assertEqual((repaired / 'src/a.txt').read_text(), 'repaired\n')
        self.assertEqual(json.loads((repaired / '.ai-company-ci/request.json').read_text())['base_sha'], self.base)

    def test_changed_or_foreign_contribution_and_role_ci_metadata_are_rejected(self):
        a = self.contribution('a')
        bad = dict(a, candidate_sha=self.base)
        with self.assertRaisesRegex(ExecutionBlocked, 'HEAD differs'):
            self.helper.aggregate('bad-head', self.base, {'a': bad}, self.manifest)
        bad = dict(a, clone=str(self.source))
        with self.assertRaisesRegex(ExecutionBlocked, 'outside the owned'):
            self.helper.aggregate('foreign', self.base, {'a': bad}, self.manifest)
        metadata = self.contribution('metadata', '.ai-company-ci/request.json', '{}')
        with self.assertRaisesRegex(ExecutionBlocked, 'program-owned CI'):
            self.helper.aggregate('bad-metadata', self.base, {'a': metadata}, self.manifest)

    def remote(self, helper):
        state = {'heads': {'main': self.base}, 'prs': [], 'pushes': 0, 'creates': 0}
        original = helper._git
        def git(clone, *args, **kwargs):
            if args[0] == 'push':
                self.assertNotIn('--force', args)
                self.assertNotIn('--force-with-lease', args)
                head, ref = args[-1].split(':')
                state['heads'][ref.removeprefix('refs/heads/')] = head
                state['pushes'] += 1
                return ''
            return original(clone, *args, **kwargs)
        def api(method, path, payload=None):
            if method == 'POST':
                state['creates'] += 1
                self.assertTrue(payload['draft'])
                pr = {'number': 7, 'html_url': 'https://github.com/owner/repo/pull/7', 'state': 'open', 'draft': True,
                      'head': {'ref': payload['head'], 'sha': state['heads'][payload['head']], 'repo': {'full_name': 'owner/repo'}},
                      'base': {'ref': payload['base'], 'sha': self.base}}
                state['prs'].append(pr)
                return pr
            return state['prs'] if '?' in path else state['prs'][0]
        return state, git, api

    def test_publish_reconciles_repeated_draft_request_and_rejects_external_ref_changes(self):
        clone, head = self.candidate()
        remote, git, api = self.remote(self.helper)
        with patch.object(self.helper, '_git', side_effect=git), patch.object(self.helper, '_api', side_effect=api), \
                patch.object(self.helper, '_remote_head', side_effect=lambda clone, branch: remote['heads'].get(branch)):
            first = self.helper.publish(clone, 'automation/candidate', 'main', 'title', 'body\nwith newlines')
            self.assertEqual(first['head_sha'], head)
            self.assertEqual(self.helper.publish(clone, 'automation/candidate', 'main', 'title', 'body\nwith newlines'), first)
            self.assertEqual((remote['pushes'], remote['creates']), (1, 1))
            remote['heads']['automation/candidate'] = self.base
            with self.assertRaisesRegex(ExecutionBlocked, 'unowned or has changed'):
                self.helper.publish(clone, 'automation/candidate', 'main', 'title', 'body\nwith newlines')
            self.assertEqual(remote['pushes'], 1)

    def test_publish_rejects_unowned_existing_branch_base_drift_and_workflow_drift(self):
        clone, head = self.candidate()
        remote, git, api = self.remote(self.helper)
        with patch.object(self.helper, '_git', side_effect=git), patch.object(self.helper, '_api', side_effect=api), \
                patch.object(self.helper, '_remote_head', side_effect=lambda clone, branch: remote['heads'].get(branch)):
            remote['heads']['automation/unowned'] = head
            with self.assertRaisesRegex(ExecutionBlocked, 'unowned'):
                self.helper.publish(clone, 'automation/unowned', 'main', 'title', 'body')
            remote['heads']['main'] = '1' * 40
            with self.assertRaisesRegex(ExecutionBlocked, 'PR base changed'):
                self.helper.publish(clone, 'automation/new', 'main', 'title', 'body')
            self.config.ci.workflow_digest = '0' * 64
            with self.assertRaisesRegex(ExecutionBlocked, 'approved definition'):
                self.helper.publish(clone, 'automation/new', 'main', 'title', 'body')
            self.assertEqual(remote['pushes'], 0)

    def test_publish_unknown_api_outcome_recovers_existing_draft_without_duplicate(self):
        clone, _ = self.candidate()
        remote, git, api = self.remote(self.helper)
        fail = [True]
        def interrupted_api(method, path, payload=None):
            value = api(method, path, payload)
            if method == 'POST' and fail.pop():
                raise Crash()
            return value
        with patch.object(self.helper, '_git', side_effect=git), patch.object(self.helper, '_api', side_effect=interrupted_api), \
                patch.object(self.helper, '_remote_head', side_effect=lambda clone, branch: remote['heads'].get(branch)):
            with self.assertRaises(Crash):
                self.helper.publish(clone, 'automation/recovery', 'main', 'title', 'body')
            result = self.helper.publish(clone, 'automation/recovery', 'main', 'title', 'body')
            self.assertEqual(result['number'], 7)
            self.assertEqual((remote['pushes'], remote['creates']), (1, 1))

    def test_workflow_emits_exact_execution_binding_and_rejects_wrong_head_or_base(self):
        clone, head = self.candidate()
        lines = WORKFLOW.read_text().splitlines()
        start = next(i for i, line in enumerate(lines) if '# AUTOMATION_EVIDENCE_SCRIPT:' in line)
        end = next(i for i in range(start, len(lines)) if lines[i] == '          PY')
        script = '\n'.join(line[10:] for line in lines[start:end])
        environment = dict(os.environ, EVIDENCE_HEAD_SHA=head, EVIDENCE_PR_BASE_SHA=self.base,
            EVIDENCE_REPOSITORY='owner/repo', EVIDENCE_RUN_ID='42', EVIDENCE_RUN_ATTEMPT='2',
            EVIDENCE_WORKFLOW_REF='owner/repo/.github/workflows/automation-evidence.yml@refs/heads/automation/fixture',
            EVIDENCE_WORKFLOW_SHA=head, RUNNER_TEMP=str(self.root / 'runner'))
        Path(environment['RUNNER_TEMP']).mkdir()
        result = subprocess.run(['python3', '-c', script], cwd=clone, env=environment, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        evidence = json.loads((self.root / 'runner/ai-company-evidence/evidence.json').read_text())
        self.assertEqual({key: evidence[key] for key in self.manifest}, self.manifest)
        self.assertEqual((evidence['head_sha'], evidence['tested_sha'], evidence['run_id'], evidence['run_attempt']),
                         (head, head, 42, 2))
        for key, value in (('EVIDENCE_HEAD_SHA', '0' * 40), ('EVIDENCE_PR_BASE_SHA', '0' * 40),
                           ('EVIDENCE_WORKFLOW_REF', 'owner/repo/.github/workflows/unapproved.yml@main')):
            with self.subTest(key=key):
                invalid = dict(environment, **{key: value})
                result = subprocess.run(['python3', '-c', script], cwd=clone, env=invalid, text=True, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
