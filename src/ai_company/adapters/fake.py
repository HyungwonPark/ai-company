"""Deterministic fixtures. No model, shell, Git, or GitHub calls are made."""

from hashlib import sha1

from ai_company.contracts import AdapterInfo, Check, Finding, Policy, Request, Result, Task, Verification, digest


class FakeAdapter:
    def __init__(self, agent_id: str, role: str, reject_first: int = 0):
        self.info = AdapterInfo(agent_id=agent_id, version="1", roles=(role,),
                                configuration_digest=digest({"reject_first": reject_first}))
        self.reject_first = reject_first
        self.requests: list[Request] = []
        self.results: dict[str, Result] = {}

    def healthcheck(self) -> bool:
        return True

    def start(self, request: Request) -> None:
        self.requests.append(request)
        context = request.context
        values = dict(run_id=request.run_id, context_digest=digest(context), role=context.role,
                      commit_sha=context.input_sha, evidence=("simulation fixture; no remote evidence",))
        if context.role == "developer":
            values["commit_sha"] = sha1(f"{digest(context.task)}:{context.attempt}".encode()).hexdigest()
        elif context.attempt < self.reject_first:
            values["verdict"] = "REVISE"
            values["findings"] = (Finding(finding_id="R1", detail="Simulated acceptance criterion failure",
                                          evidence=f"fixture rejection at attempt {context.attempt}"),)
        else:
            values["verdict"] = "PASS"
            values["resolved_findings"] = tuple(f.finding_id for f in context.feedback)
        self.results[request.run_id] = Result(**values)

    def status(self, run_id: str) -> str:
        return "completed" if run_id in self.results else "unknown"

    def cancel(self, run_id: str) -> bool:
        return run_id in self.results

    def collect(self, run_id: str) -> Result:
        return self.results[run_id]


class FakeVerifier:
    """Separate from agent output; its evidence is always labelled simulation."""

    def identity(self) -> dict:
        return {"provider": "fake-verifier", "version": 1}

    def verify(self, task: Task, policy: Policy, candidate: str) -> Verification:
        return Verification(head_sha=candidate, base_sha=task.base_sha, tested_sha=candidate,
                            task_digest=digest(task), policy_digest=digest(policy),
                            checks=tuple(Check(name=name, outcome="success", evidence=f"simulation:{name}")
                                         for name in task.required_checks))

    def observe(self, candidate: str, base_sha: str) -> tuple[str, str]:
        return candidate, base_sha
