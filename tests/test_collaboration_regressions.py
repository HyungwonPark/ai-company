"""Independent source/approval/delivery invariants over isolated persisted facts."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from ai_company.collaboration import document_sources, project_collaboration
from ai_company.dispatcher import Dispatcher
from ai_company.management import ManagementError, ManagementStore
from ai_company.translations import TranslationStore, initialize, source_digest


class CollaborationRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = 1000.0
        self.store = ManagementStore(self.root, clock=lambda: self.now)
        self.addCleanup(self.store.close)
        self.dispatcher = Dispatcher(self.root, clock=lambda: self.now)
        self.addCleanup(self.dispatcher.close)
        self.project = self.store.create_project({"name": "Fixture project", "goal": "Review sources safely",
            "roles": [{"name": "Server", "responsibility": "Server work"},
                      {"name": "Console", "responsibility": "Console work"}]})
        self.pid = self.project["id"]
        self.roles = self.store.overview(self.pid)["roles"]
        self.db = self.store.db
        initialize(self.db)
        with self.db:
            self.db.execute("INSERT INTO credential_groups VALUES ('codex','fixture-account','shared-fixture')")
            self.db.execute("INSERT INTO quota_groups VALUES ('shared-fixture','AVAILABLE',NULL,NULL)")
        self.translations = TranslationStore(self.db, clock=lambda: self.now)
        self.config = {"credential_ref": "fixture-account", "quota_group": "shared-fixture"}
        self.subject = {"title": "Preview change", "action": "Preview only", "environment": "isolated",
                        "artifact_sha": "a" * 40, "cost_usd": 12.5, "expires_at": 2000,
                        "impact": "Read preview", "rollback": "Stop preview", "verification": "CI at exact candidate"}

    def seed_task(self, task_id, role_index=0):
        state = {"task_id": task_id, "created_at": self.now, "specification": {
            "task": {"goal": "Prepare " + task_id}, "worktree": "/fixture/" + task_id,
            "execution_scope": "contribution", "plan": {"plan_digest": "b" * 64},
            "agents": [{"agent_id": "fixture-agent", "provider": "codex", "model": "requested-model",
                        "reasoning_effort": "ultra", "quota_group": "shared-fixture"}]},
            "status": "RUNNING", "stage": "developer", "reason": "Fixture execution",
            "active": {"agent_id": "fixture-agent", "session_id": "fixture-session-1", "role": "developer",
                       "execution_id": task_id + "-generation-1", "generation": 1}, "executions": []}
        with self.dispatcher.db:
            self.dispatcher._save(state, "role_assigned")
        self.store.link_task(self.pid, self.roles[role_index]["id"], task_id)
        return state

    def projection(self):
        return project_collaboration(self.db, self.store.overview(self.pid))

    def approval_document(self, approval):
        return next(d for d in document_sources(self.store.overview(approval["project_id"]))
                    if d["id"] == "approval:" + approval["id"])

    def translated_approval(self, approval):
        document = self.approval_document(approval)
        self.translations.sync(approval["project_id"], [document], self.config)
        job = self.translations.claim("fixture-translator", adapter_ready=True)
        self.assertIsNotNone(job)
        self.assertTrue(self.translations.finish(job["id"], job["lease_token"], {
            "category": "success", "fields": {"title::0": "미리보기 변경", "action::0": "미리보기만 수행",
                "impact::0": "미리보기 확인", "rollback::0": "미리보기 중지"}}))
        result = self.translations.read(document)
        self.assertEqual(result["status"], "completed")
        return document, result

    def decision(self, approval, key="fixture-decision-1", displayed=None):
        return {"decision": "approve", "comment": "Reviewed original", "subject_digest": approval["subject_digest"],
                "idempotency_key": key, "displayed_translation": displayed}

    def counts(self):
        return {table: self.db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] for table in (
            "flow_tasks", "flow_events", "session_jobs", "management_events", "management_decisions", "translation_jobs")}

    def test_parallel_assignments_have_distinct_stable_transfers_and_real_shared_quota(self):
        self.seed_task("server-work", 0)
        self.seed_task("console-work", 1)
        with self.db:
            self.db.execute("UPDATE quota_groups SET state='COOLDOWN',resume_at=1300,reason='quota'")
        before = self.counts()
        first, second = self.projection(), self.projection()
        roles = [n for n in first["nodes"] if n["kind"] == "role"]
        self.assertEqual({n["current_task_id"] for n in roles}, {"server-work", "console-work"})
        for node in roles:
            self.assertEqual(node["status"], "RUNNING")
            self.assertEqual(node["assignment"]["quota"], {"status": "COOLDOWN", "reset_at": 1300})
            self.assertEqual(node["assignment"]["requested"]["model"], "requested-model")
            self.assertEqual(node["assignment"]["observed"]["status"], "unavailable")
            self.assertFalse(node["assignment"]["observed"]["backend_model_verified"])
        transfers = [t for t in first["transfers"] if t["kind"] == "specification"]
        self.assertEqual(len(transfers), 2)
        self.assertEqual(len({t["id"] for t in transfers}), 2)
        self.assertEqual(first, second)
        self.assertEqual(self.counts(), before)

    def test_delayed_old_handoff_does_not_replace_current_owner_or_reidentify_specification(self):
        state = self.seed_task("work")
        original = self.projection()
        specification = next(t for t in original["transfers"] if t["kind"] == "specification")
        old = copy.deepcopy(state)
        self.now += 10
        state["active"].update(execution_id="work-generation-2", generation=2, session_id="fixture-session-2")
        with self.dispatcher.db:
            self.dispatcher._save(state, "ownership_transferred")
        # A historical event arrives after the current row: it is delivery history,
        # never permission to revert the row or replay the original specification.
        with self.db:
            self.db.execute("INSERT INTO flow_events(task_id,kind,at,document) VALUES (?,?,?,?)",
                            ("work", "ownership_transferred", self.now + 1, json.dumps(old)))
        current = self.projection()
        role = next(n for n in current["nodes"] if n["id"] == self.roles[0]["id"])
        self.assertEqual(role["assignment"]["execution_id"], "work-generation-2")
        self.assertEqual(role["assignment"]["generation"], 2)
        self.assertEqual(role["assignment"]["session_id"], "fixture-session-2")
        self.assertEqual(next(t for t in current["transfers"] if t["kind"] == "specification")["id"], specification["id"])
        self.assertEqual(len({t["id"] for t in current["transfers"]}), len(current["transfers"]))

    def test_late_completed_report_remains_history_while_current_task_is_stopped(self):
        state = self.seed_task("work")
        completed = copy.deepcopy(state)
        completed.update(status="CONTRIBUTION_READY", snapshot={"head_commit": "c" * 40})
        completed["executions"] = [completed.pop("active")]
        state.update(status="STOPPED", reason="Master stopped this task", active=None)
        self.now += 10
        with self.dispatcher.db:
            self.dispatcher._save(state, "stopped")
            self.dispatcher.db.execute("INSERT INTO flow_events(task_id,kind,at,document) VALUES (?,?,?,?)",
                ("work", "graph_advanced", self.now + 1, json.dumps(completed)))
        result = self.projection()
        node = next(n for n in result["nodes"] if n["id"] == self.roles[0]["id"])
        self.assertEqual(node["status"], "STOPPED")
        report = next(t for t in result["transfers"] if t["kind"] == "result_report")
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["artifact_refs"][0]["sha"], "c" * 40)
        self.assertEqual(self.dispatcher.get("work")["status"], "STOPPED")

    def test_observed_model_requires_current_attempt_runtime_evidence(self):
        state = self.seed_task("work")
        state["active"]["job_id"] = "fixture-job"
        session_id = "12345678-1234-1234-1234-123456789abc"
        job = {"job_id": "fixture-job", "session_id": session_id, "provider": "codex",
               "status": "COMPLETED", "attempt_count": 1, "updated_at": 1005,
               "result": {"structured": {"model": "gpt-6-astra", "reasoning_effort": "ultra"}}}
        with self.dispatcher.db:
            self.dispatcher._save(state, "submitted")
            self.dispatcher.db.execute("INSERT INTO session_jobs(job_id,binding,state,document) VALUES (?,?,?,?)",
                (job["job_id"], "fixture-binding", job["status"], json.dumps(job)))

        def observation():
            return next(n for n in self.projection()["nodes"] if n["id"] == self.roles[0]["id"])["assignment"]["observed"]

        self.assertEqual(observation()["status"], "unavailable")
        job["result"]["configuration_evidence"] = {"source": "codex_rollout", "scope": "cli_turn_configuration",
            "status": "observed", "cli_version": "0.154.0", "backend_model_verified": False,
            "session_id": session_id, "contexts": [{"recorded_at": 1002, "turn_id": "fixture-turn",
                "model": "observed-model", "reasoning_effort": "low"}]}
        with self.db:
            self.db.execute("INSERT INTO session_events(job_id,occurred_at,document) VALUES (?,?,?)",
                (job["job_id"], 1001, json.dumps({"attempt_count": 1, "status": "RUNNING"})))
            self.db.execute("UPDATE session_jobs SET document=? WHERE job_id=?", (json.dumps(job), job["job_id"]))
        observed = observation()
        self.assertEqual(observed["model"], "observed-model")
        self.assertEqual(observed["reasoning_effort"], "low")
        self.assertFalse(observed["backend_model_verified"])
        job["attempt_count"] = 2
        with self.db:
            self.db.execute("UPDATE session_jobs SET document=? WHERE job_id=?", (json.dumps(job), job["job_id"]))
        self.assertEqual(observation()["status"], "unavailable")

    def test_integration_reviewer_is_separate_from_author_role(self):
        self.seed_task("author-work")
        integration = self.seed_task("integration-work")
        integration["specification"]["execution_scope"] = "integration"
        integration["stage"] = "reviewer"
        integration["snapshot"] = {"head_commit": "d" * 40}
        integration["last_completed_stage"] = "check"
        integration["verification"] = {"passed": True, "head_sha": "d" * 40,
            "checks": [{"name": "fixture-required-check", "success": True}],
            "remote": {"source": "fixture", "head_sha": "d" * 40}}
        integration["active"].update(role="reviewer", agent_id="independent-reviewer")
        integration["specification"]["agents"].append({"agent_id": "independent-reviewer", "provider": "claude",
            "model": "review-model", "reasoning_effort": "low"})
        run = {"id": "fixture-run", "mode": "fixture", "state": "integration_running", "harness_version": 1,
               "roles": {}, "integration": {"task_id": "integration-work"}}
        with self.dispatcher.db:
            self.dispatcher._save(integration, "role_assigned")
            self.dispatcher.db.execute("INSERT INTO management_runs VALUES (?,?,?)", (run["id"], self.pid, json.dumps(run)))
        overview = self.store.overview(self.pid)
        author = next(r for r in overview["roles"] if r["id"] == self.roles[0]["id"])
        nodes = {n["id"]: n for n in overview["collaboration"]["nodes"]}
        self.assertEqual(author["current_task_id"], "author-work")
        self.assertEqual(nodes[author["id"]]["assignment"]["requested"]["model"], "requested-model")
        self.assertEqual(nodes["reviewer:fixture-run"]["assignment"]["requested"]["model"], "review-model")
        self.assertEqual(nodes["reviewer:fixture-run"]["current_task_id"], "integration-work")
        self.assertEqual(nodes["check:fixture-run"]["status"], "COMPLETE")
        self.assertEqual(nodes["final:fixture-run"]["status"], "WAITING_DEPENDENCY")

    def test_translation_reads_preserve_original_authority_and_do_not_enqueue(self):
        approval = self.store.request_approval(self.pid, self.subject)
        before = self.counts()
        overview = self.store.overview(self.pid)
        document = self.approval_document(approval)
        self.assertEqual(document["source_digest"], source_digest(document))
        annotation = {**document, "original_language": "arbitrary annotation"}
        self.assertEqual(source_digest(annotation), document["source_digest"])
        for key, value in self.subject.items():
            self.assertEqual(overview["approvals"][0][key], value)
            self.assertEqual(document["fields"].get(key, document["protected"].get(key)), value)
        self.assertEqual(document["protected"]["subject_digest"], approval["subject_digest"])
        self.assertIsNone(overview["documents"][document["id"]]["translation"])
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.store.decide(self.pid, approval["id"], self.decision(approval))["status"], "approved")

    def test_wrong_source_or_other_approval_translation_rejects_without_decision(self):
        first = self.store.request_approval(self.pid, self.subject)
        other = self.store.request_approval(self.pid, {**self.subject, "artifact_sha": "b" * 40})
        other_project = self.store.create_project({"name": "Other", "goal": "Separate approval boundary"})
        cross_project = self.store.request_approval(other_project["id"], self.subject)
        doc, translation = self.translated_approval(first)
        cases = [(first, {"id": translation["id"], "source_digest": "f" * 64}),
                 (other, {"id": translation["id"], "source_digest": doc["source_digest"]}),
                 (cross_project, {"id": translation["id"], "source_digest": doc["source_digest"]})]
        for index, (approval, display) in enumerate(cases):
            with self.subTest(index=index):
                before = self.counts()
                with self.assertRaises(ManagementError) as error:
                    self.store.decide(approval["project_id"], approval["id"], self.decision(approval, f"bad-display-{index}", display))
                self.assertEqual(error.exception.code, "translation_mismatch")
                self.assertEqual(self.counts(), before)
                current = next(a for a in self.store.overview(approval["project_id"])["approvals"] if a["id"] == approval["id"])
                self.assertEqual(current["status"], "pending")

    def test_failed_translation_still_allows_review_and_approval_of_original(self):
        approval = self.store.request_approval(self.pid, self.subject)
        document = self.approval_document(approval)
        self.translations.sync(self.pid, [document], self.config)
        job = self.translations.claim("fixture-translator", adapter_ready=True)
        self.translations.finish(job["id"], job["lease_token"], {"category": "code_error", "reason": "fixture failure"})
        overview = self.store.overview(self.pid)
        self.assertEqual(overview["documents"][document["id"]]["translation"]["status"], "failed")
        self.assertEqual(overview["approvals"][0]["rollback"], self.subject["rollback"])
        display = {"id": job["id"], "source_digest": document["source_digest"]}
        with self.assertRaises(ManagementError) as error:
            self.store.decide(self.pid, approval["id"], self.decision(approval, "failed-display-1", display))
        self.assertEqual(error.exception.code, "translation_mismatch")
        self.assertEqual(self.store.decide(self.pid, approval["id"], self.decision(approval))["status"], "approved")

    def test_displayed_translation_audit_survives_cache_replacement_and_idempotent_replay(self):
        approval = self.store.request_approval(self.pid, self.subject)
        document, translation = self.translated_approval(approval)
        display = {"id": translation["id"], "source_digest": document["source_digest"]}
        request = self.decision(approval, displayed=display)
        decided = self.store.decide(self.pid, approval["id"], request)
        self.assertEqual(decided["status"], "approved")
        self.assertEqual(decided["displayed_translation"]["id"], display["id"])
        self.assertEqual(decided["displayed_translation"]["source_digest"], display["source_digest"])
        for key, value in self.subject.items():
            self.assertEqual(decided[key], value)
        self.translations.sync(self.pid, [document], {**self.config, "prompt_version": "ko-translation-v2"})
        self.assertNotEqual(self.translations.read(document)["id"], translation["id"])
        self.assertEqual(self.store.decide(self.pid, approval["id"], request), decided)
        artifact = self.translations.get_result(display["id"])
        self.assertEqual(artifact["fields"]["rollback"], "미리보기 중지")
        self.assertEqual(self.store.validate_approval(self.pid, approval["id"], self.subject)["subject_digest"], approval["subject_digest"])


if __name__ == "__main__":
    unittest.main()
