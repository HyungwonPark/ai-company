"""Real Archify + temporary management records; no model/operational calls."""
import copy
import fcntl
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai_company import management_diagrams as diagrams
from ai_company.diagram_export import DiagramExportError
from ai_company.management import ManagementError, ManagementStore

loader = importlib.util.spec_from_file_location("graph_seed", Path(__file__).parent / "ui/run_workspace_graph.py")
seed_module = importlib.util.module_from_spec(loader)
loader.loader.exec_module(seed_module)


class DiagramStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ManagementStore(Path(self.tmp.name))
        diagrams.initialize(self.store.db)
        self.ids = seed_module.seed_graph(self.store, Path(self.tmp.name))
        self.pid = self.ids["project_id"]
        self.snapshot = next(s for s in self.store.overview(self.pid)["workspace_graph"]["snapshots"] if not s["run_id"])
        self.body = {"snapshot_id": self.snapshot["id"], "fingerprint": self.snapshot["fingerprint"], "idempotency_key": "test-picture"}

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_generation_retry_download_and_no_execution_authority(self):
        before = {table: self.store.db.execute("SELECT * FROM " + table).fetchall() for table in
                  ("management_runs", "management_approvals", "management_events", "management_plans", "quota_groups")}
        result = diagrams.generate(self.store, self.pid, self.body)
        self.assertEqual(result["status"], "completed")
        self.assertNotIn("snapshot", result)
        with patch.object(diagrams, "export_diagram", side_effect=AssertionError("must not regenerate")):
            self.assertEqual(result, diagrams.generate(self.store, self.pid, self.body))
        receipt = result["receipt"]
        for name in ("index.html", "preview-light.html", "preview-black.html", "diagram.svg", "input.json"):
            data = diagrams.read_file(self.store, self.pid, receipt["id"], name)
            self.assertEqual(hashlib.sha256(data).hexdigest(), receipt["files"][name]["sha256"])
        for table, rows in before.items():
            self.assertEqual(rows, self.store.db.execute("SELECT * FROM " + table).fetchall(), table)
        self.assertEqual(len(diagrams.listing(self.store, self.pid)["items"]), 1)

    def test_scope_stale_input_commands_and_key_conflict_rejected(self):
        for pid, body in ((self.ids["other_project_id"], self.body), (self.pid, {**self.body, "fingerprint": "0"*64}),
                          (self.pid, {**self.body, "node_command": "sh"}), (self.pid, {**self.body, "snapshot": self.snapshot})):
            with self.assertRaises(ManagementError):
                diagrams.generate(self.store, pid, body)
        first = diagrams.generate(self.store, self.pid, self.body)
        with self.assertRaises(ManagementError):
            diagrams.generate(self.store, self.pid, {**self.body, "fingerprint": "0"*64})
        with self.assertRaises(ManagementError):
            diagrams.read_file(self.store, self.ids["other_project_id"], first["receipt"]["id"], "index.html")

    def test_interrupted_request_recovers_same_frozen_bytes(self):
        with patch.object(diagrams, "export_diagram", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                diagrams.generate(self.store, self.pid, self.body)
        prepared = json.loads(self.store.db.execute("SELECT document FROM management_diagrams").fetchone()[0])
        self.assertEqual(prepared["status"], "prepared")
        # A reconnect must not replace the original observation, even if the API changed.
        with patch.object(self.store, "overview", side_effect=AssertionError("must reuse stored snapshot")):
            result = diagrams.generate(self.store, self.pid, self.body)
        source = json.loads(diagrams.read_file(self.store, self.pid, result["receipt"]["id"], "input.json"))
        self.assertEqual(source, prepared["snapshot"])

    def test_failure_keeps_prior_download_and_history(self):
        first = diagrams.generate(self.store, self.pid, self.body)
        with patch.object(diagrams, "export_diagram", side_effect=DiagramExportError("fixture_failure", "예시 실패")):
            failed = diagrams.generate(self.store, self.pid, {**self.body, "idempotency_key": "failed-picture"})
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(len(diagrams.listing(self.store, self.pid)["items"]), 2)
        self.assertIn(b"<!doctype", diagrams.read_file(self.store, self.pid, first["receipt"]["id"], "index.html"))

    def test_lock_and_tampered_artifact_fail_closed(self):
        target = diagrams._directory(self.store, self.pid)
        with (target.parent / ".generation.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(ManagementError) as caught:
                diagrams.generate(self.store, self.pid, self.body)
            self.assertEqual(caught.exception.code, "diagram_busy")
        result = diagrams.generate(self.store, self.pid, self.body)
        file = target / result["receipt"]["id"] / "index.html"
        file.write_text("tampered")
        with self.assertRaises(ManagementError) as caught:
            diagrams.read_file(self.store, self.pid, result["receipt"]["id"], "index.html")
        self.assertEqual(caught.exception.code, "artifact_integrity")
        file.unlink()
        file.symlink_to(target / "latest.json")
        with self.assertRaises(ManagementError):
            diagrams.read_file(self.store, self.pid, result["receipt"]["id"], "index.html")


class DiagramHTTPTests(unittest.TestCase):
    from test_management_server import ManagementHTTPTests as _HTTP
    setUp = _HTTP.setUp
    tearDown = _HTTP.tearDown
    request = _HTTP.request
    login = _HTTP.login

    def test_master_auth_csrf_and_opaque_html_without_global_csp_change(self):
        store = ManagementStore(self.root / "state")
        try:
            ids = seed_module.seed_graph(store, self.root / "state")
            snapshot = next(s for s in store.overview(ids["project_id"])["workspace_graph"]["snapshots"] if not s["run_id"])
        finally:
            store.close()
        path = "/api/projects/" + ids["project_id"] + "/diagrams"
        body = {"snapshot_id": snapshot["id"], "fingerprint": snapshot["fingerprint"], "idempotency_key": "http-picture"}
        self.assertEqual(self.request("GET", path)[0], 401)
        self.login()
        self.assertEqual(self.request("POST", path, body, headers={"X-CSRF-Token": "wrong"})[0], 403)
        self.assertEqual(self.request("POST", path, body, headers={"Origin": "https://untrusted.invalid"})[0], 403)
        status, result, _ = self.request("POST", path, body)
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "completed", result)
        file = path + "/" + result["receipt"]["id"] + "/index.html"
        self.assertEqual(self.request("GET", file, authenticated=False)[0], 401)
        status, html, headers = self.request("GET", file)
        self.assertEqual(status, 200)
        self.assertIn("sandbox; default-src 'none'", headers["Content-Security-Policy"])
        self.assertIn("script-src 'none'", headers["Content-Security-Policy"])
        for theme in ("light", "black"):
            preview = path + "/" + result["receipt"]["id"] + "/preview-" + theme + ".html"
            status, _, preview_headers = self.request("GET", preview)
            self.assertEqual(status, 200)
            self.assertIn("sandbox;", preview_headers["Content-Security-Policy"])
            self.assertNotIn("Content-Disposition", preview_headers)
            self.assertEqual(self.request("GET", preview, authenticated=False)[0], 401)
        self.assertIn("attachment", self.request("GET", file + "?download=1")[2]["Content-Disposition"])
        self.assertNotIn("unsafe-inline", self.request("GET", "/")[2]["Content-Security-Policy"])
        self.assertEqual(self.request("POST", file, {})[0], 405)
