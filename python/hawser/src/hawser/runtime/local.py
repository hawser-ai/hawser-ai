"""The in-process local runtime (ADR-0002, ADR-0010): the engine, run synchronously.

Development-grade by design: SQLite, local files, one process. The server (``hawser-server``)
runs the same :class:`~hawser.runtime.engine.Engine` behind Postgres, S3 and a leased worker.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from hawser.config import HawserConfig
from hawser.gateway import Gateway, ModelAdapter
from hawser.gateway.defaults import build_gateway, default_adapters
from hawser.models import Event, RunRequest, RunResult, RunSummary, Step
from hawser.review import ReviewService
from hawser.review.approvals import ApprovalService
from hawser.runtime.base import RunHandle
from hawser.runtime.engine import Engine
from hawser.skills.registry import SkillRegistry
from hawser.storage import LocalArtifactStore, SQLiteMetadataStore


class LocalRuntime:
    def __init__(
        self,
        config: HawserConfig | None = None,
        *,
        adapters: dict[str, ModelAdapter] | None = None,
        gateway: Gateway | None = None,
    ) -> None:
        self.config = config or HawserConfig.load()
        home = Path(self.config.home)
        home.mkdir(parents=True, exist_ok=True)
        self.meta = SQLiteMetadataStore(home / "hawser.db")
        self.store = LocalArtifactStore(home / "store")
        self.skills = SkillRegistry(list(self.config.skill_paths))
        self.gateway = (
            gateway if gateway is not None else build_gateway(self.config, adapters=adapters)
        )
        self.engine = Engine(
            self.config,
            meta=self.meta,
            store=self.store,
            gateway=self.gateway,
            skills=self.skills,
        )
        tenant = self.config.tenant_id
        self.artifacts = self.engine.artifacts_for(tenant)
        self.reviews = ReviewService(self.meta, tenant)
        self.approvals = ApprovalService(self.meta, tenant)

    # -- Runtime protocol ------------------------------------------------------------------
    def submit(self, request: RunRequest) -> RunHandle:
        run, created = self.engine.create_run(request, tenant_id=self.config.tenant_id)
        if created:
            self.engine.execute(run)
        return RunHandle(self, run.id)

    def get(self, run_id: str) -> RunResult | None:
        return self.engine.view(run_id)

    def wait(self, run_id: str, *, timeout: float | None = None) -> RunResult:
        # Execution is synchronous: by the time a caller holds a run id, the run has rested.
        result = self.get(run_id)
        if result is None:
            raise RuntimeError(f"run {run_id} has no result yet")
        return result

    def events(self, run_id: str, *, after_seq: int = 0) -> Iterator[Event]:
        yield from self.meta.list_events(run_id, after_seq=after_seq)

    def steps(self, run_id: str) -> list[Step]:
        return self.meta.list_steps(run_id)

    def list(self, *, limit: int = 50) -> list[RunSummary]:
        return self.engine.summaries(self.config.tenant_id, limit=limit)

    def resume(self, run_id: str) -> RunHandle:
        """Re-execute a run paused on ``awaiting_approval``; done steps replay from checkpoints."""
        run = self.engine.begin_resume(run_id)
        self.engine.execute(run, resuming=True)
        return RunHandle(self, run.id)

    def cancel(self, run_id: str) -> bool:
        # Synchronous execution: by the time a caller holds a run id, the run is finished.
        return False

    def close(self) -> None:
        self.meta.close()


__all__ = ["LocalRuntime", "default_adapters"]
