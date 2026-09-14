"""Read-only host preflight. Does not install, load profiles or launch workers."""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
from datetime import datetime, timezone

WORKER_PATH = "/home/edward/.local/bin:/home/edward/.nvm/versions/node/v22.13.0/bin:/usr/bin:/bin"
FILES = ["/home/edward/.config/systemd/user/ai-company-quota.timer",
         "/home/edward/.config/systemd/user/ai-company-quota.service",
         "/home/edward/talenta-site/Caddyfile", "/home/edward/talenta-site/docker-compose.yml"]


def run(argv, **kwargs):
    return subprocess.run(argv, capture_output=True, text=True, timeout=30, check=True, **kwargs)


def baseline():
    with sqlite3.connect("file:/home/edward/ai-company/state/sessions/sessions.sqlite?mode=ro", uri=True) as db:
        db.execute("BEGIN")
        rows = [json.loads(r[0]) for r in db.execute("SELECT document FROM session_jobs ORDER BY job_id")]
    health = {}
    for name in ("app", "caddy", "db", "backup"):
        raw = run(["docker", "inspect", "--format",
                   '{"id":{{json .Id}},"started_at":{{json .State.StartedAt}},"pid":{{.State.Pid}},"restarts":{{.RestartCount}},"status":{{json .State.Status}},"health":{{with index .State "Health"}}{{json .Status}}{{else}}null{{end}}}',
                   "talenta-edward-" + name + "-1"]).stdout
        health[name] = json.loads(raw)
    return {"files": {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in FILES},
            "queue_count": len(rows), "queue_digest": hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest(),
            "timer": run(["systemctl", "--user", "show", "ai-company-quota.timer",
                          "-p", "ActiveState", "-p", "UnitFileState"]).stdout,
            "services": health}


SOURCE = Path("/home/edward/ai-company/workspaces/agent-failover-loop/.ai-company/pr5-followup-2c04c489")
HASHES = {
    "bubblewrap_0.9.0-1ubuntu0.1_arm64.deb": "3fb4ca3a8d2060444836568ed49d6897a403467e4ba29c93440900093fb96a38",
    "package-inspection/usr/share/apparmor/extra-profiles/bwrap-userns-restrict": "11d39094f044f0cda0febb3ad517b830301da6b2ce929664af09ee9e4dd264f9",
}
TARGETS = (
    "/usr/bin/bwrap", "/etc/apparmor.d/bwrap-userns-restrict",
    "/etc/apparmor.d/local/bwrap-userns-restrict", "/etc/apparmor.d/local/unpriv_bwrap",
    "/var/tmp/ai-company-bwrap-20260914-reviewed",
)
KEYS = ("kernel.unprivileged_userns_clone", "kernel.apparmor_restrict_unprivileged_userns")


def main():
    os.environ["LC_ALL"] = "C"
    os.environ["PATH"] = WORKER_PATH
    values = {key: run(["/usr/sbin/sysctl", "-n", key]).stdout.strip() for key in KEYS}
    hashes = {name: hashlib.sha256((SOURCE / name).read_bytes()).hexdigest() for name in HASHES}
    existing = [name for name in TARGETS if Path(name).exists() or Path(name).is_symlink()]
    package = subprocess.run(["dpkg-query", "-W", "bubblewrap"], capture_output=True, text=True, timeout=30)
    simulation = run(["apt-get", "--simulate", "install", "--no-install-recommends", str(SOURCE / next(iter(HASHES)))]).stdout
    operations = re.findall(r"^(Inst|Conf|Remv|Purg) (\S+)", simulation, re.M)
    single_package = (
        "0 upgraded, 1 newly installed, 0 to remove" in simulation
        and [name for op, name in operations if op == "Inst"] == ["bubblewrap"]
        and all(op in ("Inst", "Conf") and name == "bubblewrap" for op, name in operations)
        and "Inst bubblewrap (0.9.0-1ubuntu0.1 " in simulation
    )
    # Non-root audit is useful evidence but does not replace root's locked pre-install check.
    audit = subprocess.run(["dpkg", "--audit"], capture_output=True, text=True, timeout=30)
    assignments = []
    for folder in ("/etc/sysctl.d", "/run/sysctl.d", "/usr/local/lib/sysctl.d", "/usr/lib/sysctl.d", "/lib/sysctl.d"):
        for path in sorted(Path(folder).glob("*.conf")):
            for line in path.read_text().splitlines():
                match = re.match(r"\s*-?kernel[./](unprivileged_userns_clone|apparmor_restrict_unprivileged_userns)\s*=\s*([^#;\s]+)", line)
                if match:
                    assignments.append({"file": str(path), "key": "kernel." + match[1], "value": match[2]})
    for line in Path("/etc/sysctl.conf").read_text().splitlines():
        match = re.match(r"\s*-?kernel[./](unprivileged_userns_clone|apparmor_restrict_unprivileged_userns)\s*=\s*([^#;\s]+)", line)
        if match:
            assignments.append({"file": "/etc/sysctl.conf", "key": "kernel." + match[1], "value": match[2]})
    sudo = subprocess.run(["sudo", "-n", "true"], capture_output=True, text=True, timeout=10)
    checks = {
        "both_limits_one": all(value == "1" for value in values.values()),
        "approved_hashes_match": hashes == HASHES,
        "targets_absent": not existing,
        "package_record_absent": package.returncode == 1,
        "simulation_only_reviewed_package": single_package,
        "nonroot_dpkg_audit_clean": audit.returncode == 0 and not audit.stdout.strip() and not audit.stderr.strip(),
        "explicit_limit_assignments_one": all(row["value"] == "1" for row in assignments),
    }
    print(json.dumps({
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "status": "PREFLIGHT_ONLY" if all(checks.values()) else "REVIEW_REQUIRED",
        "host_mutations": False, "checks": checks, "limits": values,
        "source_hashes": hashes, "existing_targets": existing,
        "package_operations": operations, "sysctl_assignments": assignments,
        "root_auth_available": sudo.returncode == 0,
        "unverified": ["root AppArmor loaded-profile inventory", "root dpkg audit and transaction locks",
                       "installation", "post-install limits", "actual worker isolation and cleanup"],
        "baseline": baseline(),
    }, indent=2))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
