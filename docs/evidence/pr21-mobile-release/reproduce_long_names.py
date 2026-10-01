"""Synthetic reproduction only: PYTHONPATH=src python3 <this file>. Requires Node."""
import json
from pathlib import Path
import tempfile

from ai_company.diagram_export import export_diagram


def main():
    binding = {"project_id": "synthetic_project", "plan_id": "synthetic_plan",
               "plan_digest": "a" * 64, "run_id": None}
    results = []
    with tempfile.TemporaryDirectory() as output:
        for size in (2, 12, 24, 48, 100):
            node = {**binding, "id": "synthetic_role", "kind": "role", "name": "역" * size,
                    "status": "PLANNED", "phase": "planned", "source": "fixture"}
            snapshot = {**binding, "id": "synthetic_snapshot", "source": "fixture", "mode": "planned",
                        "cursor": 0, "observed_at": 1, "warnings": [], "nodes": [node], "edges": [],
                        "report_refs": [], "approval_refs": []}
            receipt = export_diagram(snapshot, Path(output) / str(size), project_id=binding["project_id"])
            bundle = Path(output) / str(size) / receipt["directory"]
            assert json.loads((bundle / "input.json").read_text()) == snapshot
            assert json.loads((bundle / "archify.json").read_text())["nodes"][0]["label"] == node["name"]
            results.append({"characters": size, "compiler": receipt["validation"]["compiler"],
                            "original_preserved": True})
    print(json.dumps({"source": "synthetic; no operational records", "results": results}, indent=2))


if __name__ == "__main__":
    main()
