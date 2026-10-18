from __future__ import annotations

import pytest

from hawser.config import LOCAL_TENANT
from hawser.storage import SqlBackend
from hawser_server.auth import AuthService, generate_key


def test_keys_authenticate_and_revoke(backend: SqlBackend) -> None:
    auth = AuthService(backend)
    tenant = auth.create_tenant("acme")
    key, token = auth.create_key(tenant.id, "ci")
    assert token.startswith("hk_live_") and len(token) == 48
    assert auth.authenticate(token) is not None
    assert auth.authenticate(token).tenant_id == tenant.id  # type: ignore[union-attr]
    assert auth.authenticate("hk_live_nope") is None
    assert auth.authenticate(token[:-1] + ("x" if token[-1] != "x" else "y")) is None
    assert [k.id for k in auth.list_keys(tenant.id)] == [key.id]
    assert auth.list_keys(tenant.id)[0].last_used_at is not None

    assert auth.revoke_key(key.id)
    assert auth.authenticate(token) is None  # the cache is invalidated
    assert not auth.revoke_key("key_01J9X3K4N7Q2M8R5T6V7W8X9Y0")


def test_create_key_requires_tenant(backend: SqlBackend) -> None:
    auth = AuthService(backend)
    with pytest.raises(KeyError):
        auth.create_key("ten_01J9X3K4N7Q2M8R5T6V7W8X9Y0", "x")


def test_dev_key_bootstrap_is_idempotent(backend: SqlBackend) -> None:
    auth = AuthService(backend)
    token = generate_key()
    first = auth.ensure_dev_key(token)
    second = auth.ensure_dev_key(token)
    assert first == second
    assert first.tenant_id == LOCAL_TENANT
    assert len(auth.list_keys(LOCAL_TENANT)) == 1
    # any token shape works for the dev key (prefix is a hash, not a format)
    assert auth.ensure_dev_key("plain-dev-secret").tenant_id == LOCAL_TENANT
    assert auth.authenticate("plain-dev-secret") is not None
