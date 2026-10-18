"""Skill manifests (blueprint §12). Full loader and bundle tooling arrive in Phase 2."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from hawser.models.base import HawserModel
from hawser.models.common import JsonSchema, SemVer, Slug
from hawser.models.policy import ExecutionMode, Policy


class InputType(StrEnum):
    document = "document"
    image = "image"
    audio = "audio"
    video = "video"
    text = "text"
    json = "json"
    any = "any"


class SkillInput(HawserModel):
    type: InputType
    description: str | None = None
    required: bool = True
    #: Accept a list of inputs under this name.
    multiple: bool = False


class SkillModels(HawserModel):
    allowed: list[str] | None = None
    preferred: list[str] | None = None


class EvidenceRequirements(HawserModel):
    #: JSON pointers (or glob-like ``/line_items/*/total``) that must carry evidence.
    required: list[str] = []
    preferred: list[str] = []


class SkillExample(HawserModel):
    name: str
    inputs: dict[str, str]  # input name -> relative path inside the bundle
    expected: str  # relative path to expected output JSON


class ValidatorConfig(HawserModel):
    """A validator by id with parameters, e.g. ``rules.expressions`` with ``rules``."""

    id: str
    params: dict[str, Any] = Field(default_factory=dict)


class SkillManifest(HawserModel):
    name: Slug
    version: SemVer
    description: str
    inputs: dict[str, SkillInput]
    #: Inline JSON Schema or a bundle-relative path to one.
    output_schema: JsonSchema | str
    mode: ExecutionMode
    models: SkillModels = SkillModels()
    #: Tools the skill needs (names, optionally ``name@version``). The broker enforces these.
    tools: list[str] = []
    validators: list[str | ValidatorConfig] = []
    evidence: EvidenceRequirements = EvidenceRequirements()
    policy: Policy = Policy()
    #: Dataset references for ``hawser eval``.
    evals: list[str] = []
    #: Version constraint on the runtime, e.g. ``>=0.2,<0.3``.
    compatibility: str | None = None
    examples: list[SkillExample] = []
    #: Prompt/planning guidance files relative to the bundle (``prompts/system.md`` ...).
    prompts: dict[str, str] = {}
    #: Instruction used when the skill runs in direct mode (or as the default goal).
    instruction: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "EvidenceRequirements",
    "InputType",
    "SkillExample",
    "SkillInput",
    "SkillManifest",
    "SkillModels",
    "ValidatorConfig",
]
