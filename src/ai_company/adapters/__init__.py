"""Adapters expose a lifecycle; they never decide workflow transitions."""

from typing import Literal, Protocol

from ai_company.contracts import AdapterInfo, Request, Result


class Adapter(Protocol):
    info: AdapterInfo

    def healthcheck(self) -> bool: ...

    def start(self, request: Request) -> None:
        """Start without blocking until completion; use request.run_id as identity."""
        ...

    def status(self, run_id: str) -> Literal["running", "completed", "unknown"]: ...

    def cancel(self, run_id: str) -> bool:
        """True means execution termination has been confirmed."""
        ...

    def collect(self, run_id: str) -> Result: ...
