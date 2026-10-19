"""Tenant settings (the default policy) and the contract schemas."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter

from hawser.clock import now
from hawser.models import Policy
from hawser.models.export import EXPORTED, schema_for
from hawser_server.deps import PrincipalDep, ServicesDep
from hawser_server.errors import NotFound
from hawser_server.util import ts

router = APIRouter(prefix="/v1", tags=["settings"])


def default_policy(svc: ServicesDep, tenant_id: str) -> Policy:
    with svc.backend.transaction() as tx:
        rows = tx.execute("SELECT data FROM tenant_settings WHERE tenant_id = ?", (tenant_id,))
    if not rows:
        return Policy()
    data = json.loads(str(rows[0][0]))
    return Policy.model_validate(data.get("policy", {}))


@router.get("/policy", response_model=Policy)
def get_policy(svc: ServicesDep, who: PrincipalDep) -> Policy:
    """The tenant's default policy (what the playground starts from)."""
    return default_policy(svc, who.tenant_id)


@router.put("/policy", response_model=Policy)
def put_policy(svc: ServicesDep, who: PrincipalDep, policy: Policy) -> Policy:
    data = {"policy": json.loads(policy.model_dump_json())}
    with svc.backend.transaction(write=True) as tx:
        tx.execute(
            "INSERT INTO tenant_settings (tenant_id, data, updated_at) VALUES (?,?,?)"
            " ON CONFLICT (tenant_id) DO UPDATE SET data = excluded.data,"
            " updated_at = excluded.updated_at",
            (who.tenant_id, json.dumps(data), ts(now())),
        )
    return policy


@router.get("/schemas", response_model=list[str])
def list_schemas(who: PrincipalDep) -> list[str]:
    return sorted(EXPORTED)


@router.get("/schemas/{name}")
def get_schema(who: PrincipalDep, name: str) -> dict[str, Any]:
    """A contract model's JSON Schema (the same files as ``api/schemas/``)."""
    if name not in EXPORTED:
        raise NotFound(f"unknown schema {name}", code="schema_unknown")
    return schema_for(name)
