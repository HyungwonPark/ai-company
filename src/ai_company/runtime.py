"""Bounded polling. Real subprocess supervision is a later, separate adapter gate."""

import time
from collections.abc import Callable

from ai_company.adapters import Adapter
from ai_company.contracts import Request, Result


class ExecutionBlocked(RuntimeError):
    pass


class ReconciliationRequired(ExecutionBlocked):
    pass


class Runner:
    def __init__(self, clock: Callable[[], float] = time.monotonic,
                 pause: Callable[[float], None] = time.sleep):
        self.clock, self.pause = clock, pause

    def run(self, adapter: Adapter, request: Request, timeout: float) -> Result:
        deadline = self.clock() + timeout
        try:
            adapter.start(request)
            while self.clock() < deadline:
                status = adapter.status(request.run_id)
                if status == "completed":
                    result = adapter.collect(request.run_id)
                    if self.clock() < deadline:
                        return result
                    break
                if status != "running":
                    raise ReconciliationRequired("adapter cannot confirm execution state")
                self.pause(min(0.05, max(0, deadline - self.clock())))
        except Exception as exc:
            # No automatic retry when start/collect may already have caused side effects.
            if isinstance(exc, ReconciliationRequired):
                raise
            raise ReconciliationRequired(f"adapter lifecycle error: {type(exc).__name__}") from exc
        try:
            confirmed = adapter.cancel(request.run_id)
        except Exception as exc:
            raise ReconciliationRequired("timeout; cancellation could not be confirmed") from exc
        if not confirmed:
            raise ReconciliationRequired("timeout; execution termination is unconfirmed")
        raise ExecutionBlocked("run timeout; termination confirmed")
