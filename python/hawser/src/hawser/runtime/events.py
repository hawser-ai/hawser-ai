"""Event emission for one run."""

from __future__ import annotations

from typing import Any

from hawser.models import Event, EventType
from hawser.storage import MetadataStore


class EventEmitter:
    def __init__(self, meta: MetadataStore, *, run_id: str, tenant_id: str) -> None:
        self.meta = meta
        self.run_id = run_id
        self.tenant_id = tenant_id

    def emit(
        self, type: EventType, payload: dict[str, Any] | None = None, *, step_id: str | None = None
    ) -> Event:
        return self.meta.append_event(
            run_id=self.run_id,
            tenant_id=self.tenant_id,
            type=type,
            payload=payload,
            step_id=step_id,
        )


__all__ = ["EventEmitter"]
