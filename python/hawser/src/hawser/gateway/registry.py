"""Model registry: the declared truth about capabilities and pricing (blueprint §9)."""

from __future__ import annotations

import hashlib
from importlib import resources
from pathlib import Path

import yaml

from hawser.errors import PolicyViolation
from hawser.models import ModelSpec


class Registry:
    def __init__(self, specs: list[ModelSpec], *, version: str) -> None:
        self._by_id = {s.id: s for s in specs}
        self.version = version

    def get(self, model_id: str) -> ModelSpec:
        spec = self._by_id.get(model_id)
        if spec is None:
            raise PolicyViolation(
                f"unknown model {model_id!r}; known: {', '.join(sorted(self._by_id))}",
                code="model_unknown",
                details={"model": model_id},
            )
        if not spec.enabled:
            raise PolicyViolation(f"model {model_id!r} is disabled", code="model_disabled")
        return spec

    def all(self) -> list[ModelSpec]:
        return sorted(self._by_id.values(), key=lambda s: s.id)

    def __contains__(self, model_id: str) -> bool:
        return model_id in self._by_id


def load_registry(path: Path | None = None) -> Registry:
    if path is None:
        text = (
            resources.files("hawser.gateway").joinpath("registry.yaml").read_text(encoding="utf-8")
        )
    else:
        text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    specs = [ModelSpec.model_validate(m) for m in data.get("models", [])]
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    return Registry(specs, version=f"{data.get('version', 'unversioned')}+{digest}")


__all__ = ["Registry", "load_registry"]
