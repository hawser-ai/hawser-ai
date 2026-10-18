"""Durable execution: crash recovery from checkpoints, lost leases, poison runs, cancel."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from hawser.gateway import Gateway, load_registry
from hawser.gateway.base import ModelRequest, ModelResponse
from hawser.models import EventType, ModelSpec, PathInput, Policy, RunRequest, RunStatus
from hawser.models.common import JsonSchema
from hawser.models.policy import Limits
from hawser.testing import StaticAdapter
from hawser_server.jobs import JobQueue
from hawser_server.services import Services
from hawser_server.testing import ServicesFactory, wait_for
from hawser_server.worker import Worker


class BlockingAdapter(StaticAdapter):
    """Like StaticAdapter, but every call waits for ``release`` (a worker stuck mid-step)."""

    def __init__(self, output: Any) -> None:
        super().__init__(output)
        self.entered = threading.Event()
        self.release = threading.Event()

    def complete(self, spec: ModelSpec, request: ModelRequest) -> ModelResponse:
        self.entered.set()
        self.release.wait(timeout=30)
        return super().complete(spec, request)


def _request(pdf: Path, schema: JsonSchema, **policy: Any) -> RunRequest:
    return RunRequest(
        instruction="Extract the invoice.",
        inputs=[PathInput(path=str(pdf), name="invoice.pdf")],
        output_schema=schema,
        policy=Policy(**policy),
    )


def test_crashed_worker_is_taken_over_without_repeating_paid_work(
    make_services: ServicesFactory,
    tmp_path: Path,
    truth_envelope: Any,
    invoice_pdf: Path,
    invoice_schema: JsonSchema,
) -> None:
    blocking = BlockingAdapter(truth_envelope)
    db = f"sqlite:///{tmp_path / 'shared.db'}"
    svc_a = make_services(
        gateway=Gateway(load_registry(), {"anthropic": blocking}), database_url=db
    )
    good = StaticAdapter(truth_envelope)
    svc_b = make_services(gateway=Gateway(load_registry(), {"anthropic": good}), database_url=db)
    tenant = svc_a.auth.create_tenant("acme").id

    run, _ = svc_a.engine.create_run(_request(invoice_pdf, invoice_schema), tenant_id=tenant)
    svc_a.queue.enqueue(run.id, tenant)

    worker_a = Worker(svc_a, concurrency=1, lease_seconds=5, poll_s=0.02, owner="A")
    assert worker_a.tick()  # leases and starts executing in a pool thread
    assert blocking.entered.wait(timeout=30)  # stuck inside the extraction step

    # The lease "expires" (simulated crash): another worker takes the run over.
    with svc_a.backend.transaction(write=True) as tx:
        tx.execute(
            "UPDATE jobs SET lease_expires_at = ? WHERE run_id = ?",
            ("2000-01-01T00:00:00.000000+00:00", run.id),
        )
    worker_b = Worker(svc_b, concurrency=1, lease_seconds=5, poll_s=0.02, owner="B")
    assert worker_b.tick()
    worker_b.drain()
    result = svc_b.engine.view(run.id)
    assert result is not None and result.status == RunStatus.accepted, result and result.error
    assert len(good.calls) == 1  # exactly one paid call completed the run
    events = svc_b.meta.list_events(run.id)
    skipped = [e for e in events if e.type == EventType.step_skipped]
    assert {e.payload["key"] for e in skipped} >= {"admit"}  # checkpoints replayed, not redone

    # The stuck worker wakes up, sees it lost the lease, and discards its work.
    blocking.release.set()
    worker_a.drain()
    after = svc_b.engine.view(run.id)
    assert after is not None and after.status == RunStatus.accepted
    assert after.revision_id == result.revision_id  # nothing was overwritten
    job = svc_b.queue.get(run.id)
    assert job is not None and job.state == "done" and job.lease_owner == "B"
    worker_a.stop(wait=False)
    worker_b.stop(wait=False)


def test_poison_run_fails_after_max_attempts(
    make_services: ServicesFactory,
    truth_envelope: Any,
    invoice_pdf: Path,
    invoice_schema: JsonSchema,
) -> None:
    svc = make_services(output=truth_envelope, worker_max_attempts=2)
    tenant = svc.auth.create_tenant("acme").id
    run, _ = svc.engine.create_run(_request(invoice_pdf, invoice_schema), tenant_id=tenant)
    svc.queue.enqueue(run.id, tenant)
    with svc.backend.transaction(write=True) as tx:
        tx.execute("UPDATE jobs SET attempts = 2 WHERE run_id = ?", (run.id,))
    w = Worker(svc, concurrency=1, lease_seconds=5, poll_s=0.02)
    assert w.tick()
    w.drain()
    result = svc.engine.view(run.id)
    assert result is not None and result.status == RunStatus.failed
    assert result.error is not None and result.error["code"] == "worker_attempts_exhausted"
    assert svc.queue.get(run.id).state == "done"  # type: ignore[union-attr]
    w.stop(wait=False)


def test_cancel_signal_stops_at_step_boundary(
    make_services: ServicesFactory,
    truth_envelope: Any,
    invoice_pdf: Path,
    invoice_schema: JsonSchema,
) -> None:
    blocking = BlockingAdapter(truth_envelope)
    svc = make_services(gateway=Gateway(load_registry(), {"anthropic": blocking}))
    tenant = svc.auth.create_tenant("acme").id
    run, _ = svc.engine.create_run(_request(invoice_pdf, invoice_schema), tenant_id=tenant)
    svc.queue.enqueue(run.id, tenant)
    w = Worker(svc, concurrency=1, lease_seconds=5, poll_s=0.02)
    assert w.tick()
    assert blocking.entered.wait(timeout=30)
    svc.meta.request_cancel(run.id, tenant)  # what POST /cancel does for an executing run
    blocking.release.set()
    w.drain()
    result = svc.engine.view(run.id)
    assert result is not None and result.status == RunStatus.cancelled
    assert result.error is not None and result.error["code"] == "cancelled"
    # the paid step was still checkpointed before the cancel took effect
    assert any(
        s.key.startswith("extract") and s.status.value == "completed"
        for s in svc.meta.list_steps(run.id)
    )
    w.stop(wait=False)


def test_wall_time_limit(
    make_services: ServicesFactory,
    truth_envelope: Any,
    invoice_pdf: Path,
    invoice_schema: JsonSchema,
) -> None:
    blocking = BlockingAdapter(truth_envelope)
    svc = make_services(gateway=Gateway(load_registry(), {"anthropic": blocking}))
    tenant = svc.auth.create_tenant("acme").id
    req = _request(invoice_pdf, invoice_schema, limits=Limits(max_wall_time_s=0))
    run, _ = svc.engine.create_run(req, tenant_id=tenant)
    svc.queue.enqueue(run.id, tenant)
    w = Worker(svc, concurrency=1, lease_seconds=5, poll_s=0.02)
    # a zero wall-time budget trips before the first step; nothing blocks
    blocking.release.set()
    assert w.tick()
    w.drain()
    result = svc.engine.view(run.id)
    assert result is not None and result.status == RunStatus.needs_review
    assert result.error is not None and result.error["code"] == "max_wall_time"
    w.stop(wait=False)


def test_worker_loop_processes_queue(
    services: Services, invoice_pdf: Path, invoice_schema: JsonSchema
) -> None:
    tenant = services.auth.create_tenant("acme").id
    ids: list[str] = []
    for _ in range(3):
        run, _ = services.engine.create_run(_request(invoice_pdf, invoice_schema), tenant_id=tenant)
        services.queue.enqueue(run.id, tenant)
        ids.append(run.id)
    w = Worker(services, concurrency=2, lease_seconds=5, poll_s=0.02)
    w.start()
    try:
        wait_for(
            lambda: all(
                (r := services.engine.view(i)) is not None and r.status == RunStatus.accepted
                for i in ids
            )
        )
    finally:
        w.stop()
    assert JobQueue(services.backend).counts() == {"done": 3}
