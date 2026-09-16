"""Read-only, run-bound graph snapshots; layout never grants execution authority."""
import json

from ai_company.collaboration import assignment, project_collaboration
from ai_company.contracts import digest


def _assignment(value):
    """Allowlist configuration evidence, excluding CLI paths, accounts and raw output."""
    observed = value.get("observed") or {}
    return {"provider": value.get("provider"), "execution_id": value.get("execution_id"),
            "generation": value.get("generation"), "harness_version": value.get("harness_version"),
            "requested": {k: value.get("requested", {}).get(k) for k in
                          ("model", "reasoning_effort", "ultracode_enabled")},
            "observed": {k: observed.get(k) for k in
                         ("status", "model", "reasoning_effort", "source", "scope", "backend_model_verified")},
            "quota": value.get("quota", {"status": "unobserved", "reset_at": None})}


def _run_tasks(run):
    records = [*run.get("roles", {}).values(), *run.get("role_history", []),
               *run.get("integration_history", []), run.get("integration") or {}]
    return {r["task_id"] for r in records if r.get("task_id")}



def _bound_roles(db, run, task_rows):
    """Recover labels only from this run's fixed task specification.

    Current management role rows can describe a later plan. If the saved plan
    cannot be matched, display an explicit unknown rather than borrow its prose.
    """
    tasks = {task["id"]: task for task in task_rows}
    roles = []
    for key, role_id in run.get("role_ids", {}).items():
        task_id = run.get("roles", {}).get(key, {}).get("task_id")
        task = tasks.get(task_id)
        row = db.execute("SELECT document FROM flow_tasks WHERE task_id=?", (task_id,)).fetchone() if task else None
        context = json.loads(row[0]).get("specification", {}).get("plan", {}) if row else {}
        fixed_role = context.get("role") or {}
        valid = (task and task.get("role_id") == role_id and context.get("plan_digest") == run.get("plan_digest")
                 and context.get("project_id") == run.get("project_id") and fixed_role.get("key") == key)
        roles.append({"id": role_id, "key": key, "active": True,
                      "name": fixed_role.get("name", "역할 확인 불가") if valid else "역할 확인 불가",
                      "responsibility": fixed_role.get("responsibility", "") if valid else "실행에 고정된 역할 원문 확인 필요"})
    return roles


def workspace_graph(db, overview, *, observed_at):
    """Project snapshots use explicit plan/run/task bindings, never label matching.

    A snapshot fingerprint supplements management cursor because session rows can
    change before the coordinator emits a management event. Reads never persist it.
    """
    project = overview["project"]
    pid = project["id"]
    cursor = overview.get("collaboration", {}).get("cursor", 0)
    plans = {p["id"]: p for p in overview.get("plans", []) if p.get("project_id", pid) == pid}
    runs = [r for r in overview.get("runs", []) if r.get("project_id") == pid]
    bindings = [(p, None) for p in plans.values()]
    bindings += [(plans.get(r.get("plan_id")), r) for r in runs]
    if not bindings:
        bindings = [(None, None)]
    snapshots = [_snapshot(db, overview, plan, run, cursor, observed_at) for plan, run in bindings]
    latest_plan = next(reversed(plans.values()), None) if plans else None
    eligible = [s for s in snapshots if not latest_plan or s["plan_id"] == latest_plan["id"]]
    default = eligible[-1] if eligible else snapshots[-1]
    result = {"schema_version": 1, "project_id": pid, "cursor": cursor, "observed_at": observed_at,
              "default_snapshot_id": default["id"], "snapshots": snapshots}
    result["fingerprint"] = digest([s["fingerprint"] for s in snapshots])
    return result


def _snapshot(db, overview, plan, run, cursor, observed_at):
    project = overview["project"]
    pid = project["id"]
    plan_id = (run or {}).get("plan_id") or (plan or {}).get("id")
    plan_digest = (run or {}).get("plan_digest") or (plan or {}).get("digest")
    run_id = (run or {}).get("id")
    fixture = project.get("source") == "fixture" or (run if run else plan or {}).get("mode") in ("fixture", "demo")
    source = "fixture" if fixture else "execution" if run else "planned"
    identity = "graph:" + digest([pid, plan_id, run_id])[:24]
    binding = {"project_id": pid, "plan_id": plan_id, "plan_digest": plan_digest, "run_id": run_id}
    selected_tasks = _run_tasks(run) if run else set()
    task_rows = [t for t in overview.get("tasks", []) if t["id"] in selected_tasks]
    # Filter even corrupt run references through this project's task links.
    selected_tasks = {t["id"] for t in task_rows}
    role_ids = (run or {}).get("role_ids", {})
    plan_matches = not run or (plan and plan.get("digest") == run.get("plan_digest"))
    content_roles = (plan or {}).get("content", {}).get("roles", []) if plan_matches else []
    roles = [{**r, "id": role_ids.get(r["key"], "planned:" + r["key"]), "active": bool(run)} for r in content_roles]
    if run and not roles:
        roles = _bound_roles(db, run, task_rows)
    if not plan and not run and fixture:
        roles = overview.get("roles", [])
        task_rows = overview.get("tasks", [])
    request_id = (plan or {}).get("request_id") if plan_matches else None
    requests = [r for r in overview.get("pm_requests", []) if request_id and r.get("request_id") == request_id]
    # A PM request belongs to its proposal, not to development of every later run.
    scoped = {**overview, "roles": roles, "tasks": task_rows, "plans": [plan] if plan and plan_matches else [],
              "runs": [run] if run else [], "pm_requests": requests, "documents": {}}
    projection = project_collaboration(db, scoped)
    result = {"id": identity, **binding, "source": source, "mode": "execution" if run else "planned", "cursor": cursor, "observed_at": observed_at,
              "nodes": [], "edges": [], "report_refs": [], "approval_refs": [], "warnings": []}
    if run and (not plan or plan.get("digest") != run.get("plan_digest")):
        result["warnings"].append("실행에 고정된 계획 원문을 대조할 수 없습니다.")
        # Never display mismatched plan dependencies as this run's contract.
        content_roles = []
    ids = {n["id"]: identity + ":" + n["id"] for n in projection["nodes"]}
    role_by_id = {role["id"]: role for role in roles}
    for node in projection["nodes"]:
        # The run explicitly names its current contribution; a late history row
        # must not become the owner because it has a larger timestamp/revision.
        if run and node["kind"] == "role":
            key = next((key for key, rid in role_ids.items() if rid == node["id"]), None)
            current_id = run.get("roles", {}).get(key, {}).get("task_id")
            current = next((t for t in task_rows if t["id"] == current_id), None)
            state_row = db.execute("SELECT document FROM flow_tasks WHERE task_id=?", (current_id,)).fetchone() if current else None
            state = json.loads(state_row[0]) if state_row else {}
            node = {**node, "current_task_id": current_id if current else None,
                    "status": (current or {}).get("status", run.get("roles", {}).get(key, {}).get("status", "PLANNED")),
                    "assignment": assignment(db, state, run.get("harness_version")),
                    "wait_reason": (current or {}).get("wait_reason"), "resume_at": (current or {}).get("resume_at"),
                    "handoffs": (current or {}).get("handoffs", [])}
        kind = node["kind"]
        task_id = node.get("current_task_id")
        actual = bool(task_id) or (kind == "pm" and requests)
        row_source = "fixture" if fixture else "execution" if actual else "planned"
        role = role_by_id.get(node["id"], {})
        status = node["status"] if actual or fixture else "PLANNED"
        reference = {"table": "management_roles", "id": node["id"]} if run and kind == "role" else {
            "table": "management_plans", "id": plan_id, "role_key": role.get("key")}
        if kind == "pm":
            reference = {"table": "management_pm_requests", "id": request_id}
        elif task_id:
            reference = {"table": "flow_tasks", "id": task_id, "task_id": task_id, "role_id": node["id"]}
        result["nodes"].append({"id": ids[node["id"]], **binding, "kind": kind, "name": node["name"],
            "responsibility": node.get("responsibility", ""), "status": status,
            "current_task_id": task_id, "task_ids": node.get("task_ids", []),
            "current_task_title": next((t.get("title", "") for t in task_rows if t["id"] == task_id), ""),
            "phase": "recorded" if actual else "planned",
            "assignment": _assignment(node.get("assignment", {})), "wait_reason": node.get("wait_reason"),
            "resume_at": node.get("resume_at"), "source": row_source, "reference": reference,
            "handoffs": [{k: e.get(k) for k in ("execution_id", "previous_execution_id", "generation", "role")}
                         for e in node.get("handoffs", [])]})
    for transfer in projection["transfers"]:
        if transfer.get("run_id") != run_id or (run_id and transfer.get("task_id") not in selected_tasks):
            continue
        if transfer["from"] not in ids or transfer["to"] not in ids:
            result["warnings"].append("일부 전달의 역할 연결 근거가 없어 그림에서 제외했습니다.")
            continue
        result["edges"].append({**{k: transfer.get(k) for k in
            ("kind", "status", "title", "reason", "cursor", "created_at", "started_at", "completed_at", "execution_id", "generation", "task_id")},
            "id": identity + ":" + transfer["id"], **binding, "from": ids[transfer["from"]], "to": ids[transfer["to"]],
            "source": "fixture" if fixture else "execution", "phase": "recorded", "reference": transfer["source"],
            "artifact_refs": [{k: a.get(k) for k in ("label", "sha")} for a in transfer.get("artifact_refs", [])]})
    for role in content_roles:
        to = ids.get(role_ids.get(role["key"], "planned:" + role["key"]))
        pm = ids.get("pm:" + pid)
        if not run and pm and to:
            result["edges"].append({"id": identity + ":specification:" + digest(role["key"])[:20],
                **binding, "from": pm, "to": to, "kind": "planned_specification", "status": "planned",
                "title": "역할 제안", "reason": "PM 계획에 포함된 역할입니다. 실제 작업 배정·전달 기록은 아닙니다.",
                "source": "fixture" if fixture else "planned", "phase": "planned", "cursor": cursor, "artifact_refs": [],
                "reference": {"table": "management_plans", "id": plan_id, "plan_digest": plan_digest,
                              "role_key": role["key"]}})
        for dependency in role.get("depends_on", []):
            sender = ids.get(role_ids.get(dependency, "planned:" + dependency))
            if not sender or not to:
                continue
            result["edges"].append({"id": identity + ":dependency:" + digest([dependency, role["key"]])[:20],
                **binding, "from": sender, "to": to, "kind": "planned_dependency", "status": "planned",
                "title": "계획 의존", "reason": "확정 여부와 실제 전달 이력은 별도로 확인합니다.",
                "source": "fixture" if fixture else "planned", "phase": "planned", "cursor": cursor, "artifact_refs": [],
                "reference": {"table": "management_plans", "id": plan_id, "plan_digest": plan_digest,
                              "from_role_key": dependency, "to_role_key": role["key"]}})
    reports = [r for r in overview.get("reports", []) if r["id"] in {"flow-" + t for t in selected_tasks}]
    approvals = [a for a in overview.get("approvals", []) if run and a["id"] == run.get("approval_id")]
    for kind, items in (("report", reports), ("approval", approvals)):
        for item in items:
            ref = {"id": item["id"], **binding, "title": item.get("title"), "kind": kind,
                   "document_id": kind + ":" + item["id"]}
            if kind == "approval":
                ref.update(subject_digest=item.get("subject_digest"), artifact_sha=item.get("artifact_sha"), status=item.get("status"))
            result[kind + "_refs"].append(ref)
            result["nodes"].append({"id": identity + ":" + kind + ":" + item["id"], **binding,
                "kind": "document", "name": item.get("title", kind), "status": item.get("status", "AVAILABLE"),
                "source": source, "phase": "recorded", "document_kind": kind, "reference": ref, "assignment": {},
                "responsibility": "같은 실행에 연결된 " + ("승인" if kind == "approval" else "보고"),
                "current_task_id": None, "task_ids": [], "handoffs": []})
    result["history"] = {"complete": not result["warnings"], "shown": len(result["edges"]),
                         "total": len(projection["transfers"]) + sum(e["phase"] == "planned" for e in result["edges"]),
                         "recorded": sum(e["phase"] == "recorded" for e in result["edges"]),
                         "planned": sum(e["phase"] == "planned" for e in result["edges"])}
    result["empty_reason"] = "계획을 요청하면 역할과 의존 관계가 표시됩니다." if not plan and not run and not fixture else None
    # Timestamp is response time, not a new fact. Stable fingerprints enable no-op refresh.
    result["fingerprint"] = digest({k: v for k, v in result.items() if k != "observed_at"})
    return result
