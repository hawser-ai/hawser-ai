from __future__ import annotations

from fastapi import APIRouter, Response

from hawser_server import __version__
from hawser_server.deps import ServicesDep
from hawser_server.schemas import Health

router = APIRouter(tags=["system"])


@router.get("/healthz", response_model=Health)
def healthz() -> Health:
    return Health(status="ok", version=__version__)


@router.get("/readyz", response_model=Health)
def readyz(svc: ServicesDep, response: Response) -> Health:
    ok = svc.ready()
    if not ok:
        response.status_code = 503
    return Health(
        status="ready" if ok else "unavailable",
        version=__version__,
        database=ok,
        jobs=svc.queue.counts() if ok else {},
    )
