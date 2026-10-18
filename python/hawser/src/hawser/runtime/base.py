"""The ``Runtime`` protocol: what the SDK talks to — local (in-process) or a server (HTTP)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from hawser.models import Event, RunRequest, RunResult, RunSummary, Step


class Runtime(Protocol):
    def submit(self, request: RunRequest) -> RunHandle: ...
    def get(self, run_id: str) -> RunResult | None: ...
    def wait(self, run_id: str, *, timeout: float | None = None) -> RunResult: ...
    def events(self, run_id: str, *, after_seq: int = 0) -> Iterator[Event]: ...
    def steps(self, run_id: str) -> list[Step]: ...
    def list(self, *, limit: int = 50) -> list[RunSummary]: ...
    def cancel(self, run_id: str) -> bool: ...


class RunHandle:
    def __init__(self, runtime: Runtime, run_id: str) -> None:
        self.runtime = runtime
        self.run_id = run_id

    def result(self, *, timeout: float | None = None) -> RunResult:
        """Block until the run rests (terminal, needs_review or awaiting_approval)."""
        return self.runtime.wait(self.run_id, timeout=timeout)

    def events(self, *, after_seq: int = 0) -> Iterator[Event]:
        return self.runtime.events(self.run_id, after_seq=after_seq)


__all__ = ["RunHandle", "Runtime"]
