"""Dataset format: ``dataset.yaml`` + case inputs + expected outputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ScoringConfig(BaseModel):
    #: list pointer pattern -> key field used to align items (e.g. ``/invoice/line_items: sku``)
    list_keys: dict[str, str] = Field(default_factory=dict)
    #: pointer glob patterns excluded from scoring
    ignore: list[str] = Field(default_factory=list)
    numeric_tolerance: float = 0.01


class EvalCase(BaseModel):
    id: str
    inputs: dict[str, str]
    expected: str
    tags: list[str] = Field(default_factory=list)
    expected_status: str | None = None


class Dataset(BaseModel):
    id: str
    version: str
    description: str = ""
    skill: str | None = None
    generator: dict[str, Any] = Field(default_factory=dict)
    scoring: ScoringConfig = ScoringConfig()
    cases: list[EvalCase] = Field(default_factory=list)
    path: Path = Field(default=Path("."), exclude=True)

    @property
    def ref(self) -> str:
        return f"{self.id}/{self.version}"

    def input_path(self, case: EvalCase, name: str) -> Path:
        return self.path / case.inputs[name]

    def expected_data(self, case: EvalCase) -> Any:
        return json.loads((self.path / case.expected).read_text(encoding="utf-8"))

    def select(
        self,
        *,
        tags: list[str] | None = None,
        limit: int | None = None,
        ids: list[str] | None = None,
    ) -> list[EvalCase]:
        cases = self.cases
        if ids:
            cases = [c for c in cases if c.id in ids]
        if tags:
            cases = [c for c in cases if all(t in c.tags for t in tags)]
        if limit is not None:
            cases = cases[:limit]
        return cases


def load_dataset(path: Path | str) -> Dataset:
    p = Path(path)
    manifest = p if p.is_file() else p / "dataset.yaml"
    if not manifest.exists():
        raise FileNotFoundError(f"no dataset.yaml at {p}")
    data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    ds = Dataset.model_validate(data)
    ds.path = manifest.parent
    return ds


__all__ = ["Dataset", "EvalCase", "ScoringConfig", "load_dataset"]
