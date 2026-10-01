"""Real overlapping workers over isolated Git fixtures; no model claims."""
from concurrent.futures import ThreadPoolExecutor
import subprocess
from threading import Event
from unittest.mock import patch

from ai_company.adapters.session_cli import SessionOutcome
from ai_company.dispatcher import Dispatcher
from test_dispatcher import FlowFixture, FixtureVerifier, Crash


class ParallelDispatcherTests(FlowFixture):
    def independent(self, name, *, same_repository=False):
        repo = self.repo
        if not same_repository:
            repo = self.root / name
            subprocess.run(["git", "clone", "-q", str(self.repo), str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "remote", "set-url", "origin",
                            "https://github.com/owner/repo.git"], check=True)
        task = self.task.model_copy(update={"task_id": name})
        spec = self.spec.model_copy(update={"task": task, "worktree": str(repo)})
        self.dispatcher.submit(spec)
        return spec

    def worker(self, executor, verifier=None):
        worker = Dispatcher(self.root / "state", clock=lambda: self.now,
                            verifier=verifier or self.verifier, executor=executor)
        try:
            return worker.run_once()
        finally:
            worker.close()

    @staticmethod
    def retry(*args, **kwargs):
        return SessionOutcome("transient_network", session_id="parallel-session",
                              result={"duration_seconds": 1, "total_cost_usd": 0.01})

    def test_independent_workers_overlap_before_spawn_without_false_recovery(self):
        self.submit()
        second = self.independent("other")
        entered, release = Event(), Event()
        def slow(*args, **kwargs):
            entered.set()
            if not release.wait(10):
                raise AssertionError("parallel worker did not release first executor")
            return self.retry()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.worker, slow)
            try:
                self.assertTrue(entered.wait(5))
                running = self.state()
                job = self.dispatcher.queue.get(running["active"]["job_id"])
                self.assertEqual(job["status"], "RUNNING")
                self.assertIsNone(job["process"])  # exact pre-on_spawn boundary
                other = pool.submit(self.worker, self.retry).result(timeout=5)
                self.assertEqual(other["task_id"], second.task.task_id)
                self.assertEqual(other["usage"]["executions"], 1)
                self.assertEqual(self.dispatcher.queue.get(job["job_id"])["status"], "RUNNING")
                # Reads remain available while another role is inside execution.
                self.assertEqual(len(self.dispatcher.tasks()), 2)
            finally:
                release.set()
            self.assertEqual(first.result(timeout=5)["usage"]["executions"], 1)

    def test_same_git_repository_waits_without_blocking_or_duplicate_execution(self):
        self.submit()
        second = self.independent("other", same_repository=True)
        entered, release = Event(), Event()
        def slow(*args, **kwargs):
            entered.set()
            if not release.wait(10):
                raise AssertionError("release timed out")
            return self.retry()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.worker, slow)
            try:
                self.assertTrue(entered.wait(5))
                result = pool.submit(self.worker, self.retry).result(timeout=5)
                self.assertEqual(result["status"], "IDLE")
                untouched = self.dispatcher.get(second.task.task_id)
                self.assertEqual(untouched["status"], "READY")
                self.assertEqual(untouched["usage"]["executions"], 0)
            finally:
                release.set()
            first.result(timeout=5)

    def test_two_slot_bound_preserves_third_task(self):
        self.submit()
        self.independent("other")
        third = self.independent("third")
        first_entered, second_entered, release = Event(), Event(), Event()
        def slow(event):
            def run(*args, **kwargs):
                event.set()
                if not release.wait(10):
                    raise AssertionError("release timed out")
                return self.retry()
            return run
        with ThreadPoolExecutor(max_workers=3) as pool:
            first = pool.submit(self.worker, slow(first_entered))
            self.assertTrue(first_entered.wait(5))
            second = pool.submit(self.worker, slow(second_entered))
            try:
                self.assertTrue(second_entered.wait(5))
                result = pool.submit(self.worker, self.retry).result(timeout=5)
                self.assertEqual(result["status"], "BUSY")
                self.assertEqual(self.dispatcher.get(third.task.task_id)["generation"], 0)
            finally:
                release.set()
            first.result(timeout=5)
            second.result(timeout=5)

    def test_independent_role_runs_during_local_checks(self):
        self.submit()
        self.tick()  # committed developer fixture; checks are next
        second = self.independent("other")
        entered, release = Event(), Event()
        class SlowChecks(FixtureVerifier):
            def check(inner, spec, state):
                entered.set()
                if not release.wait(10):
                    raise AssertionError("release timed out")
                return super().check(spec, state)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.worker, self.retry, SlowChecks())
            try:
                self.assertTrue(entered.wait(5))
                other = pool.submit(self.worker, self.retry).result(timeout=5)
                self.assertEqual(other["task_id"], second.task.task_id)
                self.assertEqual(self.state()["status"], "CHECK_RUNNING")
            finally:
                release.set()
            self.assertEqual(first.result(timeout=5)["stage"], "reviewer")

    def test_crash_releases_ownership_but_does_not_replay_uncertain_execution(self):
        self.submit()
        self.independent("other")
        entered, release = Event(), Event()
        def crash(*args, **kwargs):
            entered.set()
            if not release.wait(10):
                raise AssertionError("release timed out")
            raise Crash()
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.worker, crash)
            try:
                self.assertTrue(entered.wait(5))
                pool.submit(self.worker, self.retry).result(timeout=5)
            finally:
                release.set()
            with self.assertRaises(Crash):
                first.result(timeout=5)
        calls = []
        def forbidden(*args, **kwargs):
            calls.append(True)
            return self.retry()
        self.worker(forbidden)
        self.assertEqual(self.state()["status"], "NEEDS_RECONCILIATION")
        self.assertEqual(calls, [])


    def test_live_orphan_retains_capacity_after_controller_lock_is_released(self):
        self.submit()
        second = self.independent("other")
        third = self.independent("third")
        # Persist the crash boundary; a verified live process outlives its owner.
        state = self.state()
        self.dispatcher._assign(state, self.spec, self.spec.agents[1])
        self.dispatcher._materialize(state, self.spec)
        job = self.dispatcher.queue._claim(state["active"]["job_id"])
        job["process"] = {"test_live_orphan": True}
        with self.dispatcher.db:
            self.dispatcher.queue._save(job)
        state.update(status="NEEDS_RECONCILIATION", resume_at=None)
        with self.dispatcher.db:
            self.dispatcher._save(state, "fixture_orphan")
        entered, release = Event(), Event()
        def alive(identity):
            return bool(identity and identity.get("test_live_orphan"))
        def slow(*args, **kwargs):
            entered.set()
            if not release.wait(10):
                raise AssertionError("release timed out")
            return self.retry()
        with patch("ai_company.dispatcher.execution_alive", side_effect=alive), patch("ai_company.sessions.execution_alive", side_effect=alive):
            with ThreadPoolExecutor(max_workers=2) as pool:
                running = pool.submit(self.worker, slow)
                try:
                    self.assertTrue(entered.wait(5))
                    self.assertEqual(self.dispatcher.get(second.task.task_id)["active"]["agent_id"], "astra")
                    blocked = pool.submit(self.worker, self.retry).result(timeout=5)
                    self.assertEqual(blocked["status"], "BUSY")
                    self.assertEqual(self.dispatcher.get(third.task.task_id)["generation"], 0)
                finally:
                    release.set()
                running.result(timeout=5)


    def test_worker_reloads_tasks_after_yield_before_retry_or_budget_decisions(self):
        self.submit()
        self.tick()
        other = self.independent("other")
        entered, release = Event(), Event()
        class WaitingChecks(FixtureVerifier):
            def check(inner, spec, state):
                entered.set()
                if not release.wait(10):
                    raise AssertionError("release timed out")
                return super().check(spec, state)
            def remote(inner, spec, state):
                return None
        calls = []
        def executor(name):
            def execute(agent, state, *args, **kwargs):
                calls.append((name, state["task_id"], state["generation"]))
                return self.retry()
            return execute
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.worker, executor("A"), WaitingChecks())
            try:
                self.assertTrue(entered.wait(5))
                second = pool.submit(self.worker, executor("B")).result(timeout=5)
                self.assertEqual(second["status"], "WAITING_CAPACITY")
                self.assertGreater(second["resume_at"], self.now)
            finally:
                release.set()
            first.result(timeout=5)
        self.assertEqual(calls, [("B", other.task.task_id, 1)])
        jobs = [j for j in self.dispatcher.queue.list_jobs() if j["managed_by"] == other.task.task_id]
        self.assertEqual(len(jobs), 1)
        self.assertEqual(self.dispatcher.get(other.task.task_id)["usage"]["executions"], 1)

    def test_unknown_spawn_and_check_guards_reserve_slots_without_process_ids(self):
        self.submit()
        second = self.independent("other")
        third = self.independent("aaa")  # normal work sorts before recovery
        state = self.state()
        self.dispatcher._assign(state, self.spec, self.spec.agents[1])
        self.dispatcher._materialize(state, self.spec)
        job = self.dispatcher.queue._claim(state["active"]["job_id"])
        self.dispatcher.queue._write_guard(job)  # crash after spawn, before callback
        check = self.dispatcher.get(second.task.task_id)
        check.update(stage="check", status="CHECK_RUNNING", active=None)
        with self.dispatcher.db:
            self.dispatcher._save(check, "fixture_check_crash")
        self.dispatcher.queue._write_guard({"job_id": "flow-check-" + second.task.task_id,
            "repository_snapshot": check["snapshot"], "process": None, "lease_owner": "fixture-check"})
        calls = []
        def forbidden(*args, **kwargs):
            calls.append(True)
            return self.retry()
        self.assertEqual(self.worker(forbidden)["status"], "NEEDS_RECONCILIATION")
        self.assertEqual(self.worker(forbidden)["status"], "NEEDS_RECONCILIATION")
        self.assertEqual(self.worker(forbidden)["status"], "BUSY")
        self.assertEqual(calls, [])
        self.assertEqual(self.dispatcher.get(third.task.task_id)["generation"], 0)
