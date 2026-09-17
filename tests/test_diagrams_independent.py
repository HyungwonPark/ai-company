"""Independent artifact semantics and post-publication interruption checks."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai_company import diagram_export, management_diagrams
from ai_company.management import ManagementStore
from test_diagram_export import snapshot
from test_management_diagrams import seed_module


class DiagramIndependentTests(unittest.TestCase):
    def test_downloaded_workflow_exposes_recorded_wait_state(self):
        value = snapshot()
        role = value["nodes"][1]
        role.update(phase="recorded", status="WAITING_QUOTA", wait_reason="공유 계정 한도 회복 대기")
        workflow, mapping = diagram_export.to_archify_workflow(value, project_id=value["project_id"])
        rendered_role = next(node for node in workflow["nodes"] if node["id"] == mapping[role["id"]])
        self.assertIn("대기", rendered_role["sublabel"], "SVG-only readers need the recorded state, not just its provenance")

    def test_original_relationship_list_identifies_excluded_handoff_owner(self):
        value = snapshot()
        value["nodes"][1]["name"] = "구현 담당"
        edge = copy.deepcopy(value["edges"][0])
        edge.update(id="independent-handoff", kind="handoff", title="담당 이관", reason="공유 계정 한도 회복 대기",
                    phase="recorded", status="waiting", **{"from": "graph:dev", "to": "graph:dev"})
        value["edges"].append(edge)
        document = diagram_export._html(value, '<svg role="img"></svg>')
        relationships = document.split('<section id="relations">', 1)[1].split('</section>', 1)[0]
        self.assertIn("구현 담당", relationships, "the visible relationship fallback must identify the handoff role")
        self.assertIn("공유 계정 한도 회복 대기", relationships)

    def test_interruption_after_bundle_publish_recovers_without_recompiling_or_execution_writes(self):
        with tempfile.TemporaryDirectory(prefix="independent-diagram-") as directory:
            root = Path(directory)
            store = ManagementStore(root)
            try:
                management_diagrams.initialize(store.db)
                identifiers = seed_module.seed_graph(store, root)
                pid = identifiers["project_id"]
                target = next(item for item in store.overview(pid)["workspace_graph"]["snapshots"] if not item["run_id"])
                payload = {"snapshot_id": target["id"], "fingerprint": target["fingerprint"], "idempotency_key": "after-publish-interruption"}
                preserved = {table: store.db.execute("SELECT * FROM " + table).fetchall() for table in
                             ("management_runs", "management_plans", "management_approvals", "management_events", "quota_groups")}
                write_json = diagram_export._atomic_json

                def interrupt_latest(path, value):
                    if path.name == "latest.json":
                        raise KeyboardInterrupt("simulated process loss after immutable bundle publication")
                    return write_json(path, value)

                with patch.object(diagram_export, "_atomic_json", side_effect=interrupt_latest):
                    with self.assertRaises(KeyboardInterrupt):
                        management_diagrams.generate(store, pid, payload)
                prepared = json.loads(store.db.execute("SELECT document FROM management_diagrams").fetchone()[0])
                self.assertEqual(prepared["status"], "prepared")
                receipts = list((root / "diagrams" / pid).glob("*/receipt.json"))
                self.assertEqual(len(receipts), 1, "the immutable bundle already exists at this interruption boundary")
                original_receipt = json.loads(receipts[0].read_text())
                with patch.object(store, "overview", side_effect=AssertionError("must resume the original frozen snapshot")), \
                     patch.object(diagram_export.subprocess, "run", side_effect=AssertionError("published bundle must not compile twice")):
                    restored = management_diagrams.generate(store, pid, payload)
                self.assertEqual(restored["status"], "completed")
                self.assertEqual(restored["receipt"], original_receipt)
                stored_input = management_diagrams.read_file(store, pid, restored["receipt"]["id"], "input.json")
                self.assertEqual(json.loads(stored_input), prepared["snapshot"])
                self.assertEqual(store.db.execute("SELECT COUNT(*) FROM management_diagrams").fetchone()[0], 1)
                for table, rows in preserved.items():
                    self.assertEqual(rows, store.db.execute("SELECT * FROM " + table).fetchall(), table)
            finally:
                store.close()
