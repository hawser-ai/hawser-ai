"""Base class and shared config for all contract models (ADR-0004)."""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict

#: Semantic version of the public contract. Bumped per ADR when schemas change incompatibly.
CONTRACT_VERSION: Final = "0.1.0"


class HawserModel(BaseModel):
    """Contract model: strict about unknown fields, validates on assignment, serialises by alias."""

    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        populate_by_name=True,
        use_enum_values=False,
        ser_json_bytes="base64",
    )


__all__ = ["CONTRACT_VERSION", "HawserModel"]
