"""Published evaluation reports (the UI's evaluation dashboard)."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Response
from pydantic import BaseModel

from hawser.clock import now
from hawser.ids import new_id
from hawser.models import ArtifactKind
from hawser_server.deps import PrincipalDep, ServicesDep
from hawser_server.errors import BadRequest, NotFound
from hawser_server.util import ts

router = APIRouter(prefix="/v1/evals", tags=["evals"])


class EvalSummary(BaseModel):
    id: str
    name: str
    dataset: str
    skill: str | None
    model: str
    mode: str | None
    cases: int
    metrics: dict[str, float]
    artifact_id: str
    created_at: str


class EvalPublish(BaseModel):
    #: The ``report.json`` written by ``hawser eval run``.
    report: dict[str, Any]
    name: str | None = None


_COLS = "id, name, dataset, skill, model, mode, cases, metrics, artifact_id, created_at"


def _row(r: tuple[Any, ...]) -> EvalSummary:
    return EvalSummary(
        id=str(r[0]),
        name=str(r[1]),
        dataset=str(r[2]),
        skill=None if r[3] is None else str(r[3]),
        model=str(r[4]),
        mode=None if r[5] is None else str(r[5]),
        cases=int(r[6]),
        metrics=json.loads(str(r[7])),
        artifact_id=str(r[8]),
        created_at=str(r[9]),
    )


@router.post("", response_model=EvalSummary, status_code=201)
def publish_eval(
    svc: ServicesDep, who: PrincipalDep, body: EvalPublish, response: Response
) -> EvalSummary:
    """Store a report (``hawser eval publish``); the full JSON becomes an artifact."""
    report = body.report
    cfg = report.get("config") if isinstance(report.get("config"), dict) else None
    if cfg is None or "cases" not in report or "metrics" not in report:
        raise BadRequest("not an evaluation report (config, cases, metrics)", code="bad_report")
    artifacts = svc.engine.artifacts_for(who.tenant_id)
    art = artifacts.put_json(report, kind=ArtifactKind.eval_report)
    metrics = {k: float(v) for k, v in report["metrics"].items() if isinstance(v, int | float)}
    row = EvalSummary(
        id=new_id("eval"),
        name=body.name or str(report.get("id") or art.id),
        dataset=str(cfg.get("dataset", "")),
        skill=cfg.get("skill"),
        model=str(cfg.get("model", "")),
        mode=cfg.get("mode"),
        cases=len(report["cases"]),
        metrics=metrics,
        artifact_id=art.id,
        created_at=ts(now()),
    )
    with svc.backend.transaction(write=True) as tx:
        tx.execute(
            f"INSERT INTO eval_reports (tenant_id, {_COLS}) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                who.tenant_id,
                row.id,
                row.name,
                row.dataset,
                row.skill,
                row.model,
                row.mode,
                row.cases,
                json.dumps(row.metrics),
                row.artifact_id,
                row.created_at,
            ),
        )
    response.headers["Location"] = f"/v1/evals/{row.id}"
    return row


@router.get("", response_model=list[EvalSummary])
def list_evals(svc: ServicesDep, who: PrincipalDep) -> list[EvalSummary]:
    with svc.backend.transaction() as tx:
        rows = tx.execute(
            f"SELECT {_COLS} FROM eval_reports WHERE tenant_id = ? ORDER BY created_at DESC",
            (who.tenant_id,),
        )
    return [_row(r) for r in rows]


def _get(svc: ServicesDep, who: PrincipalDep, eval_id: str) -> EvalSummary:
    with svc.backend.transaction() as tx:
        rows = tx.execute(
            f"SELECT {_COLS} FROM eval_reports WHERE tenant_id = ? AND id = ?",
            (who.tenant_id, eval_id),
        )
    if not rows:
        raise NotFound(f"unknown evaluation {eval_id}", code="eval_unknown")
    return _row(rows[0])


@router.get("/{eval_id}", response_model=EvalSummary)
def get_eval(svc: ServicesDep, who: PrincipalDep, eval_id: str) -> EvalSummary:
    return _get(svc, who, eval_id)


@router.get("/{eval_id}/report")
def get_eval_report(svc: ServicesDep, who: PrincipalDep, eval_id: str) -> Any:
    """The full report: config, per-case scores, metrics, failure clusters."""
    summary = _get(svc, who, eval_id)
    return svc.engine.artifacts_for(who.tenant_id).get_json(summary.artifact_id)


@router.delete("/{eval_id}", status_code=204, response_class=Response)
def delete_eval(svc: ServicesDep, who: PrincipalDep, eval_id: str) -> Response:
    _get(svc, who, eval_id)
    with svc.backend.transaction(write=True) as tx:
        tx.execute(
            "DELETE FROM eval_reports WHERE tenant_id = ? AND id = ?", (who.tenant_id, eval_id)
        )
    return Response(status_code=204)
