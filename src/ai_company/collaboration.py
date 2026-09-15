"""Read-only collaboration and document projections over persisted execution facts.

The projection never submits work or interprets a diagram selection as authority.
Historical events describe deliveries; current task rows alone describe ownership.
"""
import copy
import json
import math
import re

from ai_company.contracts import digest


def _document(project_id, identity, kind, version, author, reference, fields, protected=None):
    value = {"id": identity, "kind": kind, "project_id": project_id, "source_version": version,
             "author_role": author, "source_ref": reference, "fields": fields, "protected": protected or {}}
    value["source_digest"] = digest(value)
    text = "\n".join(fields.values())
    korean = bool(re.search(r"[가-힣]", text))
    latin = bool(re.search(r"[A-Za-z]{3,}", text))
    other_script = any(c.isalpha() and not c.isascii() and not ("가" <= c <= "힣") for c in text)
    # This is an annotation, never a language-identification model claim.
    value["original_language"] = "mixed" if korean and (latin or other_script) else "ko" if korean else "und"
    return value


def document_sources(overview):
    """Canonical source documents. Structured authority fields are never translated."""
    project_id = overview["project"]["id"]
    documents = []
    for item in overview.get("approvals", []):
        protected = {key: item.get(key) for key in ("subject_digest", "artifact_sha", "environment", "cost_usd", "expires_at", "verification")}
        documents.append(_document(project_id, "approval:" + item["id"], "approval", item["subject_digest"],
            item.get("source", "system"), {"approval_id": item["id"], "subject_digest": item["subject_digest"]},
            {key: str(item.get(key, "")) for key in ("title", "action", "impact", "rollback")}, protected))
    for item in overview.get("reports", []):
        fields = {key: str(item.get(key, "")) for key in ("title", "summary")}
        documents.append(_document(project_id, "report:" + item["id"], "report", digest(fields),
            item.get("source", "system"), {"report_id": item["id"], "evidence": item.get("evidence", [])}, fields))
        for stage, review in (item.get("review_reports") or {}).items():
            if not isinstance(review, dict):
                continue
            fields = {"summary": str(review.get("summary", ""))}
            for index, finding in enumerate(review.get("findings") or []):
                for key in ("detail", "evidence"):
                    fields[f"finding:{index}:{key}"] = str(finding.get(key, ""))
            documents.append(_document(project_id, f"review:{item['id']}:{stage}", "review", digest(review), stage,
                {"report_id": item["id"], "execution_id": review.get("execution_id")}, fields,
                {key: review.get(key) for key in ("verdict", "candidate_sha", "execution_id", "resolved_findings")}))
    for item in overview.get("messages", []):
        if item.get("role") != "assistant":
            continue
        documents.append(_document(project_id, "message:" + item["id"], "message", digest(item.get("content", "")),
            "pm", {"message_id": item["id"], "plan_id": item.get("plan_id")}, {"content": item.get("content", "")}))
    for task in overview.get("tasks", []):
        if task.get("wait_reason"):
            documents.append(_document(project_id, "task:" + task["id"] + ":reason", "reason", digest(task["wait_reason"]),
                "system", {"task_id": task["id"]}, {"reason": task["wait_reason"]}))
    project = overview["project"]
    documents.append(_document(project_id, "project:" + project_id, "project", digest(project.get("goal", "")),
        "master", {"project_id": project_id}, {"goal": project.get("goal", "")}))
    for plan in overview.get("plans", []):
        content = plan.get("content", {})
        fields = {"summary": content.get("summary", "")}
        for index, role in enumerate(content.get("roles", [])):
            fields[f"role:{index}:name"] = role.get("name", "")
        documents.append(_document(project_id, "plan:" + plan["id"], "plan", plan["digest"], "pm",
            {"plan_id": plan["id"], "plan_digest": plan["digest"]}, fields,
            {"plan_digest": plan["digest"], "content": content}))
    return documents


def _tables(db):
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _job(db, execution):
    if not execution or not execution.get("job_id"):
        return None
    row = db.execute("SELECT document FROM session_jobs WHERE job_id=?", (execution["job_id"],)).fetchone()
    return json.loads(row[0]) if row else None


def observed_configuration(db, job):
    unknown = {"status": "unavailable", "model": None, "reasoning_effort": None,
               "source": None, "scope": None, "backend_model_verified": False, "evidence": None}
    if not job:
        return unknown
    result = job.get("result") or {}
    evidence = result.get("configuration_evidence") or {}
    # A model's structured text is deliberately not consulted here.
    if (evidence.get("source") == "codex_rollout" and evidence.get("scope") == "cli_turn_configuration"
            and evidence.get("status") == "observed" and evidence.get("cli_version") == "0.154.0"
            and evidence.get("backend_model_verified") is False and evidence.get("session_id") == job.get("session_id")
            and re.fullmatch(r"[0-9a-f-]{36}", str(job.get("session_id")))):
        contexts = evidence.get("contexts")
        start = db.execute("""SELECT occurred_at FROM session_events WHERE job_id=?
            AND json_extract(document,'$.attempt_count')=? AND json_extract(document,'$.status')='RUNNING'
            ORDER BY sequence LIMIT 1""", (job["job_id"], job.get("attempt_count"))).fetchone()
        if (start and isinstance(contexts, list) and 1 <= len(contexts) <= 128
                and all(isinstance(c, dict) and isinstance(c.get("recorded_at"), (int, float))
                        and not isinstance(c["recorded_at"], bool) and math.isfinite(c["recorded_at"])
                        and start[0] <= c["recorded_at"] <= job.get("updated_at", 0)
                        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}", str(c.get("turn_id")))
                        and isinstance(c.get("model"), str) and isinstance(c.get("reasoning_effort"), str) for c in contexts)):
            models = {c["model"] for c in contexts}
            efforts = {c["reasoning_effort"] for c in contexts}
            if len(models) == len(efforts) == 1:
                return {**unknown, "status": "observed", "model": next(iter(models)), "reasoning_effort": next(iter(efforts)),
                        "source": evidence["source"], "scope": evidence["scope"], "evidence": copy.deepcopy(evidence)}
    # CLI runtime observations can confirm a model without confirming its effort.
    if job.get("provider") == "claude" and result.get("observed_models"):
        models, efforts = result["observed_models"], result.get("observed_efforts") or []
        if isinstance(models, list) and len(models) == 1 and isinstance(models[0], str):
            return {**unknown, "status": "observed", "model": models[0],
                    "reasoning_effort": efforts[0] if len(efforts) == 1 else None,
                    "source": "claude_cli_runtime", "scope": "runtime_metadata",
                    "evidence": {"job_id": job["job_id"], "session_id": job.get("session_id"),
                                 "observed_models": models, "observed_efforts": efforts}}
    return unknown


def _execution(state, stage=None):
    active = state.get("active") or {}
    if active and (stage is None or active.get("role") == stage):
        return active
    completed = [e for e in state.get("executions", []) if stage is None or e.get("role") == stage]
    return max(completed, key=lambda e: e.get("generation", 0), default={})


def assignment(db, state, harness_version, stage=None):
    execution = _execution(state, stage)
    profile = next((a for a in state.get("specification", {}).get("agents", []) if a["agent_id"] == execution.get("agent_id")), {})
    job = _job(db, execution)
    quota = {"status": "unobserved", "reset_at": None}
    if profile.get("quota_group") and "quota_groups" in _tables(db):
        # Read the scheduler's existing quota record; never invent remaining capacity.
        row = db.execute("SELECT state,resume_at FROM quota_groups WHERE group_id=?", (profile["quota_group"],)).fetchone()
        if row:
            quota = {"status": row[0], "reset_at": row[1]}
    return {"agent_id": execution.get("agent_id"), "provider": profile.get("provider"),
            "session_id": (job or {}).get("session_id") or execution.get("session_id"),
            "execution_id": execution.get("execution_id"), "generation": execution.get("generation"),
            "requested": {key: profile.get(key) for key in ("model", "reasoning_effort", "ultracode_enabled")},
            "observed": observed_configuration(db, job), "quota_group": profile.get("quota_group"),
            "quota": quota, "harness_version": harness_version}


def project_collaboration(db, overview):
    """Use task/event IDs as identities; no guessed links from similar display names."""
    project = overview["project"]
    tables = _tables(db)
    events = [(row[0], json.loads(row[1])) for row in db.execute(
        "SELECT id,document FROM management_events WHERE project_id=? ORDER BY id", (project["id"],))]
    cursor = events[-1][0] if events else 0
    linked_cursors, flow_cursors = {}, {}
    for sequence, event in events:
        if event.get("kind") == "task_linked":
            linked_cursors[event["subject_id"]] = sequence
        if event.get("kind") == "flow_updated":
            flow_cursors[(event["subject_id"], event.get("created_at"))] = sequence
    states, task_nodes, run_for_task = {}, {}, {}
    task_summaries = {task["id"]: task for task in overview.get("tasks", [])}
    for task in overview.get("tasks", []):
        row = db.execute("SELECT document FROM flow_tasks WHERE task_id=?", (task["id"],)).fetchone()
        if row:
            states[task["id"]] = json.loads(row[0])
    nodes = []
    for role in overview.get("roles", []):
        own = [t for t in overview.get("tasks", []) if t["role_id"] == role["id"] and t.get("execution_scope") != "integration"]
        # Insertion order can contain a late historical link. Plan revision wins.
        own.sort(key=lambda t: (t.get("revision", 0), t.get("created_at", 0)))
        current = own[-1] if own else None
        state = states.get((current or {}).get("id"), {})
        run_role = next((run.get("roles", {}).get(key, {}) for run in reversed(overview.get("runs", []))
                         for key, identity in run.get("role_ids", {}).items() if identity == role["id"]), {})
        nodes.append({"id": role["id"], "name": role["name"], "kind": "role", "responsibility": role.get("responsibility", ""),
                      "status": (current or {}).get("status", run_role.get("status", "IDLE")), "current_task_id": (current or {}).get("id"),
                      "task_ids": [t["id"] for t in own], "assignment": assignment(db, state, project["harness_version"]),
                      "wait_reason": (current or {}).get("wait_reason"), "resume_at": (current or {}).get("resume_at"),
                      "handoffs": (current or {}).get("handoffs", []), "active": role.get("active", True)})
        for task in own:
            task_nodes[task["id"]] = role["id"]
    pm_id = "pm:" + project["id"]
    requests = overview.get("pm_requests", [])
    request = requests[-1] if requests else {}
    pm_task = (request.get("execution") or {}).get("task_id")
    if pm_task:
        row = db.execute("SELECT document FROM flow_tasks WHERE task_id=?", (pm_task,)).fetchone()
        if row:
            states[pm_task] = json.loads(row[0])
            task_nodes[pm_task] = pm_id
    nodes.insert(0, {"id": pm_id, "name": "Astra Ultra PM", "kind": "pm", "responsibility": "목표·역할·완료 기준 제안",
                    "status": request.get("state", "IDLE"), "current_task_id": pm_task, "task_ids": [pm_task] if pm_task else [],
                    "assignment": assignment(db, states.get(pm_task, {}), project["harness_version"], "pm"),
                    "wait_reason": request.get("reason"), "resume_at": (request.get("execution") or {}).get("resume_at"),
                    "handoffs": states.get(pm_task, {}).get("executions", []), "active": True})
    # Integration review roles are separate from their contributing author role.
    for run in overview.get("runs", []):
        for item in run.get("roles", {}).values():
            if item.get("task_id"):
                run_for_task[item["task_id"]] = run
        for item in [*run.get("role_history", []), *run.get("integration_history", []), run.get("integration") or {}]:
            if item.get("task_id"):
                run_for_task[item["task_id"]] = run
        integration = run.get("integration") or {}
        state = states.get(integration.get("task_id"), {})
        if not state:
            continue
        for stage, name in [("check", "검사"), ("reviewer", "독립 검수"), ("final", "Astra 최종 검수")]:
            execution = _execution(state, stage)
            job = _job(db, execution)
            status = ((job or {}).get("status") or state["status"]) if state.get("stage") == stage else "COMPLETE" if execution else "WAITING_DEPENDENCY"
            if stage == "check" and state.get("stage") != "check":
                # Checks run in the verifier, without a model execution record.
                # Only candidate-bound persisted evidence establishes completion.
                verified = state.get("verification") or {}
                candidate = state.get("snapshot", {}).get("head_commit")
                if candidate and verified.get("head_sha") == candidate:
                    if verified.get("passed") is True and verified.get("remote") is not None:
                        status = "COMPLETE"
                    elif verified.get("passed") is False:
                        status = "FAILED"
            node_id = stage + ":" + run["id"]
            nodes.append({"id": node_id, "name": name, "kind": stage, "responsibility": name,
                          "status": status, "current_task_id": integration["task_id"], "task_ids": [integration["task_id"]],
                          "assignment": assignment(db, state, run["harness_version"], stage),
                          "wait_reason": state.get("reason") if state.get("stage") == stage else None,
                          "resume_at": state.get("resume_at") if state.get("stage") == stage else None,
                          "handoffs": [e for e in state.get("executions", []) if e.get("role") == stage],
                          "active": run.get("state") not in ("blocked", "completed", "rejected")})
    transfers = {}

    def add(kind, sender, receiver, task_id, source, sequence, at, *, execution=None, reason=None, artifacts=None, caused_by=None, terminal_status=None):
        if not sender or not receiver:
            return
        state = states.get(task_id, {})
        explicit_execution = execution
        execution = execution or _execution(state)
        job = _job(db, execution)
        status = terminal_status or (job or {}).get("status") or state.get("status", "READY")
        phase = ("failed" if status in ("FAILED", "BLOCKED", "STOPPED") else "waiting" if status.startswith("WAIT") else
                 "completed" if status in ("COMPLETE", "COMPLETED", "CONTRIBUTION_READY", "MERGE_READY", "DEMO_READY", "PLAN_READY") else
                 "started" if status in ("RUNNING", "ACTIVE") else "received")
        identity = digest([kind, sender, receiver, task_id, source, (explicit_execution or {}).get("execution_id"), caused_by])
        refs = artifacts or []
        started = db.execute("SELECT MIN(occurred_at) FROM session_events WHERE job_id=? AND json_extract(document,'$.status')='RUNNING'",
                             ((job or {}).get("job_id"),)).fetchone()[0] if job else None
        transfers[identity] = {"id": identity, "cursor": sequence, "kind": kind, "from": sender, "to": receiver,
            "task_id": task_id, "run_id": run_for_task.get(task_id, {}).get("id"),
            "title": task_summaries.get(task_id, {}).get("title", state.get("specification", {}).get("task", {}).get("goal", kind)),
            "reason": reason or state.get("reason", ""), "status": phase, "created_at": at,
            "started_at": started, "completed_at": (at if terminal_status else (job or {}).get("updated_at")) if phase == "completed" else None,
            "artifact_refs": refs, "caused_by": caused_by, "execution_id": execution.get("execution_id"),
            "generation": execution.get("generation"), "source": source,
            "mode": run_for_task.get(task_id, {}).get("mode", "recorded")}

    for task_id, state in states.items():
        spec = state.get("specification", {})
        scope = spec.get("execution_scope")
        plan = spec.get("plan", {})
        role_id = task_nodes.get(task_id)
        run = run_for_task.get(task_id, {})
        if scope == "contribution" and role_id:
            add("specification", pm_id, role_id, task_id, {"table": "management_links", "id": task_id},
                linked_cursors.get(task_id, 0), state.get("created_at"),
                reason="확정한 계획의 역할·허용 경로·완료 기준 전달", artifacts=[{"label": "계획 해시", "sha": plan.get("plan_digest")}])
            for key, artifact in (plan.get("dependency_artifacts") or {}).items():
                sender = run.get("role_ids", {}).get(key)
                add("dependency", sender, role_id, task_id, {"table": "flow_tasks", "id": task_id},
                    linked_cursors.get(task_id, 0), state.get("created_at"), reason="선행 역할의 후보를 작업 기준에 통합",
                    artifacts=[{"label": key, "sha": artifact.get("candidate_sha") or artifact.get("head_commit"), "path": artifact.get("clone")}])
            if plan.get("repair_findings") and run.get("id"):
                add("revision_return", "reviewer:" + run["id"], role_id, task_id,
                    {"table": "flow_tasks", "id": task_id}, linked_cursors.get(task_id, 0), state.get("created_at"),
                    reason="\n".join(f.get("detail", "") for f in plan["repair_findings"]), caused_by=plan.get("revision"))
        if "flow_events" not in tables:
            continue
        for row in db.execute("SELECT id,kind,at,document FROM flow_events WHERE task_id=? ORDER BY id", (task_id,)):
            event_id, kind, at, payload = row
            event = json.loads(payload)
            sequence = flow_cursors.get((task_id, event.get("updated_at")), linked_cursors.get(task_id, 0))
            source = {"table": "flow_events", "id": event_id}
            active = event.get("active") or {}
            stage = active.get("role") or event.get("stage")
            target = stage + ":" + run["id"] if scope == "integration" and stage in ("check", "reviewer", "final") and run.get("id") else role_id
            if kind == "ownership_transferred":
                add("handoff", target, target, task_id, source, sequence, at, execution=active,
                    reason="같은 역할·작업의 담당 실행 이관", caused_by=active.get("previous_execution_id"),
                    artifacts=[{"label": "인계 체크포인트", "sha": digest(active.get("handoff") or {})}])
            elif kind == "role_assigned" and scope == "integration" and stage in ("reviewer", "final") and run.get("id"):
                sender = ("check:" if stage == "reviewer" else "reviewer:") + run["id"]
                add("review_request", sender, target, task_id, source, sequence, at, execution=active,
                    reason="같은 후보 커밋의 독립 검수 요청", artifacts=[{"label": "후보 커밋", "sha": event.get("snapshot", {}).get("head_commit")}])
            elif kind == "graph_advanced" and event.get("status") == "CONTRIBUTION_READY":
                add("result_report", role_id, pm_id, task_id, source, sequence, at, execution=_execution(event),
                    reason="역할 산출물 커밋 완료", artifacts=[{"label": "역할 후보 커밋", "sha": event.get("snapshot", {}).get("head_commit")}],
                    terminal_status=event["status"])
            elif kind in ("submitted", "graph_advanced") and event.get("stage") == "check" and scope == "integration" and run.get("id"):
                artifacts = plan.get("contribution_artifacts") or {}
                if not artifacts and run.get("revision", 0) == 0 and (run.get("integration") or {}).get("task_id") == task_id:
                    # Legacy first integrations retain the contributing task and
                    # author-session bindings even before explicit artifact maps.
                    inherited = spec.get("inherited_authors", [])
                    for key, contribution in run.get("roles", {}).items():
                        contributor = states.get(contribution.get("task_id"), {})
                        authors = contributor.get("authors", [])
                        if authors and all(author in inherited for author in authors) and contribution.get("candidate_sha"):
                            artifacts[key] = {"candidate_sha": contribution["candidate_sha"]}
                for key, artifact in artifacts.items():
                    add("result_report", run.get("role_ids", {}).get(key), "check:" + run["id"], task_id,
                        source, sequence, at, reason="역할 후보를 합쳐 같은 커밋의 검사 시작",
                        artifacts=[{"label": key, "sha": artifact.get("candidate_sha") or artifact.get("head_commit")}])
    for sequence, event in events:
        if event.get("kind") != "translation_requested":
            continue
        document = overview.get("documents", {}).get(event.get("document_id"))
        if not document or document["source_digest"] != event.get("source_digest"):
            continue
        translated = document.get("translation")
        if not translated or translated["id"] != event["subject_id"]:
            continue
        sender = "documents:" + project["id"]
        if not any(node["id"] == sender for node in nodes):
            nodes.append({"id": sender, "name": "보고·승인 문서", "kind": "document", "responsibility": "저장된 원문과 실행 근거",
                          "status": "AVAILABLE", "current_task_id": None, "task_ids": [], "assignment": {},
                          "wait_reason": None, "resume_at": None, "handoffs": [], "active": True})
        phase = {"pending": "received", "running": "started", "completed": "completed", "not_required": "completed",
                 "failed": "failed"}.get(translated["status"], "waiting")
        identity = "translation:" + translated["id"]
        transfers[identity] = {"id": identity, "cursor": sequence, "kind": "translation_request", "from": sender,
            "to": "translator:" + project["id"], "task_id": document["id"], "run_id": None,
            "title": document["fields"].get("title", document["id"]), "reason": translated.get("reason") or "원문에 연결된 번역 전용 큐",
            "status": phase, "created_at": translated.get("created_at", event.get("created_at")),
            "started_at": translated.get("started_at"), "completed_at": translated.get("completed_at"),
            "artifact_refs": [{"label": "원문 digest", "sha": document["source_digest"]}], "caused_by": document["source_version"],
            "execution_id": None, "generation": None, "source": {"table": "management_events", "id": sequence}, "mode": "recorded"}
    return {"cursor": cursor, "source": "fixture" if project.get("source") == "fixture" else "persisted_records",
            "nodes": nodes, "transfers": sorted(transfers.values(), key=lambda t: (t["cursor"], t.get("created_at") or 0, t["id"]))}
