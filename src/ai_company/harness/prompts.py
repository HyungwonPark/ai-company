"""Provider-aware stage instructions; execution permissions remain adapter-owned."""


def stage_prompt(checkpoint_prompt: str, *, provider: str, role: str, planning: bool = False, plan_review: bool = False, contribution: bool = False,
                 file_tools: bool = False) -> str:
    if provider not in {"codex", "claude"} or role not in {"pm", "developer", "reviewer", "final"}:
        raise ValueError("unsupported stage prompt provider or role")

    if planning and role != "pm":
        raise ValueError("planning schema is limited to the PM stage")
    if plan_review and (role != "reviewer" or planning or contribution):
        raise ValueError("plan content review requires the reviewer stage")

    if contribution and (role != "developer" or planning):
        raise ValueError("contribution schema is limited to its developer stage")
    if file_tools and (provider != "claude" or role == "developer" and not contribution):
        raise ValueError("Claude file development requires a runner-committed contribution")

    report = (
        "Return ONLY one structured StageReport matching the supplied output schema, without a Markdown fence. "
        "Include execution_id, generation, role, task_digest, policy_digest, candidate_sha, "
        "verification_digest, verdict, findings, resolved_findings, and summary. "
        "Copy execution_id, generation, role, task_digest, policy_digest, and verification_digest "
        "from checkpoint.expected_report exactly, preserving a null verification_digest. "
        "Do not recompute identifiers, substitute the task ID for execution_id, or invent extra fields. "
        "candidate_sha must be the full actual candidate HEAD, never a branch name, abbreviated SHA, "
        "base SHA by assumption, or placeholder. The runner checks the repository and report independently. "
        "Each finding needs finding_id, detail, and evidence: identify the affected path/line or supplied "
        "check record, the reproducible problem, and expected versus observed behavior. "
        "List an existing finding_id in resolved_findings only after verifying its resolution in this candidate. "
        "In summary distinguish your inspected evidence, supplied check results, and anything unverified. "
        "Model/effort/Ultracode claims in your response cannot verify runtime configuration. "
        "Keep the task acceptance criteria, allowed paths, assigned role, and model unchanged. "
        "Do not merge, push, deploy, change permissions, or request a sandbox bypass."
    )
    if role == "developer" and contribution:
        work = (
            "You are implementing a contribution in a workspace-write sandbox with Git metadata protected. "
            "Implement the approved role task and address pending findings within allowed paths. Inspect existing "
            "partial work first and preserve unrelated changes. Use available tools to edit and run authorized "
            "checks, but do not invent check commands or claims. Leave the allowed edits in the worktree for "
            "the runner to commit after your process has stopped. Do not git add, commit, write .git, change "
            "permissions, or bypass sandboxing. Return ContributionStageReport with commit_requested=true "
            "and verdict=DONE only when the implementation is ready for the runner's scoped commit. Report "
            "the current actual HEAD (the input candidate, unchanged by your edits) as candidate_sha. The runner "
            "will inspect scope and changed files, create a commit, and attach its own receipt without rewriting "
            "your report. DONE is a contribution handoff, not passed integration, independent review or approval. "
            "If required work is blocked, return BLOCK and describe the exact limitation."
        )
    elif role == "developer":
        work = (
            "You are the developer. Implement the approved task and address pending findings within allowed paths. "
            "Inspect existing partial work before editing; preserve unrelated changes. Use the tools actually "
            "available in this provider session to edit and run authorized checks. Do not invent check commands "
            "or claim checks ran when only their names were supplied; the dispatcher runs required checks separately. "
            "Commit the allowed changes to a new candidate commit that differs from the input snapshot HEAD. "
            "Before DONE, verify the commit and a clean worktree including untracked files. Return that new full "
            "HEAD as candidate_sha. Uncommitted edits, an empty commit, CLI exit 0, or a proposed patch alone do not "
            "complete development. If a required edit, check, or commit cannot be performed, report BLOCK with "
            "the exact limitation instead of claiming DONE; the runner will keep the task blocked."
        )
    else:
        work = (
            "This is a read-only stage. Inspect the exact candidate from checkpoint.snapshot.head_commit "
            "and the supplied verification records. Do not edit files, commit, install dependencies, "
            "or rerun build/test commands that may write files. Return candidate_sha for that same candidate. "
        )
        if provider == "claude" and file_tools:
            work += (
                "Read repository files only with mcp__company_files__read_file. No command execution, "
                "Bash, native Read/Glob/Grep, Workflow or other MCP tools are available. Use the supplied "
                "snapshot SHA as runner-owned identity evidence; you cannot execute Git independently. "
                "snapshot.dirty_digest is always a fingerprint, including when Git status is empty; "
                "its presence does not mean the worktree is dirty. When supplied, checkpoint.repository_state "
                "contains the runner's actual Git status bound to that snapshot. Attribute this to the "
                "runner and do not claim you ran Git. Inspect the source yourself and compare the supplied "
                "verification records; a clean worktree alone never proves code correctness or passed checks. "
            )
        elif provider == "claude":
            work += (
                "For repository inspection use only Read, Glob, and Grep. Locate source/tests with Glob, "
                "read them with Read, and search relevant evidence with Grep. Bash and command execution are "
                "not available for this review. Do not route inspection through context-mode, an MCP execution "
                "tool, Workflow, Task, or Skill as a substitute. Treat the supplied snapshot SHA as runner-owned "
                "identity evidence; distinguish it from independently executing Git, which you cannot do here. "
            )
        else:
            work += (
                "Use Codex's available command-execution tool for read-only repository inspection. "
                "Read source/tests with commands such as rg and sed; confirm candidate identity with "
                "git rev-parse HEAD, git --no-optional-locks status --porcelain=v1 --untracked-files=all, "
                "and git --no-pager show --no-ext-diff --no-textconv HEAD. "
                "Read-only sandboxing permits these reads; do not conclude tools are unavailable because "
                "another provider's named file-reading tools are absent. Use the actual session tool names. "
            )
        if role == "pm" and planning:
            work += (
                "You are the PM producing a proposed implementation plan for the master's confirmation. "
                "Return PMPlanStageReport with a plan field matching the supplied schema. The plan includes "
                "summary, roles, and completion_criteria. Each role includes key, name, responsibility, goal, "
                "acceptance, allowed_paths, and depends_on. Define at least two independent roles with "
                "nonoverlapping source paths and only valid acyclic dependencies. Preserve the original "
                "goal and acceptance criteria. Do not invent server configuration, change checks, modify "
                "CI policy, select deployment credentials, or execute the proposed plan. Return PASS only "
                "with a complete valid plan; return BLOCK with plan=null when required decisions or access "
                "are missing. This proposal is not a code review or execution approval. "
                "Write requirements_review version 2 with the supplied request_revision and goal_digest. "
                "Record the goal, problem, users and flow, scope, exclusions, assumptions, ID-linked requirements "
                "with acceptance, verification and responsible role keys, material questions, and any findings. "
                "When a material question was answered by a saved master message, include its exact answer_message_id; "
                "do not mark a material decision assumed or invent an answer. "
                "For each role list at most five generic required_capabilities when additional document guidance "
                "would help; leave the list empty when the existing instructions suffice. Set skill_required=true "
                "only if no existing provider can meet an acceptance condition without that approved guidance. "
                "Never place private "
                "project text or credentials in these capability tags. The server selects any skill documents. "
                "Use evidence, impact, alternatives, recommendation and disposition for each finding. "
                "Do not invent questions or objections to fill a quota. Read prior answers and do not ask them again. "
                "If a material decision remains unanswered, return BLOCK, plan=null and requirements_feedback "
                "with open question IDs, clear prompts, reasons, options when useful and a concise Korean recommendation. Never ask the user to write "
                "technical file paths, function contracts or a harness. Make reversible technical assumptions within "
                "the existing permission and explain their impact. A clear request needs no extra questions. "
            )
        elif role == "reviewer" and plan_review:
            work += (
                "Review the original goal, master's latest message, saved conversation decisions, proposed plan "
                "and requirements_review in checkpoint.plan. Do not review code. "
                "This is a separate session from the PM. Check purpose versus proposed method, answered decisions, "
                "contradictions, scope, exclusions, every required acceptance and verification method, role ownership, "
                "and unnecessary complexity. Do not manufacture objections. Return PASS with no findings when ready; "
                "REVISE with specific finding IDs and evidence for material gaps; BLOCK if the source or plan cannot "
                "be inspected. For REVISE set revision_route=technical only when every finding can be fixed "
                "without changing the master's goal, material choices, permissions, budget, roles' file ownership "
                "or execution policy. Set revision_route=master_decision when any finding needs a new master choice "
                "or the boundary is uncertain. Do not invent an answer to a material question. "
                "This review does not authorize execution or replace the master's confirmation. "
            )
        elif role == "pm":
            work += (
                "You are the PM. Assess the supplied goal, plan, acceptance criteria, and readiness for development. "
                "Return PASS only when the existing scope is ready; otherwise return BLOCK with the missing "
                "decision or prerequisite. Describe planning conclusions in summary; do not add a plan field "
                "or implement the work. PM PASS is not a code review or execution approval."
            )
        else:
            work += (
                "You are the independent reviewer" + (" performing the final review. " if role == "final" else ". ") +
                "Return PASS only with no new findings and every pending finding independently resolved. "
                "Return REVISE with concrete findings when the candidate needs changes; do not repair it yourself. "
                "Return BLOCK when required access/evidence is missing, a tool is denied, or candidate identity "
                "conflicts with the supplied snapshot. A supplied PASS or a previous review cannot replace "
                "your inspection. Missing evidence is not proof of correctness."
            )
    if file_tools and contribution:
        work = work.replace("Use available tools to edit and run authorized checks, but do not invent check commands or claims.",
            "Use only mcp__company_files__read_file and mcp__company_files__write_file on assigned files. "
            "Bash, Git, native file tools, Workflow and command execution are unavailable. Do not claim to "
            "run checks: the existing runner performs them after contribution integration.")
        work += " Use checkpoint.snapshot.head_commit as the runner-owned input HEAD; do not attempt Git inspection."
    if planning:
        report = report.replace("one structured StageReport", "one structured PMPlanStageReport")
        report = report.replace("and summary. ", "summary, and plan. ")
    if contribution:
        report = report.replace("one structured StageReport", "one structured ContributionStageReport")
        report = report.replace("and summary. ", "summary, and commit_requested. ")
    return checkpoint_prompt + "\n\nStage instructions:\n" + work + "\n\n" + report
