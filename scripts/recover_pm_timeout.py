#!/usr/bin/env python3
"""Read-only timeout diagnosis by default; explicit stopped-worker recovery on approval."""

import argparse
import json
from pathlib import Path
import subprocess

from ai_company.timeout_recovery import diagnose, apply_in_stopped_environment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case-root', required=True, type=Path)
    parser.add_argument('--shared-call-ledger', required=True, type=Path)
    parser.add_argument('--baseline-ledger', required=True, type=Path)
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--expected-digest')
    args = parser.parse_args()
    if not args.apply:
        plan = diagnose(args.case_root, args.shared_call_ledger, args.baseline_ledger, args.job_id)
        print(json.dumps({'decision': plan['decision'], 'digest': plan['digest'],
                          'checks': plan['checks'], 'duration_seconds': plan['duration_seconds']}))
        return
    if not args.expected_digest:
        parser.error('--apply requires the exact read-only diagnosis digest')
    for unit in ('ai-company-automation.service', 'ai-company-translation.service'):
        active = subprocess.run(['systemctl', '--user', 'is-active', unit], capture_output=True, text=True)
        enabled = subprocess.run(['systemctl', '--user', 'is-enabled', unit], capture_output=True, text=True)
        if active.stdout.strip() != 'inactive' or enabled.stdout.strip() != 'disabled':
            raise SystemExit('both operating workers must be inactive and disabled')
    result = apply_in_stopped_environment(args.case_root, args.shared_call_ledger,
        args.baseline_ledger, args.job_id, args.expected_digest)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
