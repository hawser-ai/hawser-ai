"""Shared CLI plumbing: which runtime a command talks to (local store or a server)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hawser.config import HawserConfig
from hawser.runtime.local import LocalRuntime
from hawser.runtime.remote import RemoteRuntime

#: Set by the root callback from ``--server`` / ``--api-key``; overrides config and env.
STATE: dict[str, str | None] = {"server_url": None, "api_key": None}

AnyRuntime = LocalRuntime | RemoteRuntime


def load_config(cwd: Path | None = None, **overrides: Any) -> HawserConfig:
    cfg = HawserConfig.load(cwd=cwd, **overrides)
    if STATE["server_url"]:
        cfg = cfg.model_copy(update={"server_url": STATE["server_url"]})
    if STATE["api_key"]:
        cfg = cfg.model_copy(update={"api_key": STATE["api_key"]})
    return cfg


def runtime(cwd: Path | None = None, *, force_local: bool = False, **overrides: Any) -> AnyRuntime:
    cfg = load_config(cwd, **overrides)
    if cfg.server_url and not force_local:
        if not cfg.api_key:
            raise SystemExit(
                "a server is configured but no API key: set HAWSER_API_KEY or pass --api-key"
            )
        return RemoteRuntime(cfg.server_url, cfg.api_key)
    return LocalRuntime(cfg)


__all__ = ["STATE", "AnyRuntime", "load_config", "runtime"]
