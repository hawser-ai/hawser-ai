"""Artifact service: bytes in the ``ArtifactStore``, records in the ``MetadataStore``."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from hawser.clock import now
from hawser.hashing import content_hash, scoped_hash
from hawser.ids import new_id
from hawser.models import Artifact, ArtifactKind, ArtifactMeta, ProducerRef
from hawser.models.common import Transform
from hawser.storage import ArtifactStore, MetadataStore, artifact_key

SYSTEM_PRODUCER = ProducerRef(kind="system", name="hawser.runtime", version="0.1")


class ArtifactService:
    def __init__(self, store: ArtifactStore, meta: MetadataStore, tenant_id: str) -> None:
        self.store = store
        self.meta = meta
        self.tenant_id = tenant_id

    def put(
        self,
        data: bytes,
        *,
        kind: ArtifactKind,
        media_type: str,
        producer: ProducerRef = SYSTEM_PRODUCER,
        parents: list[str] | None = None,
        transform: Transform | None = None,
        media: dict[str, int | float | str | bool] | None = None,
        run_id: str | None = None,
        step_id: str | None = None,
    ) -> Artifact:
        ch = content_hash(data)
        sh = scoped_hash(self.tenant_id, ch)
        self.store.put_bytes(artifact_key(self.tenant_id, sh), data)
        artifact = Artifact(
            id=new_id("art"),
            tenant_id=self.tenant_id,
            run_id=run_id,
            step_id=step_id,
            meta=ArtifactMeta(
                kind=kind,
                media_type=media_type,
                size_bytes=len(data),
                content_hash=ch,
                scoped_hash=sh,
                parents=parents or [],
                producer=producer,
                transform=transform,
                media=media or {},
            ),
            created_at=now(),
        )
        self.meta.put_artifact(artifact)
        return artifact

    def put_json(self, obj: Any, **kwargs: Any) -> Artifact:
        if isinstance(obj, BaseModel):
            data = obj.model_dump_json().encode("utf-8")
        else:
            data = json.dumps(obj, sort_keys=True, default=str).encode("utf-8")
        kwargs.setdefault("media_type", "application/json")
        return self.put(data, **kwargs)

    def get(self, artifact_id: str) -> Artifact:
        artifact = self.meta.get_artifact(artifact_id)
        if artifact is None:
            raise KeyError(artifact_id)
        return artifact

    def get_bytes(self, artifact_id: str) -> bytes:
        artifact = self.get(artifact_id)
        return self.store.get_bytes(artifact_key(artifact.tenant_id, artifact.meta.scoped_hash))

    def get_json(self, artifact_id: str) -> Any:
        return json.loads(self.get_bytes(artifact_id).decode("utf-8"))


__all__ = ["SYSTEM_PRODUCER", "ArtifactService"]
