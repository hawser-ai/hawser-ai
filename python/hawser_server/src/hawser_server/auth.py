"""API keys and tenants.

Keys are ``hk_live_<40 url-safe chars>``, shown once, stored as an argon2id hash. Lookup is by
a short hash prefix (so any token shape works for ``HAWSERD_DEV_API_KEY``), then a full verify.
Verified tokens are cached in memory for a few minutes to keep argon2 off the request path.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError

from hawser.config import LOCAL_TENANT
from hawser.ids import new_id
from hawser.storage.sql import SqlBackend
from hawser_server.util import ts

KEY_PREFIX = "hk_live_"
_CACHE_TTL_S = 300.0


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    key_id: str


@dataclass(frozen=True)
class Tenant:
    id: str
    name: str
    created_at: str


@dataclass(frozen=True)
class ApiKey:
    id: str
    tenant_id: str
    name: str
    prefix: str
    created_at: str
    revoked_at: str | None
    last_used_at: str | None


def _prefix(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def generate_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(30)[:40]


class AuthService:
    def __init__(self, backend: SqlBackend) -> None:
        self.backend = backend
        self.hasher = PasswordHasher()
        self._cache: dict[str, tuple[Principal, float]] = {}
        self._lock = threading.Lock()

    # -- tenants ---------------------------------------------------------------------------
    def create_tenant(self, name: str, *, tenant_id: str | None = None) -> Tenant:
        tenant = Tenant(id=tenant_id or new_id("ten"), name=name, created_at=ts())
        with self.backend.transaction(write=True) as tx:
            tx.execute(
                "INSERT INTO tenants (id, name, created_at) VALUES (?,?,?) ON CONFLICT DO NOTHING",
                (tenant.id, tenant.name, tenant.created_at),
            )
        return tenant

    def get_tenant(self, tenant_id: str) -> Tenant | None:
        with self.backend.transaction() as tx:
            rows = tx.execute("SELECT id, name, created_at FROM tenants WHERE id = ?", (tenant_id,))
        return None if not rows else Tenant(*(str(c) for c in rows[0]))

    def list_tenants(self) -> list[Tenant]:
        with self.backend.transaction() as tx:
            rows = tx.execute("SELECT id, name, created_at FROM tenants ORDER BY created_at")
        return [Tenant(*(str(c) for c in r)) for r in rows]

    # -- keys ------------------------------------------------------------------------------
    def create_key(
        self, tenant_id: str, name: str, *, token: str | None = None
    ) -> tuple[ApiKey, str]:
        """Create a key; returns the record and the plaintext token (shown once)."""
        if self.get_tenant(tenant_id) is None:
            raise KeyError(tenant_id)
        token = token or generate_key()
        key = ApiKey(
            id=new_id("key"),
            tenant_id=tenant_id,
            name=name,
            prefix=_prefix(token),
            created_at=ts(),
            revoked_at=None,
            last_used_at=None,
        )
        with self.backend.transaction(write=True) as tx:
            tx.execute(
                "INSERT INTO api_keys (id, tenant_id, name, prefix, hash, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (
                    key.id,
                    key.tenant_id,
                    key.name,
                    key.prefix,
                    self.hasher.hash(token),
                    key.created_at,
                ),
            )
        return key, token

    def revoke_key(self, key_id: str) -> bool:
        with self.backend.transaction(write=True) as tx:
            tx.execute(
                "UPDATE api_keys SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (ts(), key_id),
            )
            rows = tx.execute("SELECT revoked_at FROM api_keys WHERE id = ?", (key_id,))
        with self._lock:
            self._cache = {k: v for k, v in self._cache.items() if v[0].key_id != key_id}
        return bool(rows) and rows[0][0] is not None

    def list_keys(self, tenant_id: str) -> list[ApiKey]:
        with self.backend.transaction() as tx:
            rows = tx.execute(
                "SELECT id, tenant_id, name, prefix, created_at, revoked_at, last_used_at"
                " FROM api_keys WHERE tenant_id = ? ORDER BY created_at",
                (tenant_id,),
            )
        return [
            ApiKey(
                id=str(r[0]),
                tenant_id=str(r[1]),
                name=str(r[2]),
                prefix=str(r[3]),
                created_at=str(r[4]),
                revoked_at=None if r[5] is None else str(r[5]),
                last_used_at=None if r[6] is None else str(r[6]),
            )
            for r in rows
        ]

    def authenticate(self, token: str) -> Principal | None:
        cache_key = hashlib.sha256(token.encode()).hexdigest()
        with self._lock:
            hit = self._cache.get(cache_key)
            if hit is not None and hit[1] > time.monotonic():
                return hit[0]
        with self.backend.transaction() as tx:
            rows = tx.execute(
                "SELECT id, tenant_id, hash FROM api_keys WHERE prefix = ? AND revoked_at IS NULL",
                (_prefix(token),),
            )
        for key_id, tenant_id, digest in rows:
            try:
                self.hasher.verify(str(digest), token)
            except VerificationError:
                continue
            principal = Principal(tenant_id=str(tenant_id), key_id=str(key_id))
            with self._lock:
                self._cache[cache_key] = (principal, time.monotonic() + _CACHE_TTL_S)
            with self.backend.transaction(write=True) as tx:
                tx.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?", (ts(), key_id))
            return principal
        return None

    # -- bootstrap -------------------------------------------------------------------------
    def ensure_dev_key(self, token: str) -> Principal:
        """Development only: make ``token`` a valid key for the local tenant."""
        existing = self.authenticate(token)
        if existing is not None:
            return existing
        tenant = self.get_tenant(LOCAL_TENANT) or self.create_tenant(
            "local", tenant_id=LOCAL_TENANT
        )
        key, _ = self.create_key(tenant.id, "dev", token=token)
        return Principal(tenant_id=tenant.id, key_id=key.id)


__all__ = ["ApiKey", "AuthService", "Principal", "Tenant", "generate_key"]
