"""Diagnose the settled E1 PM result; apply only to an explicit state target.

The operating apply mode is reserved for a later, separate deployment approval.
No mode starts workers, calls a model, or creates a shared reservation.
"""

import argparse
import json
from pathlib import Path
import subprocess

from ai_company.pm_evidence_recovery import (
    apply_to_isolated_state,
    apply_to_operating_state,
    diagnose,
    verify_saved_recovery,
    verify_validator_release,
)
from ai_company.storage import controller_lock


def _workers_stopped():
    for unit in ("ai-company-automation.service", "ai-company-translation.service"):
        active = subprocess.run(["systemctl", "--user", "is-active", unit],
                                capture_output=True, text=True, check=False)
        enabled = subprocess.run(["systemctl", "--user", "is-enabled", unit],
                                 capture_output=True, text=True, check=False)
        if active.stdout.strip() != "inactive" or enabled.stdout.strip() != "disabled":
            raise RuntimeError("both workers must remain inactive and disabled through E1 adoption")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("diagnose", "apply-isolated", "apply-operating-and-canary", "verify"))
    parser.add_argument("--origin-root", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--shared-call-ledger", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--codex-home", type=Path, required=True)
    parser.add_argument("--evidence-manifest", type=Path, required=True)
    parser.add_argument("--validator-commit", required=True)
    args = parser.parse_args(argv)
    verify_validator_release(args.validator_commit)
    diagnosis = diagnose(args.origin_root, args.state_root, args.shared_call_ledger,
                         args.config, args.codex_home,
                         validator_commit=args.validator_commit,
                         evidence_manifest=args.evidence_manifest)
    if args.mode == "diagnose":
        result = {"state": "recoverable", "revision_id": diagnosis.revision["id"],
                  "profile": diagnosis.revision["profile"], "model_calls": 0}
    elif args.mode == "apply-isolated":
        result = {**apply_to_isolated_state(diagnosis), "model_calls": 0}
    elif args.mode == "apply-operating-and-canary":
        if diagnosis.state_root != diagnosis.origin_root:
            parser.error("operating adoption requires the actual E1 state")
        from evaluate_pm_behavior import record_recovered_canary, recovered_canary
        root = diagnosis.origin_root.parent
        with controller_lock(root / "evaluation-runner", blocking=False), \
             controller_lock(diagnosis.state_root / "automation-coordinator", blocking=False):
            _workers_stopped()
            applied = apply_to_operating_state(diagnosis)
            canary = root / "canary-result.json"
            marker = recovered_canary(root, diagnosis.shared_path, diagnosis.config_path,
                diagnosis.codex_home, diagnosis.evidence_manifest, args.validator_commit,
                allow_progress=canary.exists())
            record_recovered_canary(canary, marker)
            _workers_stopped()
        result = {**applied, "canary_state": "recorded", "model_calls": 0}
    else:
        result = {**verify_saved_recovery(diagnosis), "model_calls": 0}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
