"""Default gateway construction shared by the local runtime and the server worker."""

from __future__ import annotations

from pathlib import Path

from hawser.config import HawserConfig, RecordMode
from hawser.gateway.anthropic import AnthropicAdapter
from hawser.gateway.base import ModelAdapter
from hawser.gateway.gateway import Gateway
from hawser.gateway.openai_compat import OpenAICompatibleAdapter
from hawser.gateway.registry import load_registry
from hawser.recording import Cassette


def default_adapters() -> dict[str, ModelAdapter]:
    """First-party adapters keyed by provider. Credentials are resolved lazily per call."""
    return {
        "anthropic": AnthropicAdapter(),
        "openrouter": OpenAICompatibleAdapter.openrouter(),
        "openai": OpenAICompatibleAdapter.openai(),
        "ollama": OpenAICompatibleAdapter.ollama(),
    }


def build_gateway(
    config: HawserConfig, *, adapters: dict[str, ModelAdapter] | None = None
) -> Gateway:
    """A gateway honouring the config's record mode and cassette directory."""
    cassette: Cassette | None = None
    if config.record_mode != RecordMode.live:
        cassette = Cassette(
            Path(config.cassette_dir or Path(config.home) / "cassettes"), config.record_mode
        )
    return Gateway(
        load_registry(),
        adapters or default_adapters(),
        cassette=cassette,
        store_raw=config.store_raw,
    )


__all__ = ["build_gateway", "default_adapters"]
