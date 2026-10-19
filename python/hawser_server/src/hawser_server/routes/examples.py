"""The bundled quickstart, admitted for a tenant: the playground's one-click example."""

from __future__ import annotations

import json
from typing import Any

import yaml
from fastapi import APIRouter
from pydantic import BaseModel

from hawser.conformance.scenarios import INSTRUCTION, quickstart_dir
from hawser.models import Asset, Policy
from hawser.runtime.admission import Admitter, admit_bytes
from hawser_server.deps import PrincipalDep, ServicesDep

router = APIRouter(prefix="/v1/examples", tags=["examples"])


class QuickstartExample(BaseModel):
    asset: Asset
    instruction: str
    output_schema: dict[str, Any]
    policy: Policy
    #: Set `HAWSER_RECORD_MODE=replay` with this cassette directory to run it without a key.
    cassette_dir: str


@router.post("/quickstart", response_model=QuickstartExample)
def quickstart(svc: ServicesDep, who: PrincipalDep) -> QuickstartExample:
    """Admit the bundled invoice for this tenant and return the request that goes with it."""
    qs = quickstart_dir()
    adm = Admitter(
        config=svc.config.runtime,
        tenant_id=who.tenant_id,
        meta=svc.meta,
        artifacts=svc.engine.artifacts_for(who.tenant_id),
    )
    asset = admit_bytes(adm, (qs / "invoice.pdf").read_bytes(), name="invoice.pdf")
    schema = json.loads((qs / "invoice.schema.json").read_text(encoding="utf-8"))
    policy = Policy.model_validate(
        yaml.safe_load((qs / "policy.yaml").read_text(encoding="utf-8")) or {}
    )
    return QuickstartExample(
        asset=asset,
        instruction=INSTRUCTION,
        output_schema=schema,
        policy=policy,
        cassette_dir=str(qs / "cassette"),
    )
