"""Explicit offline snapshot export; does not load a queue or operational DB."""
import argparse
import json
from pathlib import Path

from ai_company.diagram_export import DiagramExportError, export_diagram


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--project", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.snapshot.stat().st_size > 2_000_000:
        parser.error("snapshot exceeds 2 MB")
    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    try:
        receipt = export_diagram(snapshot, args.output, project_id=args.project)
    except DiagramExportError as error:
        print(json.dumps({"status": "failed", "code": error.code, "receipt": error.receipt}, ensure_ascii=False))
        return 1
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
