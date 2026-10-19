"""Runtime configuration.

Precedence: explicit overrides > ``HAWSER_*`` env > ``hawser.toml`` > defaults.
"""

from __future__ import annotations

import os
import tomllib
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Tenant id used by the local runtime. A valid ULID (all zeros) so the id scheme holds.
LOCAL_TENANT: Final = "ten_00000000000000000000000000"

CONFIG_FILENAME: Final = "hawser.toml"


class RecordMode(StrEnum):
    """How the gateway and tool broker interact with cassettes (ADR-0008)."""

    replay = "replay"  # only cassettes; a miss is an error
    record = "record"  # always call live and overwrite the cassette entry
    auto = "auto"  # replay on hit, record on miss
    live = "live"  # never touch cassettes


class HawserConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Where the local runtime keeps its SQLite database and artifact store.
    home: Path = Field(default_factory=lambda: Path.home() / ".hawser")
    tenant_id: str = LOCAL_TENANT
    record_mode: RecordMode = RecordMode.live
    cassette_dir: Path | None = None

    default_model: str = "anthropic/claude-opus-5-5"
    #: Reasoning effort passed to providers that support it. Extraction is routine work.
    default_effort: str | None = "low"
    max_output_tokens: int = 16_000

    render_dpi: int = 150
    #: OCR engine for pages without a text layer and for images: auto | rapidocr | tesseract |
    #: azure-document-intelligence | none.
    ocr_engine: str = "auto"
    ocr_dpi: int = 300
    #: Straighten skewed photos/scans before OCR (the chain keeps evidence on the original).
    deskew: bool = True
    max_bytes: int = 50 * 1024 * 1024
    max_pages: int = 200
    max_pixels: int = 40_000_000
    #: Fraction of a model's context window the context builder may fill with inputs.
    context_fraction: float = 0.6

    #: Keep raw provider payloads in traces. Off by default (redaction, blueprint §20).
    store_raw: bool = False

    #: Extra skill bundle directories (``./skills`` and ``~/.hawser/skills`` are always searched).
    skill_paths: list[Path] = Field(default_factory=list)

    #: When set, the SDK and CLI talk to a ``hawserd`` server instead of the local runtime.
    server_url: str | None = None
    #: API key for the server; prefer the ``HAWSER_API_KEY`` environment variable over the file.
    api_key: str | None = None

    @field_validator("skill_paths", mode="before")
    @classmethod
    def _split_paths(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [p for p in value.split(os.pathsep) if p] or [p for p in value.split(",") if p]
        return value

    @classmethod
    def load(cls, *, cwd: Path | None = None, **overrides: Any) -> HawserConfig:
        """Build a config from ``hawser.toml`` in ``cwd`` (if any), the env and overrides."""
        cwd = cwd or Path.cwd()
        values: dict[str, Any] = {}
        toml_path = cwd / CONFIG_FILENAME
        if toml_path.exists():
            with toml_path.open("rb") as fh:
                data = tomllib.load(fh)
            section = data.get("hawser", {})
            if isinstance(section, dict):
                values.update(section)  # type: ignore[arg-type]
            server = data.get("server", {})
            if isinstance(server, dict):
                if server.get("url"):
                    values["server_url"] = server["url"]
                if server.get("api_key"):
                    values["api_key"] = server["api_key"]
            # A project-local config implies a project-local home unless told otherwise.
            values.setdefault("home", str(cwd / ".hawser"))
        for name in cls.model_fields:
            env = os.environ.get(f"HAWSER_{name.upper()}")
            if env:  # empty means unset (compose passes blanks through)
                values[name] = env
        values.update({k: v for k, v in overrides.items() if v is not None})
        return cls.model_validate(values)


__all__ = ["CONFIG_FILENAME", "LOCAL_TENANT", "HawserConfig", "RecordMode"]
