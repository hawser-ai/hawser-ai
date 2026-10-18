from __future__ import annotations

from fastapi import APIRouter

from hawser.models import ModelSpec, SkillManifest, ToolSpec
from hawser_server.deps import PrincipalDep, ServicesDep

router = APIRouter(prefix="/v1", tags=["catalog"])


@router.get("/skills", response_model=list[SkillManifest])
def list_skills(svc: ServicesDep, who: PrincipalDep) -> list[SkillManifest]:
    return [s.manifest for s in svc.skills.list()]


@router.get("/models", response_model=list[ModelSpec])
def list_models(svc: ServicesDep, who: PrincipalDep) -> list[ModelSpec]:
    return svc.gateway.registry.all()


@router.get("/tools", response_model=list[ToolSpec])
def list_tools(svc: ServicesDep, who: PrincipalDep) -> list[ToolSpec]:
    return svc.tools.specs()
