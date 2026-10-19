"""Server configuration: ``HAWSERD_*`` environment variables over defaults.

The runtime settings the engine needs (default model, record mode, skill paths, limits) come
from the regular ``HAWSER_*`` variables through :class:`hawser.config.HawserConfig`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from hawser.config import HawserConfig

ENV_PREFIX = "HAWSERD_"


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: ``postgresql://user:pass@host:5432/db`` or ``sqlite:///path/to/file.db`` (single node).
    database_url: str = "sqlite:///./.hawserd/hawserd.db"
    #: Local artifact directory; ignored when ``s3_bucket`` is set.
    artifacts_dir: Path = Path("./.hawserd/store")
    s3_bucket: str | None = None
    s3_endpoint_url: str | None = None
    s3_region: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_presign_ttl_s: int = Field(default=900, ge=1, le=7 * 24 * 3600)
    #: Serve artifact bytes through the API instead of presigned URLs.
    artifacts_proxy: bool = True

    host: str = "127.0.0.1"
    port: int = 8080
    #: Base URL clients use to reach this server (in webhook payloads and Location headers).
    public_url: str = "http://127.0.0.1:8080"

    #: Bootstrap one tenant and this API key at start-up. Development and Compose only.
    dev_api_key: str | None = None

    lease_seconds: int = Field(default=60, ge=5)
    worker_concurrency: int = Field(default=2, ge=1)
    worker_max_attempts: int = Field(default=3, ge=1)
    poll_interval_s: float = Field(default=0.5, gt=0)
    max_upload_bytes: int = 50 * 1024 * 1024

    webhook_timeout_s: float = 10.0
    #: Give up on a delivery after this long (dead letter).
    webhook_max_age_s: int = 24 * 3600

    sse_keepalive_s: float = 15.0

    #: Engine configuration (``HAWSER_*``).
    runtime: HawserConfig = Field(default_factory=HawserConfig.load)

    @classmethod
    def load(cls, **overrides: Any) -> ServerConfig:
        values: dict[str, Any] = {}
        for name in cls.model_fields:
            if name == "runtime":
                continue
            env = os.environ.get(f"{ENV_PREFIX}{name.upper()}")
            if env:  # empty means unset (compose passes blanks through)
                values[name] = env
        values.update({k: v for k, v in overrides.items() if v is not None})
        return cls.model_validate(values)

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite:")


__all__ = ["ENV_PREFIX", "ServerConfig"]
