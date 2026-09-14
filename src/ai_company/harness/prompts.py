"""Provider-aware stage instructions; execution permissions remain adapter-owned."""


def stage_prompt(checkpoint_prompt: str, *, provider: str, role: str, planning: bool = False, contribution: bool = False) -> str:
    if provider not in {"codex", "claude"} or role not in {"pm", "developer", "reviewer", "final"}:
        raise ValueError("unsupported stage prompt provider or role")

    if planning and role != "pm":
        raise ValueError("planning schema is limited to the PM stage")

    if contribution and (role != "developer" or planning):
        raise ValueError("contribution schema is limited to its developer stage")

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
        if provider == "claude":
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
    if planning:
        report = report.replace("one structured StageReport", "one structured PMPlanStageReport")
        report = report.replace("and summary. ", "summary, and plan. ")
    if contribution:
        report = report.replace("one structured StageReport", "one structured ContributionStageReport")
        report = report.replace("and summary. ", "summary, and commit_requested. ")
    return checkpoint_prompt + "\n\nStage instructions:\n" + work + "\n\n" + report
