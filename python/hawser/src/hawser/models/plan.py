"""Plan IR (blueprint §10.1). Reserved in Phase 0; node types are fully specified in Phase 4.

Only the envelope is fixed now so that ``RunResult.plan_id`` and events can reference plans.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from hawser.clock import UtcDatetime
from hawser.ids import PlanId, RunId
from hawser.models.base import HawserModel
from hawser.models.policy import ExecutionMode


class PlanEnvelope(HawserModel):
    id: PlanId
    run_id: RunId
    mode: ExecutionMode
    goal: str | None = None
    #: Phase 4 replaces ``Any`` with the typed node union.
    nodes: list[Any] = []
    edges: list[tuple[str, str]] = []
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    #: ``cached`` when reused from the plan cache; ``skill`` when derived from a workflow skill.
    source: str = Field(default="planner", pattern=r"^(planner|skill|cached|direct)$")
    created_at: UtcDatetime


__all__ = ["PlanEnvelope"]
