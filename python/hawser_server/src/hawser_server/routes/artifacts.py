from __future__ import annotations

from fastapi import APIRouter, Response

from hawser.models import Artifact
from hawser.storage import artifact_key
from hawser_server.deps import PrincipalDep, ServicesDep, owned_artifact
from hawser_server.schemas import ArtifactUrl

router = APIRouter(prefix="/v1/artifacts", tags=["artifacts"])


@router.get("/{artifact_id}", response_model=Artifact)
def get_artifact(svc: ServicesDep, who: PrincipalDep, artifact_id: str) -> Artifact:
    return owned_artifact(svc, who, artifact_id)


@router.get("/{artifact_id}/content", response_class=Response)
def get_artifact_content(svc: ServicesDep, who: PrincipalDep, artifact_id: str) -> Response:
    artifact = owned_artifact(svc, who, artifact_id)
    data = svc.store.get_bytes(artifact_key(artifact.tenant_id, artifact.meta.scoped_hash))
    return Response(
        content=data,
        media_type=artifact.meta.media_type,
        headers={
            "Cache-Control": "private, max-age=3600",
            "ETag": f'"{artifact.meta.content_hash}"',
        },
    )


@router.get("/{artifact_id}/url", response_model=ArtifactUrl)
def get_artifact_url(svc: ServicesDep, who: PrincipalDep, artifact_id: str) -> ArtifactUrl:
    """A URL to fetch the bytes: presigned when S3 is configured without proxying."""
    artifact = owned_artifact(svc, who, artifact_id)
    if svc.s3 is not None and not svc.config.artifacts_proxy:
        ttl = svc.config.s3_presign_ttl_s
        url = svc.s3.url(artifact_key(artifact.tenant_id, artifact.meta.scoped_hash), ttl_s=ttl)
        return ArtifactUrl(artifact_id=artifact.id, url=url, expires_in_s=ttl)
    base = svc.config.public_url.rstrip("/")
    return ArtifactUrl(artifact_id=artifact.id, url=f"{base}/v1/artifacts/{artifact.id}/content")
