"""Everything a request handler or worker needs, built once per process."""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from hawser.gateway import Gateway
from hawser.gateway.defaults import build_gateway
from hawser.runtime.engine import Engine
from hawser.skills.registry import SkillRegistry
from hawser.storage import ArtifactStore, LocalArtifactStore, SqlBackend, SqlMetadataStore
from hawser.storage.schema import apply_migrations
from hawser.tools.registry import ToolRegistry
from hawser.tools.registry import registry as default_registry
from hawser_server.auth import AuthService, Principal
from hawser_server.config import ServerConfig
from hawser_server.db import open_backend, ping
from hawser_server.jobs import JobQueue
from hawser_server.migrations import ALL_MIGRATIONS
from hawser_server.storage import S3ArtifactStore, s3_store_from_config
from hawser_server.webhooks import WebhookService


class PageCache:
    """A small LRU of rendered pages keyed by (asset, page, dpi)."""

    def __init__(self, entries: int) -> None:
        self.entries = entries
        self._data: OrderedDict[tuple[str, int, float], bytes] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: tuple[str, int, float]) -> bytes | None:
        with self._lock:
            if key not in self._data:
                return None
            self._data.move_to_end(key)
            return self._data[key]

    def put(self, key: tuple[str, int, float], value: bytes) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self.entries:
                self._data.popitem(last=False)


class Services:
    def __init__(
        self,
        config: ServerConfig,
        *,
        backend: SqlBackend | None = None,
        store: ArtifactStore | None = None,
        gateway: Gateway | None = None,
        s3_client: Any | None = None,
        tools: ToolRegistry | None = None,
        migrate: bool | None = None,
    ) -> None:
        self.config = config
        self.backend = backend or open_backend(config.database_url)
        if migrate if migrate is not None else config.is_sqlite:
            self.migrate()
        self.meta = SqlMetadataStore(self.backend, migrations=())
        self.s3: S3ArtifactStore | None = None
        object_reader: Callable[[str], bytes] | None = None
        if store is not None:
            self.store = store
        elif config.s3_bucket:
            self.s3 = s3_store_from_config(config, client=s3_client)
            self.store = self.s3
            object_reader = self.s3.read_uri
        else:
            self.store = LocalArtifactStore(Path(config.artifacts_dir))
        self.queue = JobQueue(self.backend, max_attempts=config.worker_max_attempts)
        self.auth = AuthService(self.backend)
        self.webhooks = WebhookService(
            self.backend, timeout_s=config.webhook_timeout_s, max_age_s=config.webhook_max_age_s
        )
        self.skills = SkillRegistry(list(config.runtime.skill_paths))
        self.gateway = gateway or build_gateway(config.runtime)
        self.tools = tools or default_registry
        self.page_cache = PageCache(config.page_cache_entries)
        self.engine = Engine(
            config.runtime,
            meta=self.meta,
            store=self.store,
            gateway=self.gateway,
            skills=self.skills,
            object_reader=object_reader,
        )

    def migrate(self) -> list[str]:
        return apply_migrations(self.backend, ALL_MIGRATIONS)

    def bootstrap(self) -> Principal | None:
        """Apply development bootstrap (``HAWSERD_DEV_API_KEY``) and make sure S3 is usable."""
        if self.s3 is not None:
            self.s3.ensure_bucket()
        if self.config.dev_api_key:
            return self.auth.ensure_dev_key(self.config.dev_api_key)
        return None

    def ready(self) -> bool:
        try:
            return ping(self.backend)
        except Exception:
            return False

    def close(self) -> None:
        self.webhooks.close()
        self.backend.close()


__all__ = ["Services"]
