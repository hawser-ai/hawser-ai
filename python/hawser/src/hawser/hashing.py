"""Content hashing (ADR-0006).

Two hashes exist for every input:

* ``content_hash`` — plain SHA-256 of the bytes. Stored, never exposed across tenants.
* ``scoped_hash`` — SHA-256 over ``tenant_id || NUL || content_hash_bytes``. Used as the
  dedup/artifact key so that one tenant cannot learn whether another tenant holds a file
  by submitting it and observing a cache hit.

Hashes are rendered as ``sha256:<hex>`` so the algorithm travels with the value.
"""

from __future__ import annotations

import hashlib
from typing import IO, Final

ALGORITHM: Final = "sha256"
_PREFIX: Final = f"{ALGORITHM}:"
_CHUNK: Final = 1 << 20


def content_hash(data: bytes | IO[bytes]) -> str:
    """Return ``sha256:<hex>`` of ``data`` (bytes or a binary stream read in chunks)."""
    h = hashlib.sha256()
    if isinstance(data, bytes):
        h.update(data)
    else:
        while chunk := data.read(_CHUNK):
            h.update(chunk)
    return _PREFIX + h.hexdigest()


def scoped_hash(tenant_id: str, hash_value: str) -> str:
    """Derive the tenant-scoped hash from a tenant id and a ``sha256:<hex>`` content hash."""
    if not tenant_id:
        raise ValueError("tenant_id must be non-empty")
    digest = hashlib.sha256()
    digest.update(tenant_id.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(bytes.fromhex(hex_of(hash_value)))
    return _PREFIX + digest.hexdigest()


def hex_of(hash_value: str) -> str:
    """Strip the ``sha256:`` prefix, validating the format."""
    if not hash_value.startswith(_PREFIX):
        raise ValueError(f"unsupported hash format {hash_value!r}; expected '{_PREFIX}<hex>'")
    hexpart = hash_value[len(_PREFIX) :]
    if len(hexpart) != 64 or any(c not in "0123456789abcdef" for c in hexpart):
        raise ValueError(f"malformed sha256 hex in {hash_value!r}")
    return hexpart


__all__ = ["ALGORITHM", "content_hash", "hex_of", "scoped_hash"]
