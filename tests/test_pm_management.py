"""Persistent PM proposals and master confirmations; no model calls or deployment."""
from concurrent.futures import ThreadPoolExecutor
import copy
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from ai_company.contracts import digest
from ai_company.management import ManagementError, ManagementStore
from ai_company.management_server import ManagementHTTPServer
from tests.legacy_pm import use_legacy_requests


class PMManagementTests(unittest.TestCase):
    def setUp(self):
        use_legacy_requests(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = 1000
        self.store = ManagementStore(self.root, clock=lambda: self.now)
        self.project = self.store.create_project({"name": "Automatic PM", "goal": "Build a small API", "roles": [{"name": "Old role", "responsibility": "Prior work"}]})
        self.pid = self.project["id"]
        self.content = {"summary": "Build and independently verify an API", "roles": [
            {"key": "backend", "name": "Backend", "responsibility": "API", "goal": "Implement endpoint",
             "acceptance": ["Endpoint verified"], "allowed_paths": ["src/"], "depends_on": []},
            {"key": "tests", "name": "Tests", "responsibility": "Independent verification", "goal": "Verify endpoint",
             "acceptance": ["Failure cases tested"], "allowed_paths": ["tests/"], "depends_on": []}],
            "completion_criteria": ["Checks and independent final review pass"]}

    def tearDown(self):
        self.store.close(); self.tmp.cleanup()

    def propose(self):
        message = self.store.post_message(self.pid, {"content": "Plan this goal"})
        request = self.store.get_pm_request(message["id"])
        self.store.save_pm_request({**request, "state": "running", "configuration_digest": "c" * 64, "mode": "fixture"}, expected_state="pending")
        plan = self.store.complete_pm_request(message["id"], self.content, evidence={"source": "test-stub"})
        return message, plan

    def confirm_body(self, plan, **changes):
        return {"plan_digest": plan["digest"], "base_harness_version": plan["base_harness_version"], "idempotency_key": "confirm-once-1", **changes}

    def assert_blocked(self, code, function, *args):
        with self.assertRaises(ManagementError) as error:
            function(*args)
        self.assertEqual(error.exception.code, code)

    def translated_plan(self, plan):
        from ai_company.collaboration import plan_document
        from ai_company.translations import TranslationStore, segments
        source = plan_document(self.pid, plan)
        from ai_company.dispatcher import Dispatcher
        initializer = Dispatcher(self.root, clock=lambda: self.now)
        initializer.close()
        store = TranslationStore(self.store.db, clock=lambda: self.now)
        with self.store.db:
            self.store.db.execute("INSERT OR IGNORE INTO credential_groups VALUES ('codex','plan-test','plan-test')")
            self.store.db.execute("INSERT OR IGNORE INTO quota_groups VALUES ('plan-test','AVAILABLE',NULL,NULL)")
        store.sync(self.pid, [source], {'credential_ref': 'plan-test', 'quota_group': 'plan-test'})
        job = store.claim('fixture-translator', adapter_ready=True)
        translations = {
            'Build and independently verify an API': 'API를 개발하고 독립적으로 검증합니다',
            'Backend': '서버', 'API': 'API 담당', 'Implement endpoint': '엔드포인트 구현',
            'Endpoint verified': '엔드포인트 검증', 'Tests': '검사',
            'Independent verification': '독립 검증', 'Verify endpoint': '엔드포인트 확인',
            'Failure cases tested': '실패 사례 검사',
            'Checks and independent final review pass': '검사와 독립 최종 검수 통과',
        }
        store.finish(job['id'], job['lease_token'], {'category': 'success',
            'fields': {key: translations[value] for key, value in segments(source['fields']).items()}})
        self.assertEqual(store.get_result(job['id'])['status'], 'completed')
        return {'id': job['id'], 'source_digest': source['source_digest']}

    def test_translated_confirmation_preserves_original_and_exact_run_with_audit(self):
        _, plan = self.propose(); original = copy.deepcopy(plan['content'])
        reference = self.translated_plan(plan)
        body = self.confirm_body(plan, displayed_translation=reference)
        result = self.store.confirm_plan(self.pid, plan['id'], body)
        self.assertEqual(result['plan']['content'], original)
        self.assertEqual(result['plan']['digest'], plan['digest'])
        self.assertEqual(result['plan']['displayed_translation'], reference)
        self.assertEqual(result, self.store.confirm_plan(self.pid, plan['id'], body))
        self.assertEqual(len(self.store.run_records()), 1)
        self.assertEqual(result['run']['plan_digest'], plan['digest'])
        self.assertEqual(self.store.overview(self.pid)['project']['harness_content'], json.dumps(original, ensure_ascii=False, indent=2))

    def test_plan_translation_mismatch_rolls_back_confirmation(self):
        _, plan = self.propose(); reference = self.translated_plan(plan)
        wrong = dict(reference, source_digest='f' * 64)
        self.assert_blocked('translation_mismatch', self.store.confirm_plan, self.pid, plan['id'], self.confirm_body(plan, displayed_translation=wrong))
        self.assertEqual(self.store.get_plan(self.pid, plan['id'])['status'], 'proposed')
        self.assertEqual(self.store.run_records(), [])
        row = self.store.db.execute('SELECT document FROM translation_jobs WHERE id=?', (reference['id'],)).fetchone()
        artifact = json.loads(row[0]); artifact['source']['protected']['content']['roles'][0]['allowed_paths'] = ['secret/']
        with self.store.db:
            self.store.db.execute('UPDATE translation_jobs SET document=? WHERE id=?', (json.dumps(artifact), reference['id']))
        self.assert_blocked('translation_mismatch', self.store.confirm_plan, self.pid, plan['id'], self.confirm_body(plan, displayed_translation=reference))
        self.assertEqual(self.store.run_records(), [])

    def test_message_request_atomic_persistence_and_snapshot(self):
        message = self.store.post_message(self.pid, {"content": "Build a plan"})
        self.store.close(); self.store = ManagementStore(self.root, clock=lambda: self.now)
        request = self.store.get_pm_request(message["id"])
        self.assertEqual(request["state"], "pending")
        self.assertEqual(request["goal_digest"], digest(self.project["goal"]))
        self.assertEqual(request["request_revision"], 1)
        with patch.object(self.store, "_event", side_effect=RuntimeError("crash")):
            with self.assertRaises(RuntimeError):
                self.store.post_message(self.pid, {"content": "Must roll back"})
        overview = self.store.overview(self.pid)
        self.assertEqual(len(overview["messages"]), 1)
        self.assertEqual(len(overview["pm_requests"]), 1)
        self.assertEqual(overview["project"]["request_revision"], 1)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM session_jobs").fetchone()[0], 0)

    def test_plain_goal_starts_pm_atomically_without_confirming_or_developing(self):
        project = self.store.create_project({'name': '내 프로젝트', 'goal': '역할 진행을 쉽게 보고 싶어요', 'start_pm': True})
        self.store.close(); self.store = ManagementStore(self.root, clock=lambda: self.now)
        overview = self.store.overview(project['id'])
        self.assertEqual(overview['project']['request_revision'], 1)
        self.assertEqual(len(overview['pm_requests']), 1)
        self.assertEqual(overview['pm_requests'][0]['state'], 'pending')
        self.assertEqual(overview['pm_requests'][0]['goal'], project['goal'])
        self.assertEqual(overview['pm_requests'][0]['conversation_context']['messages'], [])
        for key in ('roles', 'plans', 'runs'):
            self.assertEqual(overview[key], [])
        for table in ('flow_tasks', 'session_jobs'):
            self.assertEqual(self.store.db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0)
        before = self.store.list_projects()
        with patch.object(self.store, '_append_pm_request', side_effect=RuntimeError('crash')):
            with self.assertRaises(RuntimeError):
                self.store.create_project({'name': '롤백', 'goal': '함께 계획', 'start_pm': True})
        self.assertEqual(self.store.list_projects(), before)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM management_harnesses').fetchone()[0], len(before))

    def test_followup_carries_project_local_immutable_conversation_and_proposal(self):
        first, plan = self.propose()
        other = self.store.create_project({'name': '다른 프로젝트', 'goal': '비공개 목표', 'start_pm': True})
        self.store.post_message(other['id'], {'content': '다른 프로젝트의 요청'})
        followup = self.store.post_message(self.pid, {'content': '검사 역할의 책임을 더 구체적으로 나눠주세요'})
        request = self.store.get_pm_request(followup['id'])
        context = request['conversation_context']
        self.assertEqual(context['messages'][0]['id'], first['id'])
        self.assertEqual([item['role'] for item in context['messages']], ['user', 'assistant'])
        self.assertEqual(context['previous_proposal']['content'], plan['content'])
        self.assertEqual(context['previous_proposal']['digest'], plan['digest'])
        self.assertNotIn('다른 프로젝트', json.dumps(context, ensure_ascii=False))
        changed = copy.deepcopy(request); changed['conversation_context']['previous_proposal']['content']['summary'] = '변조'
        self.assert_blocked('request_mismatch', self.store.save_pm_request, changed)
        self.assert_blocked('stale_plan', self.store.confirm_plan, self.pid, plan['id'], self.confirm_body(plan))
        self.assertEqual(self.store.run_records(), [])
        self.store.post_message(self.pid, {'content': '추가 의견'})
        self.assertEqual(self.store.get_pm_request(followup['id']), request)

    def test_conversation_is_bounded_without_mutating_saved_messages(self):
        for index in range(10):
            self.store.post_message(self.pid, {'content': str(index) + '가' * 7000})
        last = self.store.pm_requests(self.pid)[-1]['conversation_context']
        self.assertLessEqual(sum(len(item['content']) for item in last['messages']), 16000)
        self.assertTrue(all(item['truncated'] for item in last['messages']))
        self.assertEqual(len(self.store.overview(self.pid)['messages'][0]['content']), 7001)

    def test_proposal_does_not_activate_roles_or_harness_and_completion_is_idempotent(self):
        message, plan = self.propose()
        overview = self.store.overview(self.pid)
        self.assertEqual(plan["status"], "proposed")
        self.assertEqual(overview["project"]["harness_version"], 1)
        self.assertEqual([r["name"] for r in overview["roles"]], ["Old role"])
        self.assertEqual(overview["runs"], [])
        self.assertEqual(self.store.complete_pm_request(message["id"], self.content, evidence={"source": "test-stub"}), plan)
        self.assertEqual(len(self.store.overview(self.pid)["messages"]), 2)
        self.assert_blocked("result_conflict", self.store.complete_pm_request, message["id"], {**self.content, "summary": "Different"})
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM flow_tasks").fetchone()[0], 0)

    def test_confirmation_creates_one_run_and_activates_plan_atomically(self):
        _, plan = self.propose()
        value = self.confirm_body(plan)
        confirmed = self.store.confirm_plan(self.pid, plan["id"], value)
        self.assertEqual(confirmed, self.store.confirm_plan(self.pid, plan["id"], value))
        self.assertEqual(len(self.store.run_records()), 1)
        self.assertEqual(confirmed["run"]["state"], "pending")
        self.assertEqual(set(confirmed["run"]["role_ids"]), {"backend", "tests"})
        overview = self.store.overview(self.pid)
        self.assertEqual(overview["project"]["harness_version"], 2)
        self.assertEqual([r["name"] for r in overview["roles"] if r.get("active")], ["Backend", "Tests"])
        self.assertFalse(overview["roles"][0]["active"])
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM session_jobs").fetchone()[0], 0)
        self.assert_blocked("idempotency_conflict", self.store.confirm_plan, self.pid, plan["id"], self.confirm_body(plan, plan_digest="b" * 64))
        self.assert_blocked("already_confirmed", self.store.confirm_plan, self.pid, plan["id"], self.confirm_body(plan, idempotency_key="different-key"))

    def test_confirmation_rollback_preserves_roles_harness_and_no_pending_run(self):
        _, plan = self.propose()
        with patch.object(self.store, "_event", side_effect=RuntimeError("crash before commit")):
            with self.assertRaises(RuntimeError):
                self.store.confirm_plan(self.pid, plan["id"], self.confirm_body(plan))
        overview = self.store.overview(self.pid)
        self.assertEqual(overview["project"]["harness_version"], 1)
        self.assertEqual([r["name"] for r in overview["roles"]], ["Old role"])
        self.assertEqual(overview["runs"], [])
        self.assertEqual(overview["plans"][0]["status"], "proposed")
        self.assertEqual(self.store.confirm_plan(self.pid, plan["id"], self.confirm_body(plan))["run"]["state"], "pending")

    def test_stale_message_goal_harness_and_mutated_plan_fail(self):
        _, plan = self.propose()
        self.assert_blocked("plan_mismatch", self.store.confirm_plan, self.pid, plan["id"], self.confirm_body(plan, plan_digest="a" * 64))
        self.assert_blocked("stale_plan", self.store.confirm_plan, self.pid, plan["id"], self.confirm_body(plan, base_harness_version=7))
        self.store.post_message(self.pid, {"content": "Change scope"})
        self.assert_blocked("stale_plan", self.store.confirm_plan, self.pid, plan["id"], self.confirm_body(plan))
        _, fresh = self.propose()
        draft = self.store.save_harness(self.pid, {"base_version": 1, "content": "New constraints"})
        self.store.activate_harness(self.pid, draft["version"], draft["digest"])
        self.assert_blocked("stale_plan", self.store.confirm_plan, self.pid, fresh["id"], self.confirm_body(fresh))
        _, newest = self.propose()
        altered = copy.deepcopy(newest); altered["content"]["summary"] = "Unreviewed content"
        with self.store.db:
            self.store.db.execute("UPDATE management_plans SET document=? WHERE id=?", (json.dumps(altered), newest["id"]))
        self.assert_blocked("plan_mismatch", self.store.confirm_plan, self.pid, newest["id"], self.confirm_body(newest))
        _, goal_plan = self.propose()
        project = self.store._project(self.pid); project["goal"] = "Another goal"
        with self.store.db:
            self.store.db.execute("UPDATE management_projects SET document=? WHERE id=?", (json.dumps(project), self.pid))
        self.assert_blocked("stale_plan", self.store.confirm_plan, self.pid, goal_plan["id"], self.confirm_body(goal_plan))
        self.assertEqual(self.store.run_records(), [])

    def test_late_pm_result_is_saved_as_stale_without_activation(self):
        message = self.store.post_message(self.pid, {"content": "Old request"})
        request = self.store.get_pm_request(message["id"])
        self.store.save_pm_request({**request, "state": "running", "configuration_digest": "c" * 64, "mode": "fixture"}, expected_state="pending")
        self.store.post_message(self.pid, {"content": "Newest request"})
        plan = self.store.complete_pm_request(message["id"], self.content)
        self.assertEqual(plan["status"], "stale")
        self.assertEqual(self.store.get_pm_request(message["id"])["state"], "stale")
        self.assert_blocked("stale_plan", self.store.confirm_plan, self.pid, plan["id"], self.confirm_body(plan))
        self.assertEqual(self.store.overview(self.pid)["project"]["harness_version"], 1)

    def test_worker_state_cas_and_identity_are_preserved(self):
        message = self.store.post_message(self.pid, {"content": "Plan"})
        request = self.store.get_pm_request(message["id"])
        self.store.save_pm_request({**request, "state": "running", "execution": {"id": "attempt-1"}, "configuration_digest": "c" * 64, "mode": "fixture"}, expected_state="pending")
        with self.assertRaises(ManagementError):
            self.store.save_pm_request({**request, "state": "running", "configuration_digest": "c" * 64, "mode": "fixture"}, expected_state="pending")
        self.assert_blocked("request_mismatch", self.store.save_pm_request, {**request, "goal": "replaced"})
        plan = self.store.complete_pm_request(message["id"], self.content)
        run = self.store.confirm_plan(self.pid, plan["id"], self.confirm_body(plan))["run"]
        self.store.save_run({**run, "state": "preparing", "roles": {}}, expected_state="pending")
        with self.assertRaises(ManagementError):
            self.store.save_run({**run, "state": "preparing"}, expected_state="pending")
        self.assert_blocked("run_mismatch", self.store.save_run, {**run, "harness_version": 999})

    def test_concurrent_confirmation_has_one_durable_run(self):
        _, plan = self.propose()
        def confirm():
            worker = ManagementStore(self.root)
            try:
                return worker.confirm_plan(self.pid, plan["id"], self.confirm_body(plan))
            finally:
                worker.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(lambda _: confirm(), range(2)))
        self.assertEqual(first, second)
        self.assertEqual(len(self.store.run_records()), 1)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM management_confirmations").fetchone()[0], 1)

    def test_fixture_project_confirmation_is_blocked(self):
        _, plan = self.propose()
        project = self.store._project(self.pid); project["source"] = "fixture"
        with self.store.db:
            self.store.db.execute("UPDATE management_projects SET document=? WHERE id=?", (json.dumps(project), self.pid))
        self.assert_blocked("fixture_only", self.store.confirm_plan, self.pid, plan["id"], self.confirm_body(plan))
        self.assertEqual(self.store.run_records(), [])

    def test_configuration_and_mode_bind_request_plan_run_and_reject_fixture_as_live(self):
        message = self.store.post_message(self.pid, {"content": "Plan"})
        request = self.store.get_pm_request(message["id"])
        claimed = self.store.save_pm_request({**request, "state": "running", "configuration_digest": "c" * 64, "mode": "live"}, expected_state="pending")
        self.assert_blocked("configuration_mismatch", self.store.save_pm_request, {**claimed, "configuration_digest": "d" * 64})
        self.assert_blocked("configuration_mismatch", self.store.save_pm_request, {**claimed, "mode": "fixture"})
        with self.assertRaises(ManagementError) as refused:
            self.store.complete_pm_request(message["id"], self.content, evidence={"source": "fixture"})
        self.assertEqual(refused.exception.code, "fixture_only")
        self.assertEqual(self.store.overview(self.pid)["plans"], [])
        plan = self.store.complete_pm_request(message["id"], self.content, evidence={"source": "unit_test", "scope": "stub"})
        confirmed = self.store.confirm_plan(self.pid, plan["id"], self.confirm_body(plan))
        self.assertEqual(confirmed["run"]["mode"], "live")
        self.assertEqual(confirmed["run"]["configuration_digest"], "c" * 64)
        self.assert_blocked("run_mismatch", self.store.save_run, {**confirmed["run"], "mode": "fixture"})
        self.assert_blocked("run_mismatch", self.store.save_run, {**confirmed["run"], "configuration_digest": "d" * 64})

    def test_waiting_pm_and_stale_proposal_are_truthful_in_overview(self):
        _, plan = self.propose()
        message = self.store.post_message(self.pid, {"content": "A new goal detail"})
        request = self.store.get_pm_request(message["id"])
        self.assertTrue(self.store.request_is_current(message["id"]))
        self.assertFalse(self.store.request_is_current(plan["request_id"]))
        self.store.save_pm_request({**request, "state": "waiting_quota", "wait_reason": "Shared account cooling down", "resume_at": 3000})
        overview = self.store.overview(self.pid)
        self.assertEqual(overview["plans"][0]["status"], "stale")
        self.assertEqual(overview["readiness"]["pm"], "waiting_quota")
        self.assertEqual(overview["pm_requests"][-1]["resume_at"], 3000)
        self.assertEqual(overview["runs"], [])

    def test_http_confirmation_requires_authenticated_same_origin_csrf(self):
        _, plan = self.propose()
        token = "test-secret-" + "x" * 32
        token_file = self.root / "token"; token_file.write_text(token); token_file.chmod(0o600)
        server = ManagementHTTPServer(("127.0.0.1", 0), self.root, token_file)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True); thread.start()
        def call(path, body, extra=None):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            connection.request("POST", path, json.dumps(body), {"Origin": server.origin, "Content-Type": "application/json", **(extra or {})})
            response = connection.getresponse(); result = json.loads(response.read()); headers = dict(response.getheaders()); status = response.status
            connection.close(); return status, result, headers
        try:
            path = f"/api/projects/{self.pid}/plans/{plan['id']}/confirm"
            self.assertEqual(call(path, self.confirm_body(plan))[0], 401)
            status, login, headers = call("/api/login", {"token": token})
            self.assertEqual(status, 200)
            auth = {"Cookie": headers["Set-Cookie"].split(";", 1)[0]}
            self.assertEqual(call(path, self.confirm_body(plan), auth)[0], 403)
            auth["X-CSRF-Token"] = login["csrf_token"]
            self.assertEqual(call(path, self.confirm_body(plan), {**auth, "Origin": "https://foreign.example"})[0], 403)
            status, result, _ = call(path, self.confirm_body(plan), auth)
            self.assertEqual(status, 200)
            self.assertEqual(result["run"]["state"], "pending")
            approval = self.store.request_approval(self.pid, {"title": "Accept candidate", "action": "accept report", "environment": "draft-pr",
                "artifact_sha": "a" * 40, "cost_usd": 0, "expires_at": server.clock() + 3600,
                "impact": "Record acceptance only", "rollback": "Reject draft", "verification": "unit test fixture"},
                idempotency_key="automation-candidate-1")
            status, decision, _ = call(f"/api/projects/{self.pid}/approvals/{approval['id']}/decisions",
                {"decision": "approve", "comment": "Reviewed", "subject_digest": approval["subject_digest"], "idempotency_key": "automation-decision-1"}, auth)
            self.assertEqual(status, 200)
            self.assertEqual(decision["approval"]["status"], "approved")
        finally:
            server.shutdown(); server.server_close(); thread.join()
