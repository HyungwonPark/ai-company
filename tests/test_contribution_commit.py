"""Runner commits preserve sandbox restrictions and bind original model output."""
import copy
from pathlib import Path
import tempfile
import unittest

from ai_company.adapters.session_cli import SessionOutcome
from ai_company.contribution_commit import commit_contribution, validate_contribution_receipt
from ai_company.contracts import digest
from ai_company.flow_contracts import ContributionStageReport
from ai_company.runtime import ExecutionBlocked
from test_dispatcher import FlowFixture


class ContributionCommitTests(unittest.TestCase):
    git = FlowFixture.git
    commit = FlowFixture.commit
    open = FlowFixture.open
    execute = FlowFixture.execute
    state = FlowFixture.state
    submit = FlowFixture.submit

    def setUp(self):
        FlowFixture.setUp(self)
        self.spec = self.spec.model_copy(update={"execution_scope": "contribution"})
        self.submit()
        state = self.state()
        self.dispatcher._assign(state, self.spec, self.spec.agents[1])
        self.state_record = self.state()
        active = self.state_record["active"]
        self.report = dict(execution_id=active["execution_id"], generation=active["generation"], role="developer",
            task_digest=digest(self.spec.task), policy_digest=digest(self.spec.policy), candidate_sha=self.task.base_sha,
            verification_digest=None, verdict="DONE", findings=[], resolved_findings=[], summary="Ready for runner commit",
            commit_requested=True)
        self.outcome = SessionOutcome("success", session_id="fixture-developer", result={"structured_output": self.report})
        self.output = self.root / "output"; self.output.mkdir()

    def commit_output(self):
        return commit_contribution(self.spec, self.state_record, self.outcome, self.repo, output_dir=self.output)

    def test_receipt_binds_unmodified_report_parent_tree_and_allowed_paths(self):
        original = copy.deepcopy(self.report)
        (self.repo / "src/code.txt").write_text("candidate\n")
        outcome = self.commit_output()
        head = self.git("rev-parse", "HEAD")
        self.assertNotEqual(head, self.task.base_sha)
        self.assertEqual(self.report, original)
        self.assertEqual(self.git("status", "--porcelain"), "")
        job = {"head_commit": head, "result": outcome.result}
        validate_contribution_receipt(self.spec, self.state_record, job, ContributionStageReport.model_validate(self.report))
        self.assertEqual(self.commit_output().result["runner_commit"], outcome.result["runner_commit"])
        altered = copy.deepcopy(job); altered["result"]["runner_commit"]["report_digest"] = "0" * 64
        with self.assertRaises(ExecutionBlocked):
            validate_contribution_receipt(self.spec, self.state_record, altered, ContributionStageReport.model_validate(self.report))

    def test_outside_path_and_symlink_do_not_commit(self):
        (self.repo / "outside.txt").write_text("not allowed")
        with self.assertRaises(ExecutionBlocked):
            self.commit_output()
        self.assertEqual(self.git("rev-parse", "HEAD"), self.task.base_sha)
        (self.repo / "outside.txt").unlink()
        (self.repo / "src/code.txt").unlink()
        (self.repo / "src/code.txt").symlink_to(self.root / "outside")
        with self.assertRaises(ExecutionBlocked):
            self.commit_output()

    def test_refuses_noop_block_and_unstopped_live_process(self):
        with self.assertRaises(ExecutionBlocked):
            self.commit_output()
        self.report["verdict"] = "BLOCK"
        self.assertIs(self.commit_output(), self.outcome)
        self.report["verdict"] = "DONE"
        self.spec = self.spec.model_copy(update={"mode": "live"})
        (self.repo / "src/code.txt").write_text("changed")
        with self.assertRaisesRegex(ExecutionBlocked, "stopped"):
            self.commit_output()

    def test_candidate_movement_or_report_swap_fails_closed(self):
        (self.repo / "src/code.txt").write_text("changed")
        self.commit_output()
        self.report["summary"] = "another report"
        with self.assertRaises(ExecutionBlocked):
            self.commit_output()
        self.assertNotEqual(self.git("rev-parse", "HEAD"), self.task.base_sha)


if __name__ == "__main__":
    unittest.main()
