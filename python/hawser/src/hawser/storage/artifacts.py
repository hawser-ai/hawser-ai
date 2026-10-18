"""Binary artifact storage (blueprint §19). Content-addressed by tenant-scoped hash."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Protocol

from hawser.hashing import hex_of


def artifact_key(tenant_id: str, scoped_hash: str) -> str:
    """Storage key: ``<tenant>/<hh>/<hex>`` — stable across local and S3 backends."""
    h = hex_of(scoped_hash)
    return f"{tenant_id}/{h[:2]}/{h}"


class ArtifactStore(Protocol):
    def put_bytes(self, key: str, data: bytes) -> None: ...
    def get_bytes(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def url(self, key: str, *, ttl_s: int = 900) -> str: ...


class LocalArtifactStore:
    """Files under ``root``. Atomic writes (temp file + rename); existing keys are not rewritten."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        if ".." in key.split("/"):
            raise ValueError("invalid artifact key")
        return self.root / key

    def put_bytes(self, key: str, data: bytes) -> None:
        path = self._path(key)
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def get_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def url(self, key: str, *, ttl_s: int = 900) -> str:
        return self._path(key).resolve().as_uri()


__all__ = ["ArtifactStore", "LocalArtifactStore", "artifact_key"]
