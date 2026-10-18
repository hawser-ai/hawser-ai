"""The ``Runtime`` protocol: what the SDK talks to — local (Phase 1) or a server (Phase 5)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol

from hawser.models import Event, RunRequest, RunResult, RunSummary


class Runtime(Protocol):
    def submit(self, request: RunRequest) -> RunHandle: ...
    def get(self, run_id: str) -> RunResult | None: ...
    def events(self, run_id: str, *, after_seq: int = 0) -> Iterator[Event]: ...
    def list(self, *, limit: int = 50) -> list[RunSummary]: ...
    def cancel(self, run_id: str) -> bool: ...


class RunHandle:
    def __init__(self, runtime: Runtime, run_id: str) -> None:
        self.runtime = runtime
        self.run_id = run_id

    def result(self) -> RunResult:
        result = self.runtime.get(self.run_id)
        if result is None:
            raise RuntimeError(f"run {self.run_id} has no result yet")
        return result

    def events(self, *, after_seq: int = 0) -> Iterator[Event]:
        return self.runtime.events(self.run_id, after_seq=after_seq)


__all__ = ["RunHandle", "Runtime"]
