"""Generated subprocess fixtures only: no installed provider CLI or model calls."""

import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest

from ai_company.adapters.session_cli import _proc_info, run_session


class SessionCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.worktree = self.root / "worktree"
        self.worktree.mkdir()
        self.output = self.root / "output"
        self.cli = self.root / "fixture-cli"

    def fixture(self, events, *, exit_code=0, extra=""):
        self.cli.write_text(
            f"#!{sys.executable}\n"
            "import json, os, pathlib, signal, subprocess, sys, time\n"
            "pathlib.Path('invocation.json').write_text(json.dumps({'argv': sys.argv[1:], 'stdin': sys.stdin.read(), 'cwd': os.getcwd()}))\n"
            f"events = {events!r}\n"
            "for event in events: print(json.dumps(event), flush=True)\n"
            f"{extra}\n"
            f"sys.exit({exit_code})\n"
        )
        self.cli.chmod(0o700)

    def run_cli(self, provider="codex", session_id="session-1", **kwargs):
        return run_session(provider, self.worktree, "resume checkpoint\n", session_id,
                           output_dir=self.output, executable=str(self.cli), **kwargs)

    def test_codex_explicit_resume_uses_stdin_and_restricted_logs(self):
        self.fixture([{"type": "thread.started", "thread_id": "session-1"},
                      {"type": "turn.completed", "usage": {"input_tokens": 10}}])
        spawned = []
        outcome = self.run_cli(on_spawn=spawned.append)
        self.assertEqual(outcome.category, "success")
        invocation = json.loads((self.worktree / "invocation.json").read_text())
        self.assertEqual(invocation["argv"], ["exec", "resume", "session-1", "--json", "-"])
        self.assertEqual(invocation["stdin"], "resume checkpoint\n")
        self.assertEqual(invocation["cwd"], str(self.worktree))
        self.assertEqual(set(spawned[0]), {"pid", "pgid", "boot_id", "proc_start_ticks"})
        self.assertEqual(spawned[0]["pid"], spawned[0]["pgid"])
        for key in ("stdout_path", "stderr_path"):
            self.assertEqual(stat.S_IMODE(Path(outcome.result[key]).stat().st_mode), 0o600)

    def test_new_codex_session_id_is_observed(self):
        self.fixture([{"type": "thread.started", "thread_id": "new-session"},
                      {"type": "turn.completed"}])
        outcome = self.run_cli(session_id=None)
        self.assertEqual(outcome.session_id, "new-session")
        self.assertEqual(json.loads((self.worktree / "invocation.json").read_text())["argv"],
                         ["exec", "--json", "-"])

    def test_codex_message_only_quota_has_no_invented_reset(self):
        for message in (
            "You've hit your usage limit. Try again later.",
            "You've hit your usage limit. Try again at 4:31 AM.",
            "You've hit your usage limit for GPT-5. Switch to another model now, or try again later.",
        ):
            with self.subTest(message=message):
                self.fixture([{"type": "turn.failed", "error": {"message": message}}], exit_code=1)
                outcome = self.run_cli()
                self.assertEqual(outcome.category, "quota")
                self.assertIsNone(outcome.reset_at)

    def test_structured_reset_at_is_normalized(self):
        for value, expected in ((1800000000, 1800000000.0),
                                ("2026-09-13T12:00:00+00:00", 1789300800.0)):
            with self.subTest(value=value):
                self.fixture([{"type": "turn.failed", "error": {
                    "code": "usage_limit_reached", "reset_at": value}}], exit_code=1)
                outcome = self.run_cli()
                self.assertEqual(outcome.category, "quota")
                self.assertEqual(outcome.reset_at, expected)

    def test_model_content_and_stderr_do_not_trigger_retry(self):
        self.fixture([{"type": "item.completed", "item": {"type": "agent_message",
            "text": "You've hit your usage limit. rate_limit_exceeded"}}], exit_code=1,
            extra="print('rate_limit_exceeded: quota exhausted', file=sys.stderr)")
        self.assertEqual(self.run_cli().category, "unknown")

    def test_auth_permission_context_and_unknown_are_not_retryable(self):
        for code, expected in (("invalid_api_key", "authentication"),
                               ("permission_denied", "permission"),
                               ("approval_required", "approval_required"),
                               ("context_length_exceeded", "context_exhausted"),
                               ("test_failure", "test_failure"),
                               ("code_error", "code_error"),
                               ("unknown_limit", "unknown")):
            with self.subTest(code=code):
                self.fixture([{"type": "turn.failed", "error": {"code": code}}], exit_code=1)
                self.assertEqual(self.run_cli().category, expected)

    def test_success_overrides_recovered_intermediate_errors(self):
        self.fixture([{"type": "error", "message": "rate limit exceeded: retrying"},
                      {"type": "turn.completed"}])
        self.assertEqual(self.run_cli().category, "success")

    def test_quota_without_session_preserves_waiting_reason(self):
        self.fixture([{"type": "turn.failed", "error": {"code": "rate_limit_exceeded"}}], exit_code=1)
        outcome = self.run_cli(session_id=None)
        self.assertEqual(outcome.category, "rate_limit")
        self.assertIsNone(outcome.session_id)

    def test_invalid_observed_session_cannot_become_a_resume_option(self):
        for invalid in ("--last", "a\n--last", "a b", 123, "x" * 201):
            with self.subTest(invalid=invalid):
                self.fixture([{"type": "thread.started", "thread_id": invalid},
                              {"type": "turn.completed"}])
                self.assertEqual(self.run_cli(session_id=None).category, "reconciliation")

    def test_invalid_explicit_session_rejected_before_spawn(self):
        self.fixture([])
        with self.assertRaises(ValueError):
            self.run_cli(session_id="--last")
        self.assertFalse((self.worktree / "invocation.json").exists())

    def test_session_mismatch_requires_reconciliation(self):
        self.fixture([{"type": "thread.started", "thread_id": "different-session"},
                      {"type": "turn.completed"}])
        outcome = self.run_cli()
        self.assertEqual(outcome.category, "reconciliation")
        self.assertEqual(outcome.session_id, "session-1")

    def test_claude_rejected_quota_uses_resets_at(self):
        self.fixture([{"type": "system", "subtype": "init", "session_id": "session-1"},
                      {"type": "rate_limit_event", "session_id": "session-1",
                       "rate_limit_info": {"status": "rejected", "resetsAt": 1800000000}},
                      {"type": "assistant", "session_id": "session-1", "error": "rate_limit",
                       "message": {"content": []}},
                      {"type": "result", "subtype": "success", "is_error": True,
                       "session_id": "session-1", "api_error_status": 429}], exit_code=1)
        outcome = self.run_cli("claude")
        self.assertIn(outcome.category, {"rate_limit", "quota"})
        self.assertEqual(outcome.reset_at, 1800000000.0)
        invocation = json.loads((self.worktree / "invocation.json").read_text())
        self.assertEqual(invocation["argv"], ["-p", "--output-format", "stream-json",
                                               "--verbose", "--resume", "session-1"])

    def test_claude_authentication_and_permission_denials_are_not_success(self):
        for event, expected in (({"type": "result", "subtype": "success", "is_error": True,
                                  "api_error_status": 401}, "authentication"),
                                ({"type": "result", "subtype": "success", "is_error": False,
                                  "permission_denials": [{"tool_name": "Bash"}]}, "permission")):
            with self.subTest(expected=expected):
                self.fixture([event])
                self.assertEqual(self.run_cli("claude").category, expected)

    def test_claude_budget_and_turn_limits_override_earlier_quota(self):
        for subtype in ("error_max_turns", "error_max_budget_usd", "error_max_structured_output_retries"):
            with self.subTest(subtype=subtype):
                self.fixture([
                    {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"}},
                    {"type": "result", "subtype": subtype, "is_error": True},
                ], exit_code=1)
                self.assertEqual(self.run_cli("claude").category, "code_error")

    def test_claude_unknown_api_failure_does_not_reuse_stale_quota(self):
        for details in ({"api_error_status": 400}, {"errors": ["unclassified failure"]}):
            with self.subTest(details=details):
                self.fixture([
                    {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"}},
                    {"type": "result", "subtype": "error_during_execution", "is_error": True, **details},
                ], exit_code=1)
                self.assertEqual(self.run_cli("claude").category, "unknown")

    def test_claude_context_and_interrupt_override_result_success(self):
        for details, expected in (
            ({"stop_reason": "model_context_window_exceeded"}, "context_exhausted"),
            ({"terminal_reason": "aborted_tools"}, "reconciliation"),
            ({"terminal_reason": "max_turns"}, "code_error"),
        ):
            with self.subTest(details=details):
                self.fixture([{"type": "result", "subtype": "success", "is_error": False, **details}])
                self.assertEqual(self.run_cli("claude").category, expected)

    def test_malformed_json_cannot_authorize_retry(self):
        self.fixture([{"type": "turn.failed", "error": {"code": "rate_limit_exceeded"}}],
                     exit_code=1, extra="print('not protocol JSON')")
        self.assertEqual(self.run_cli().category, "unknown")

    def test_billing_and_sandbox_failures_never_become_quota(self):
        errors = [
            {"code": "insufficient_quota"},
            {"message": "Quota exceeded. Check your plan and billing details."},
            {"message": "Your workspace is out of credits. Add credits to continue."},
            {"message": "You hit your spend cap set in your workspace. Increase your spend cap to continue."},
            {"message": "sandbox error: You've hit your usage limit. Try again later."},
            {"message": "sandbox error: rate limit exceeded: tool text"},
            {"message": "codex-linux-sandbox was required but not provided"},
        ]
        for error in errors:
            with self.subTest(error=error):
                self.fixture([{"type": "turn.failed", "error": error}], exit_code=1)
                self.assertIn(self.run_cli().category, {"authentication", "permission"})

    def test_codex_message_only_context_and_rate_limit(self):
        for message, expected in (
            ("Codex ran out of room in the model's context window. Start a new thread or clear earlier history before retrying.", "context_exhausted"),
            ("rate limit exceeded: requests per minute", "rate_limit"),
            ("request timed out", "transient_network"),
            ("stream disconnected before completion: connection closed", "transient_network"),
        ):
            with self.subTest(message=message):
                self.fixture([{"type": "turn.failed", "error": {"message": message}}], exit_code=1)
                self.assertEqual(self.run_cli().category, expected)

    def test_stream_wrapper_does_not_make_auth_or_sandbox_errors_retryable(self):
        for detail in ("authentication failed", "sandbox error: denied", "unknown failure"):
            with self.subTest(detail=detail):
                self.fixture([{"type": "turn.failed", "error": {
                    "message": "stream disconnected before completion: " + detail}}], exit_code=1)
                self.assertEqual(self.run_cli().category, "unknown")

    def test_claude_warning_and_model_text_do_not_block_success(self):
        self.fixture([{"type": "rate_limit_event", "rate_limit_info": {"status": "allowed_warning"}},
                      {"type": "assistant", "message": {"content": [
                          {"type": "text", "text": "rate_limit, authentication_failed"}]}},
                      {"type": "result", "subtype": "success", "is_error": False}])
        self.assertEqual(self.run_cli("claude").category, "success")

    def test_large_output_does_not_deadlock(self):
        self.fixture([{"type": "turn.completed"}], extra=(
            "sys.stderr.write('x' * 2_000_000)\n"
            "print(json.dumps({'type': 'item.completed', 'item': {'text': 'x' * 1_000_000}}))"))
        self.assertEqual(self.run_cli(timeout_seconds=3).category, "success")

    def test_timeout_terminates_process_group_and_descendant(self):
        self.fixture([], extra=(
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)'])\n"
            "pathlib.Path('child.pid').write_text(str(child.pid))\n"
            "time.sleep(30)"))
        spawned = []
        outcome = self.run_cli(timeout_seconds=0.3, on_spawn=spawned.append)
        self.assertEqual(outcome.category, "reconciliation")
        self.assertIn("stopped=True", outcome.message)
        child = int((self.worktree / "child.pid").read_text())
        for pid in (spawned[0]["pid"], child):
            info = _proc_info(pid)
            self.assertTrue(info is None or info["state"] == "Z")

    def test_spawn_callback_failure_terminates_child(self):
        self.fixture([], extra="time.sleep(30)")
        spawned = []

        def fail(record):
            spawned.append(record)
            raise RuntimeError("database unavailable")

        outcome = self.run_cli(on_spawn=fail)
        self.assertEqual(outcome.category, "reconciliation")
        self.assertIsNone(_proc_info(spawned[0]["pid"]))

    def test_timeout_keeps_observed_new_session(self):
        self.fixture([{"type": "thread.started", "thread_id": "new-session"}], extra="time.sleep(30)")
        outcome = self.run_cli(session_id=None, timeout_seconds=0.2)
        self.assertEqual(outcome.category, "reconciliation")
        self.assertEqual(outcome.session_id, "new-session")

    def test_live_descendant_after_cli_exit_requires_reconciliation(self):
        self.fixture([{"type": "turn.completed"}], extra=(
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
            "pathlib.Path('child.pid').write_text(str(child.pid))"))
        outcome = self.run_cli()
        self.assertEqual(outcome.category, "reconciliation")
        child = int((self.worktree / "child.pid").read_text())
        info = _proc_info(child)
        self.assertTrue(info is None or info["state"] == "Z")

    def test_logs_are_unique_across_retries(self):
        self.fixture([{"type": "turn.completed"}])
        first = self.run_cli()
        second = self.run_cli()
        self.assertNotEqual(first.result["stdout_path"], second.result["stdout_path"])
        self.assertTrue(Path(first.result["stdout_path"]).exists())


    def test_ultracode_new_and_resume_preserve_separate_settings(self):
        for sid in (None, 'session-1'):
            with self.subTest(session=sid):
                self.fixture([{'type':'system','subtype':'init','session_id':'session-1','model':'claude-opus-5',
                               'effort':'xhigh','ultracode':True},
                              {'type':'result','session_id':'session-1','subtype':'success','is_error':False,
                               'modelUsage':{'claude-opus-5':{},'claude-haiku-4-5':{}}}])
                outcome=self.run_cli(provider='claude',session_id=sid,model='claude-opus-5',
                                     reasoning_effort='xhigh',ultracode_enabled=True)
                argv=json.loads((self.worktree/'invocation.json').read_text())['argv']
                self.assertEqual(argv[argv.index('--effort')+1],'ultracode')
                self.assertEqual(outcome.result['requested_effort'],'xhigh')
                self.assertEqual(outcome.result['observed_models'],['claude-opus-5'])
                self.assertEqual(outcome.result['auxiliary_models'],['claude-haiku-4-5'])
                self.assertEqual(outcome.result['observed_ultracode'],[True])

    def test_codex_new_and_resume_preserve_model_effort_schema(self):
        for sid in (None, 'session-1'):
            self.fixture([{'type':'thread.started','thread_id':'session-1'},{'type':'turn.completed'}])
            outcome=self.run_cli(session_id=sid,model='gpt-6-astra',reasoning_effort='high',
                                 output_schema={'type':'object','properties':{},'additionalProperties':False})
            argv=json.loads((self.worktree/'invocation.json').read_text())['argv']
            self.assertIn('model_reasoning_effort="high"',argv)
            self.assertEqual(argv[argv.index('--model')+1],'gpt-6-astra')
            self.assertTrue(Path(argv[argv.index('--output-schema')+1]).is_file())
            self.assertEqual(outcome.result['observed_efforts'],[])

    def test_unknown_or_active_workflow_cgroup_prevents_termination_claim(self):
        from unittest.mock import patch
        from ai_company.adapters.session_cli import service_alive
        from ai_company.sessions import execution_alive
        import subprocess
        unit='ai-company-run-'+'a'*32+'.service'
        with patch('ai_company.adapters.session_cli.subprocess.run',return_value=subprocess.CompletedProcess([],0,'LoadState=loaded\nActiveState=active\nControlGroup=\n','')):
            self.assertTrue(service_alive(unit))
            self.assertTrue(execution_alive({'boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                                            'pgid':99999999,'systemd_unit':unit}))
        self.assertTrue(service_alive('unrelated-operating-service.service'))


if __name__ == "__main__":
    unittest.main()
