"""Synthetic E1 dossier exercises the real diagnosis, shared ledger and canary.

Only the external Codex rollout observation is simulated; no model is called.
All state and source files live in a temporary directory.
"""

from hashlib import sha256
from contextlib import closing
from pathlib import Path
import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from ai_company.adapters.session_cli import codex_output_schema, codex_output_schema_digest
from ai_company.automation import Automation
from ai_company.automation_contracts import AutomationConfig
from ai_company.contracts import digest
from ai_company.dispatcher import Dispatcher
from ai_company.flow_contracts import FlowSpec, PMPlanStageReport
from ai_company.management import ManagementStore
from ai_company.pm_evidence_recovery import (
    RecoveryError, apply_to_isolated_state, apply_to_operating_state, diagnose,
    verify_saved_recovery,
)
from ai_company.sessions import repository_snapshot
from ai_company.shared_calls import SharedCallLedger
from tests import test_automation as automation_fixture


SPEC = importlib.util.spec_from_file_location(
    "evaluate_pm_behavior", Path(__file__).resolve().parents[1] / "scripts/evaluate_pm_behavior.py")
EVALUATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EVALUATION)


def _save(db, table, key, document):
    db.execute(f"INSERT INTO {table} VALUES (?,?)", (key, json.dumps(document, ensure_ascii=False)))


class RecoveryRestartIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        harness = automation_fixture.CoordinatorTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        self.repo = harness.repo
        config_data = harness.config.model_dump(mode="json")
        config_data["mode"] = "live"
        config_data["max_parallel"] = 1
        config_data["policy"]["configuration_evidence"] = "cli_configuration_v2"
        config_data["policy"].update(max_executions=18, max_runtime_seconds=1200,
                                     max_repairs=2)
        self.config = AutomationConfig.model_validate(config_data)
        self.pm_agent = next(agent for agent in self.config.agents
                             if agent.agent_id == self.config.policy.candidates["pm"][0])
        self.review_agent = next(agent for agent in self.config.agents
                                 if agent.provider == "codex" and "reviewer" in agent.roles)
        self.account = (self.pm_agent.provider, self.pm_agent.credential_ref, self.pm_agent.quota_group)
        self.root = self.base / "trial"
        self.origin = self.root / "E1"
        self.worker = Automation(self.origin, self.config)
        self.addCleanup(self.worker.close)
        self.project = self.worker.store.create_project({"name": "Synthetic E1", "goal": "Create two outputs"})
        message = self.worker.store.post_message(self.project["id"], {"content": "Plan the two outputs"})
        self.request = self.worker.store.get_pm_request(message["id"])
        self.config_path = self.base / "config.json"
        self.config_path.write_text(json.dumps(self.config.model_dump(mode="json"), ensure_ascii=False))
        self.codex_home = self.base / "codex-home"
        self.session_id = "synthetic-e1-session"
        rollout = self.codex_home / "sessions" / "2026" / "09" / "27" / f"rollout-fixture-{self.session_id}.jsonl"
        rollout.parent.mkdir(parents=True)
        rollout.write_text('{"type":"thread.started"}\n')
        self.snapshot = repository_snapshot(self.repo)
        self.task_id = "pm-" + self.request["request_id"]
        task_contract = self.worker._task(self.task_id, "Plan", ["Plan is ready"],
                                          self.config.base_sha, self.config.allowed_paths)
        self.spec = self.worker._spec(task_contract, self.repo, "planning", {
            "request_revision": self.request["request_revision"],
            "goal_digest": self.request["goal_digest"],
            "project_id": self.project["id"]})
        self.job_id = "synthetic-e1-job"
        self.active = {"job_id": self.job_id, "generation": 1, "execution_id": digest("e1-execution"),
                       "role": "pm", "agent_id": self.pm_agent.agent_id,
                       "task_digest": digest(self.spec.task), "policy_digest": digest(self.spec.policy),
                       "input_snapshot": self.snapshot}
        plan = dict(harness.plan)
        plan["requirements_review"] = {
            "version": 2, "revision": self.request["request_revision"],
            "goal_digest": self.request["goal_digest"], "problem": "Create two outputs",
            "users_and_flow": "Owner requests and checks two outputs",
            "scope": ["Create two outputs"], "exclusions": [], "assumptions": [], "questions": [],
            "findings": [], "requirements": [{"id": "R-" + role["key"], "source": "master goal",
                "acceptance": role["acceptance"][0], "verification": "Inspect output and run unit checks",
                "role_keys": [role["key"]]} for role in plan["roles"]]}
        report = {"execution_id": self.active["execution_id"], "generation": 1, "role": "pm",
                  "task_digest": digest(self.spec.task), "policy_digest": digest(self.spec.policy),
                  "candidate_sha": self.config.base_sha, "verification_digest": None,
                  "verdict": "PASS", "findings": [], "resolved_findings": [],
                  "summary": "Synthetic plan", "plan": plan}
        logs = self.origin / "sessions" / "session-logs" / "original"
        logs.mkdir(parents=True)
        stdout = logs / "stdout.jsonl"
        stdout.write_text("\n".join(json.dumps(item) for item in (
            {"type": "thread.started", "thread_id": self.session_id},
            {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(report)}},
            {"type": "turn.completed"})) + "\n")
        stderr = logs / "stderr.log"
        stderr.write_text("")
        schema = logs / "schema-1.json"
        strict = codex_output_schema(PMPlanStageReport.model_json_schema())
        schema.write_text(json.dumps(strict, sort_keys=True, separators=(",", ":")))
        schema_sha = codex_output_schema_digest(PMPlanStageReport.model_json_schema())
        result = {"structured_output": report, "stdout_path": str(stdout), "stderr_path": str(stderr),
                  "output_schema_sha256": schema_sha, "cgroup_stopped": True,
                  "exit_code": 0, "duration_seconds": 1, "total_cost_usd": None}
        task = {"task_id": self.task_id, "specification": self.spec.model_dump(mode="json"),
                "spec_digest": digest(self.spec), "status": "BLOCKED", "generation": 1,
                "active": self.active, "executions": [], "snapshot": self.snapshot,
                "stage": "pm", "authors": [], "pm_sessions": []}
        job = {"job_id": self.job_id, "task_id": self.task_id, "status": "SESSION_COMPLETED",
               "last_category": "success", "attempt_count": 1, "session_id": self.session_id,
               "provider": self.pm_agent.provider, "agent_id": self.pm_agent.agent_id,
               "head_commit": self.config.base_sha,
               "repository_snapshot": self.snapshot, "process": None,
               "updated_at": 1001.0, "result": result}
        self.worker.store.save_pm_request({**self.request, "state": "blocked",
            "reason": "assigned Codex CLI turn configuration is missing or unbound",
            "configuration_digest": digest(self.config), "mode": "live"}, expected_state="pending")
        with self.worker.store.db:
            _save(self.worker.store.db, "flow_tasks", self.task_id, task)
            self.worker.store.db.execute("INSERT INTO session_jobs VALUES (?,?,?,?,?,?,?)",
                (self.job_id, "synthetic", "SESSION_COMPLETED", None, None, None,
                 json.dumps(job, ensure_ascii=False)))
            self.worker.store.db.execute("INSERT INTO session_events(job_id,occurred_at,document) VALUES (?,?,?)",
                (self.job_id, 1000.0, json.dumps({"status": "RUNNING", "attempt_count": 1})))
        self.prior = self.base / "prior"
        self.prior.mkdir()
        prior_manifest = self.prior / "evaluation-bindings.json"
        prior_manifest.write_text("{}")
        self.shared = self.base / "shared.sqlite"
        ledger = SharedCallLedger.initialize(self.shared, [
            (*self.account, "AVAILABLE", None, None, 0, 0, 0, 0)])
        try:
            for index in range(6):
                owner = str(self.prior / f"E{index + 1}")
                rid = digest([owner, "old-job", 1])
                ledger.reserve(rid, owner, *self.account)
                ledger.started(rid, owner, {"kind": "synthetic", "pid": index + 1})
                ledger.settle(rid, owner, digest([rid, index]),
                              {"category": "success", "duration_seconds": 1,
                               "total_cost_usd": None}, terminated=True)
            self.original_reservation = digest([str(self.origin), self.job_id, 1])
            ledger.reserve(self.original_reservation, str(self.origin), *self.account)
            ledger.started(self.original_reservation, str(self.origin), {"kind": "synthetic", "pid": 7})
            ledger.settle(self.original_reservation, str(self.origin),
                          digest([self.original_reservation, 1, "success", result]),
                          {"category": "success", "duration_seconds": 1,
                           "total_cost_usd": None, "output_schema_sha256": schema_sha}, terminated=True)
        finally:
            ledger.close()
        (self.root / "cases.json").write_text("{}")
        bindings = self.root / "evaluation-bindings.json"
        bindings.write_text(json.dumps({"case_bindings": {"E1": {
            "message_sha256": sha256(self.request["content"].encode()).hexdigest()}},
            "product_commit": self.config.base_sha,
            "input_sha256": {str(self.config_path): sha256(self.config_path.read_bytes()).hexdigest()}}))
        lineage = self.root / "evaluation-lineage.json"
        lineage.write_text(json.dumps({"schema_version": 1, "prior_trial_root": str(self.prior),
            "shared_call_ledger": str(self.shared),
            "prior_manifest_sha256": sha256(prior_manifest.read_bytes()).hexdigest()}))
        budget = self.origin / "case-budget.json"
        effective = self.config.model_dump(mode="json")
        budget.write_text(json.dumps({"caps": {key: effective["policy"][key] for key in
            ("max_executions", "max_runtime_seconds", "max_repairs")},
            "effective_configuration_sha256": sha256(json.dumps(effective,
                ensure_ascii=False, sort_keys=True).encode()).hexdigest()}))
        evidence_dir = self.base / "frozen"
        evidence_dir.mkdir()
        self.evidence_manifest = evidence_dir / "manifest.json"
        original_db = self.origin / "sessions" / "sessions.sqlite"
        with sqlite3.connect(evidence_dir / "trial-state.sqlite") as frozen:
            self.worker.store.db.backup(frozen)
        with sqlite3.connect(f"file:{self.shared}?mode=ro", uri=True) as source, \
                sqlite3.connect(evidence_dir / "shared-ledger.sqlite") as frozen:
            source.backup(frozen)
        files = {"cli-stdout.jsonl": stdout, "cli-stderr.log": stderr,
                 "output-schema.json": schema, "rollout.jsonl": rollout,
                 "case-budget.json": budget, "cases.json": self.root / "cases.json",
                 "evaluation-bindings.json": bindings, "evaluation-lineage.json": lineage}
        self.evidence_manifest.write_text(json.dumps({"files": {name: {
            "source": str(path), "sha256": sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size} for name, path in files.items()},
            "databases": {"shared-ledger.sqlite": {"source": str(self.shared)},
                          "trial-state.sqlite": {"source": str(original_db)}}}))
        self.validator_commit = "a" * 40
        observation = patch("ai_company.pm_evidence_recovery.codex_turn_configuration",
                            return_value={"status": "observed", "profile": "synthetic-cli-profile"})
        cli_validation = patch.object(Dispatcher, "_validate_cli_configuration")
        observation.start()
        cli_validation.start()
        self.addCleanup(observation.stop)
        self.addCleanup(cli_validation.stop)

    def diagnosis(self):
        return diagnose(self.origin, self.origin, self.shared, self.config_path,
                        self.codex_home, validator_commit=self.validator_commit,
                        evidence_manifest=self.evidence_manifest)

    def add_review(self, category="success", *, plan_id=None, job_id="synthetic-review-job"):
        diagnosis = self.diagnosis()
        with sqlite3.connect(self.origin / "sessions" / "sessions.sqlite") as db:
            plan_row = (db.execute("SELECT document FROM management_plans WHERE id=?", (plan_id,)).fetchone()
                        if plan_id else db.execute("SELECT document FROM management_plans").fetchone())
            plan = json.loads(plan_row[0])
            request = json.loads(db.execute("SELECT document FROM management_pm_requests").fetchone()[0])
            task_id = "plan-review-" + plan["digest"][:48]
            spec_data = self.spec.model_dump(mode="json")
            spec_data["task"]["task_id"] = task_id
            spec_data["execution_scope"] = "plan_review"
            spec_data["plan"] = {"project_id": plan["project_id"], "plan_digest": plan["digest"],
                                 "requirements_revision": request["request_revision"]}
            spec_data["inherited_pm_sessions"] = [{"provider": plan["evidence"]["provider"],
                                                    "session_id": plan["evidence"]["session_id"]}]
            spec = FlowSpec.model_validate(spec_data)
            active = {"job_id": job_id, "generation": 1,
                      "execution_id": digest("synthetic-review-execution"), "role": "reviewer",
                      "agent_id": self.review_agent.agent_id,
                      "provider": self.review_agent.provider,
                      "task_digest": digest(spec.task), "policy_digest": digest(spec.policy)}
            waiting = category in ("quota", "reserved", "unknown", "claimed_reserved")
            task = {"task_id": task_id, "specification": spec.model_dump(mode="json"),
                    "spec_digest": digest(spec), "generation": 1, "active": active,
                    "executions": [], "status": "READY" if category in ("reserved", "unknown") else
                    "NEEDS_RECONCILIATION" if category == "claimed_reserved" else
                    "WAITING_QUOTA" if category == "quota" else "PLAN_REVIEWED"}
            reset = 2_000_000_000.0 if category == "quota" else None
            result = {"duration_seconds": 1, "total_cost_usd": None}
            job = {"job_id": job_id, "task_id": task_id,
                   "attempt_count": 0 if category in ("reserved", "unknown") else 1,
                   "managed_by": task_id, "agent_id": self.review_agent.agent_id,
                   "provider": self.review_agent.provider,
                   "specification": {"task": spec.task.model_dump(mode="json"),
                       "agent_id": self.review_agent.agent_id,
                       "provider": self.review_agent.provider, "worktree": spec.worktree},
                   "status": "READY" if category in ("reserved", "unknown") else
                   "NEEDS_RECONCILIATION" if category == "claimed_reserved" else
                   "WAITING_QUOTA" if category == "quota" else "SESSION_COMPLETED",
                   "last_category": None if waiting and category != "quota" else category,
                   "result": None if category in ("reserved", "unknown", "claimed_reserved") else result,
                   "resume_at": reset,
                   "session_id": "synthetic-review-session", "process": None,
                   "checkpoint": {"expected_report": active}}
            _save(db, "flow_tasks", task_id, task)
            db.execute("INSERT INTO session_jobs VALUES (?,?,?,?,?,?,?)",
                       (job_id, "synthetic", job["status"], reset, None, None, json.dumps(job)))
            if category == "claimed_reserved":
                db.execute("INSERT INTO session_events(job_id,occurred_at,document) VALUES (?,?,?)",
                    (job_id, 2000.0, json.dumps({"attempt_count": 1, "status": "RUNNING"})))
        ledger = SharedCallLedger(self.shared)
        try:
            rid = digest([str(self.origin), job_id, 1])
            ledger.reserve(rid, str(self.origin), *self.account)
            if category == "unknown":
                ledger.uncertain(rid, str(self.origin), "synthetic unresolved guard")
            elif category not in ("reserved", "claimed_reserved"):
                ledger.started(rid, str(self.origin), {"kind": "synthetic", "pid": 8})
                fact = {"category": category, "duration_seconds": 1, "total_cost_usd": None}
                if reset is not None:
                    fact["reset_at"] = reset
                ledger.settle(rid, str(self.origin), digest([rid, 1, category, result]), fact, terminated=True)
        finally:
            ledger.close()
        return rid

    def test_review_settlement_survives_restart_and_tampering_is_rejected(self):
        first = self.diagnosis()
        self.assertEqual(apply_to_operating_state(first)["state"], "saved")
        marker = EVALUATION.recovered_canary(self.root, self.shared, self.config_path,
            self.codex_home, self.evidence_manifest, self.validator_commit)
        self.worker.close()  # An entirely new connection must prove the saved state.
        self.assertTrue(EVALUATION.canary_verified(self.root, self.shared, marker))
        review_id = self.add_review(category="quota")
        after = self.diagnosis()
        self.assertEqual(verify_saved_recovery(after, allow_review_progress=True)["state"], "verified")
        self.assertTrue(EVALUATION.canary_verified(self.root, self.shared, marker))
        self.assertEqual(EVALUATION.recovered_canary(self.root, self.shared, self.config_path,
            self.codex_home, self.evidence_manifest, self.validator_commit,
            allow_progress=True)["plan_id"], marker["plan_id"])
        with sqlite3.connect(self.shared) as ledger:
            ledger.execute("UPDATE reservations SET event_id=? WHERE reservation_id=?",
                           ("tampered-review-event", review_id))
        with self.assertRaises(RecoveryError):
            self.diagnosis()

    def test_review_success_and_same_job_quota_resume_keep_original_call_once(self):
        first = self.diagnosis()
        apply_to_operating_state(first)
        marker = EVALUATION.recovered_canary(self.root, self.shared, self.config_path,
            self.codex_home, self.evidence_manifest, self.validator_commit)
        self.worker.close()
        review_id = self.add_review(category="quota")
        self.assertTrue(EVALUATION.canary_verified(self.root, self.shared, marker))
        with sqlite3.connect(self.origin / "sessions" / "sessions.sqlite") as db:
            job = json.loads(db.execute("SELECT document FROM session_jobs WHERE job_id='synthetic-review-job'").fetchone()[0])
            task_id = job["task_id"]
            task = json.loads(db.execute("SELECT document FROM flow_tasks WHERE task_id=?", (task_id,)).fetchone()[0])
            session_id = job["session_id"]
            db.execute("INSERT INTO session_events(job_id,occurred_at,document) VALUES (?,?,?)",
                       (job["job_id"], 2000.0, json.dumps(job)))
            job.update(attempt_count=2, status="SESSION_COMPLETED", last_category="success",
                       resume_at=None, result={"duration_seconds": 1, "total_cost_usd": None})
            task.update(status="PLAN_REVIEWED", executions=[task["active"]], active=None)
            db.execute("UPDATE session_jobs SET state=?,document=? WHERE job_id=?",
                       (job["status"], json.dumps(job), job["job_id"]))
            db.execute("UPDATE flow_tasks SET document=? WHERE task_id=?", (json.dumps(task), task_id))
            plan = json.loads(db.execute("SELECT document FROM management_plans").fetchone()[0])
            plan["status"] = "proposed"
            db.execute("UPDATE management_plans SET document=? WHERE id=?",
                       (json.dumps(plan), plan["id"]))
        ledger = SharedCallLedger(self.shared, clock=lambda: 2_000_000_001.0)
        try:
            retry_id = digest([str(self.origin), job["job_id"], 2])
            ledger.reserve(retry_id, str(self.origin), *self.account)
            ledger.started(retry_id, str(self.origin), {"kind": "synthetic", "pid": 9})
            ledger.settle(retry_id, str(self.origin), digest([retry_id, 2, "success", job["result"]]),
                          {"category": "success", "duration_seconds": 1,
                           "total_cost_usd": None}, terminated=True)
        finally:
            ledger.close()
        resumed = self.diagnosis()
        self.assertEqual(verify_saved_recovery(resumed, allow_review_progress=True)["plan_id"], marker["plan_id"])
        self.assertTrue(EVALUATION.canary_verified(self.root, self.shared, marker))
        with sqlite3.connect(self.shared) as ledger:
            self.assertEqual(ledger.execute("SELECT count(*) FROM reservations WHERE owner=? AND state='SETTLED'",
                (str(self.origin),)).fetchone()[0], 3)
            self.assertEqual(ledger.execute("SELECT count(*) FROM reservations WHERE reservation_id=?",
                (self.original_reservation,)).fetchone()[0], 1)
            self.assertEqual(ledger.execute("SELECT count(*) FROM reservations WHERE reservation_id=?",
                (review_id,)).fetchone()[0], 1)
        with sqlite3.connect(self.origin / "sessions" / "sessions.sqlite") as db:
            saved = json.loads(db.execute("SELECT document FROM session_jobs WHERE job_id=?", (job["job_id"],)).fetchone()[0])
            self.assertEqual((saved["job_id"], saved["session_id"]), (job["job_id"], session_id))
            self.assertEqual(db.execute("SELECT count(*) FROM management_plans").fetchone()[0], 1)

    def test_original_seven_and_stale_followup_generation_fail_closed(self):
        first = self.diagnosis()
        apply_to_operating_state(first)
        self.worker.close()
        self.add_review(category="success")
        self.diagnosis()
        with sqlite3.connect(self.origin / "sessions" / "sessions.sqlite") as db:
            row = db.execute("SELECT task_id,document FROM flow_tasks WHERE task_id LIKE 'plan-review-%'").fetchone()
            task = json.loads(row[1])
            task["active"]["generation"] = 2
            db.execute("UPDATE flow_tasks SET document=? WHERE task_id=?", (json.dumps(task), row[0]))
        with self.assertRaisesRegex(RecoveryError, "task, generation or plan binding"):
            self.diagnosis()
        with sqlite3.connect(self.origin / "sessions" / "sessions.sqlite") as db:
            task["active"]["generation"] = 1
            db.execute("UPDATE flow_tasks SET document=? WHERE task_id=?", (json.dumps(task), row[0]))
        self.diagnosis()
        with sqlite3.connect(self.shared) as ledger:
            ledger.execute("UPDATE reservations SET result=? WHERE reservation_id=?",
                           ('{"category":"changed"}', self.original_reservation))
        with self.assertRaisesRegex(RecoveryError, "settlement differs"):
            self.diagnosis()

    def test_unrelated_extra_reservation_is_not_treated_as_review(self):
        first = self.diagnosis()
        apply_to_operating_state(first)
        self.worker.close()
        self.add_review(category="success")
        self.diagnosis()
        with sqlite3.connect(self.shared) as ledger:
            ledger.execute("INSERT INTO reservations(reservation_id,owner,group_id,state,created_at) "
                           "VALUES (?,?,?,?,?)", ("unrelated-extra", str(self.origin),
                           self.account[2], "RESERVED", 3000.0))
        with self.assertRaisesRegex(RecoveryError, "unique job attempt"):
            self.diagnosis()
        with sqlite3.connect(self.shared) as ledger:
            ledger.execute("DELETE FROM reservations WHERE reservation_id='unrelated-extra'")
        self.diagnosis()

    def test_reserved_then_unknown_review_is_safe_wait_without_another_pm_call(self):
        first = self.diagnosis()
        apply_to_operating_state(first)
        marker = EVALUATION.recovered_canary(self.root, self.shared, self.config_path,
            self.codex_home, self.evidence_manifest, self.validator_commit)
        self.worker.close()
        review_id = self.add_review(category="reserved")
        self.assertTrue(EVALUATION.canary_verified(self.root, self.shared, marker))
        self.assertEqual(EVALUATION.execution_usage(self.root, self.shared,
            self.config.policy.retry.execution_timeout_seconds)[1], 1)
        ledger = SharedCallLedger(self.shared)
        try:
            ledger.uncertain(review_id, str(self.origin), "synthetic unresolved guard")
        finally:
            ledger.close()
        self.assertTrue(EVALUATION.canary_verified(self.root, self.shared, marker))
        self.assertEqual(EVALUATION.execution_usage(self.root, self.shared,
            self.config.policy.retry.execution_timeout_seconds)[1], 1)
        with sqlite3.connect(self.shared) as ledger:
            self.assertEqual(ledger.execute("SELECT count(*) FROM reservations WHERE owner=?",
                (str(self.origin),)).fetchone()[0], 2)

    def test_claimed_but_unstarted_review_is_safe_wait(self):
        first = self.diagnosis()
        apply_to_operating_state(first)
        marker = EVALUATION.recovered_canary(self.root, self.shared, self.config_path,
            self.codex_home, self.evidence_manifest, self.validator_commit)
        self.worker.close()
        self.add_review(category="claimed_reserved")
        self.assertTrue(EVALUATION.canary_verified(self.root, self.shared, marker))
        self.assertEqual(EVALUATION.execution_usage(self.root, self.shared,
            self.config.policy.retry.execution_timeout_seconds)[1], 1)

    def test_revise_repair_and_second_review_remain_on_one_request(self):
        first = self.diagnosis()
        apply_to_operating_state(first)
        marker = EVALUATION.recovered_canary(self.root, self.shared, self.config_path,
            self.codex_home, self.evidence_manifest, self.validator_commit)
        self.worker.close()
        self.add_review(category="success")
        repair_id = "synthetic-repair-job"
        with sqlite3.connect(self.origin / "sessions" / "sessions.sqlite") as db:
            parent = json.loads(db.execute("SELECT document FROM management_plans").fetchone()[0])
            parent.update(status="superseded", revision_action="automatic",
                          revision_result_id="synthetic-revision-plan")
            revised = json.loads(json.dumps(parent))
            revised.update(id="synthetic-revision-plan", status="reviewing",
                           revision_of=parent["id"], auto_revision_attempt=1,
                           evidence={"source": "synthetic_repair", "provider": self.pm_agent.provider,
                                     "session_id": "synthetic-repair-session"})
            revised.pop("revision_result_id", None)
            revised["digest"] = digest(ManagementStore._plan_binding(revised))
            db.execute("UPDATE management_plans SET document=? WHERE id=?", (json.dumps(parent), parent["id"]))
            db.execute("INSERT INTO management_plans VALUES (?,?,?)",
                       (revised["id"], revised["project_id"], json.dumps(revised)))
            task_id = "pm-revise-" + digest([parent["id"], 1])[:48]
            spec_data = self.spec.model_dump(mode="json")
            spec_data["task"]["task_id"] = task_id
            spec_data["plan"] = {"source_plan_id": parent["id"], "auto_revision_attempt": 1,
                                 "project_id": parent["project_id"],
                                 "request_revision": parent["request_revision"],
                                 "goal_digest": parent["goal_digest"]}
            spec = FlowSpec.model_validate(spec_data)
            active = {"job_id": repair_id, "generation": 1,
                      "execution_id": digest("synthetic-repair-execution"), "role": "pm",
                      "agent_id": self.pm_agent.agent_id, "provider": self.pm_agent.provider,
                      "task_digest": digest(spec.task), "policy_digest": digest(spec.policy)}
            task = {"task_id": task_id, "specification": spec.model_dump(mode="json"),
                    "spec_digest": digest(spec), "generation": 1, "active": None,
                    "executions": [active], "status": "PLAN_READY",
                    "usage": {"executions": 1, "repairs": 0}}
            result = {"duration_seconds": 1, "total_cost_usd": None}
            job = {"job_id": repair_id, "task_id": task_id, "managed_by": task_id,
                   "agent_id": self.pm_agent.agent_id, "provider": self.pm_agent.provider,
                   "specification": {"task": spec.task.model_dump(mode="json"),
                       "agent_id": self.pm_agent.agent_id, "provider": self.pm_agent.provider,
                       "worktree": spec.worktree},
                   "attempt_count": 1, "status": "SESSION_COMPLETED",
                   "last_category": "success", "result": result,
                   "session_id": revised["evidence"]["session_id"],
                   "checkpoint": {"expected_report": active}}
            _save(db, "flow_tasks", task_id, task)
            db.execute("INSERT INTO session_jobs VALUES (?,?,?,?,?,?,?)",
                       (repair_id, "synthetic", "SESSION_COMPLETED", None, None, None,
                        json.dumps(job)))
        ledger = SharedCallLedger(self.shared)
        try:
            rid = digest([str(self.origin), repair_id, 1])
            ledger.reserve(rid, str(self.origin), *self.account)
            ledger.started(rid, str(self.origin), {"kind": "synthetic", "pid": 10})
            ledger.settle(rid, str(self.origin), digest([rid, 1, "success", result]),
                {"category": "success", "duration_seconds": 1,
                 "total_cost_usd": None}, terminated=True)
        finally:
            ledger.close()
        self.assertEqual(EVALUATION.global_usage(self.root, self.shared)[1], 1)
        self.assertTrue(EVALUATION.canary_verified(self.root, self.shared, marker))
        self.add_review(category="success", plan_id=revised["id"],
                        job_id="synthetic-second-review-job")
        self.assertTrue(EVALUATION.canary_verified(self.root, self.shared, marker))
        with sqlite3.connect(self.origin / "sessions" / "sessions.sqlite") as db:
            self.assertEqual(db.execute("SELECT count(*) FROM management_pm_requests").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM management_plans").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT count(*) FROM pm_recovery_revisions").fetchone()[0], 1)

    def test_followup_calls_at_cap_pass_and_next_call_is_rejected(self):
        first = self.diagnosis()
        apply_to_operating_state(first)
        self.worker.close()
        self.add_review(category="success")
        path = self.origin / "sessions" / "sessions.sqlite"
        for attempt in range(2, 19):
            with sqlite3.connect(path) as db:
                job = json.loads(db.execute("SELECT document FROM session_jobs WHERE job_id='synthetic-review-job'").fetchone()[0])
                db.execute("INSERT INTO session_events(job_id,occurred_at,document) VALUES (?,?,?)",
                           (job["job_id"], 3000.0 + attempt, json.dumps(job)))
                job["attempt_count"] = attempt
                db.execute("UPDATE session_jobs SET document=? WHERE job_id=?",
                           (json.dumps(job), job["job_id"]))
            ledger = SharedCallLedger(self.shared)
            try:
                rid = digest([str(self.origin), job["job_id"], attempt])
                ledger.reserve(rid, str(self.origin), *self.account)
                ledger.started(rid, str(self.origin), {"kind": "synthetic", "pid": 100 + attempt})
                ledger.settle(rid, str(self.origin),
                    digest([rid, attempt, "success", job["result"]]),
                    {"category": "success", "duration_seconds": 1,
                     "total_cost_usd": None}, terminated=True)
            finally:
                ledger.close()
            if attempt == 17:
                self.diagnosis()  # Exactly 24 global calls and 18 E1 calls.
        with self.assertRaisesRegex(RecoveryError, "evaluation call or repair cap"):
            self.diagnosis()

    def test_real_diagnosis_and_isolated_copy_preserve_original(self):
        self.worker.close()
        copy = self.base / "isolated" / "E1"
        (copy / "sessions").mkdir(parents=True, mode=0o700)
        copy.chmod(0o700)
        source = self.origin / "sessions" / "sessions.sqlite"
        target = copy / "sessions" / "sessions.sqlite"
        with closing(sqlite3.connect(source)) as original, \
                closing(sqlite3.connect(target)) as copied:
            original.backup(copied)
        isolated = diagnose(self.origin, copy, self.shared, self.config_path,
            self.codex_home, validator_commit=self.validator_commit,
            evidence_manifest=self.evidence_manifest)
        self.assertEqual(apply_to_isolated_state(isolated)["state"], "saved")
        again = diagnose(self.origin, copy, self.shared, self.config_path,
            self.codex_home, validator_commit=self.validator_commit,
            evidence_manifest=self.evidence_manifest)
        self.assertEqual(verify_saved_recovery(again)["state"], "verified")
        with closing(sqlite3.connect(source)) as original:
            self.assertEqual(original.execute("SELECT count(*) FROM management_plans").fetchone()[0], 0)
        with closing(sqlite3.connect(target)) as copied:
            self.assertEqual(copied.execute("SELECT count(*) FROM management_plans").fetchone()[0], 1)

    def test_real_diagnosis_then_isolated_alias_is_rejected_before_write(self):
        first = self.diagnosis()
        apply_to_operating_state(first)
        self.worker.close()
        copy = self.base / "isolated" / "E1"
        copy.mkdir(parents=True, mode=0o700)
        copy.chmod(0o700)
        (copy / "sessions").symlink_to(self.origin / "sessions", target_is_directory=True)
        aliased = diagnose(self.origin, copy, self.shared, self.config_path,
            self.codex_home, validator_commit=self.validator_commit,
            evidence_manifest=self.evidence_manifest)
        with self.assertRaisesRegex(RecoveryError, "symbolic link"):
            from ai_company.pm_evidence_recovery import apply_to_isolated_state
            apply_to_isolated_state(aliased)
        (copy / "sessions").unlink()
        (copy / "sessions").mkdir(mode=0o700)
        source = self.origin / "sessions" / "sessions.sqlite"
        with sqlite3.connect(source) as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        os.link(source, copy / "sessions" / "sessions.sqlite")
        aliased = diagnose(self.origin, copy, self.shared, self.config_path,
            self.codex_home, validator_commit=self.validator_commit,
            evidence_manifest=self.evidence_manifest)
        with self.assertRaisesRegex(RecoveryError, "aliases"):
            apply_to_isolated_state(aliased)
        with sqlite3.connect(self.origin / "sessions" / "sessions.sqlite") as db:
            self.assertEqual(db.execute("SELECT count(*) FROM pm_recovery_revisions").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
