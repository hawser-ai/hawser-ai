"""Model adapter declarations (blueprint §9). Registry file and selector arrive in Phases 1/3."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from hawser.models.base import HawserModel


class Modality(StrEnum):
    text = "text"
    image = "image"
    document = "document"  # native PDF input
    audio = "audio"
    video = "video"


class Pricing(HawserModel):
    """USD per million tokens unless stated otherwise."""

    input_per_mtok: float = Field(ge=0)
    output_per_mtok: float = Field(ge=0)
    cache_read_per_mtok: float | None = Field(default=None, ge=0)
    cache_write_per_mtok: float | None = Field(default=None, ge=0)
    version: str


class DataHandling(HawserModel):
    hosted: bool = True
    regions: list[str] = []
    retains_data: bool = False
    trains_on_data: bool = False


class RetryBehaviour(HawserModel):
    max_attempts: int = Field(default=3, ge=1)
    base_delay_ms: int = Field(default=500, ge=0)
    max_delay_ms: int = Field(default=20_000, ge=0)
    timeout_ms: int = Field(default=120_000, ge=0)


class ModelSpec(HawserModel):
    #: Registry id, ``<provider>/<model>``; e.g. ``anthropic/claude-sonnet-5-5`` or
    #: ``openrouter/anthropic/claude-sonnet-5.5`` for routed providers.
    id: str = Field(pattern=r"^[a-z0-9-]+/[A-Za-z0-9._:/-]+$")
    provider: str
    model: str
    display_name: str | None = None
    modalities: list[Modality] = [Modality.text]
    context_window: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    structured_output: bool = False
    tool_calling: bool = False
    streaming: bool = False
    data_handling: DataHandling = DataHandling()
    pricing: Pricing | None = None  # None for local models (cost accounted as 0 or via compute)
    retry: RetryBehaviour = RetryBehaviour()
    #: Known constraints, e.g. ``no_additional_properties_false``, ``max_images_per_request=20``.
    constraints: list[str] = []
    #: Prior quality score used for ranking before skill-specific eval data exists (0..1).
    quality_prior: float = Field(default=0.5, ge=0, le=1)
    #: Prior for relative speed (1 = fastest tier). Used by the ``fastest`` strategy.
    speed_prior: float = Field(default=0.5, ge=0, le=1)
    enabled: bool = True


__all__ = ["DataHandling", "Modality", "ModelSpec", "Pricing", "RetryBehaviour"]
