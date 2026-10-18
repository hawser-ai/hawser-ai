"""The durable worker: lease a run, heartbeat, execute from checkpoints, deliver webhooks.

Any number of workers may run against one database. A worker that dies mid-step loses its
lease; another worker re-executes the run and finished steps replay from their checkpoints.
A worker whose lease was stolen (clock skew, a very long step) aborts at the next step boundary
through the engine ``guard`` instead of racing the new owner.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from hawser.models import PAUSED_STATUSES, RESTING_STATUSES, TERMINAL_STATUSES, RunStatus
from hawser.runtime.engine import ExecutionAborted
from hawser_server.jobs import Job
from hawser_server.services import Services

log = logging.getLogger("hawserd.worker")


class LeaseLost(ExecutionAborted):
    """This worker no longer owns the run; another worker took the expired lease."""


class Worker:
    def __init__(
        self,
        services: Services,
        *,
        concurrency: int | None = None,
        lease_seconds: int | None = None,
        poll_s: float | None = None,
        owner: str | None = None,
    ) -> None:
        self.svc = services
        self.concurrency = concurrency or services.config.worker_concurrency
        self.lease_seconds = lease_seconds or services.config.lease_seconds
        self.poll_s = poll_s or services.config.poll_interval_s
        self.owner = owner or f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"
        self._stop = threading.Event()
        self._pool = ThreadPoolExecutor(
            max_workers=self.concurrency, thread_name_prefix="hawser-run"
        )
        self._active: set[str] = set()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.processed = 0

    # -- lifecycle -------------------------------------------------------------------------
    def start(self) -> threading.Thread:
        self._thread = threading.Thread(target=self.run_forever, name="hawser-worker", daemon=True)
        self._thread.start()
        return self._thread

    def stop(self, *, wait: bool = True) -> None:
        self._stop.set()
        if wait and self._thread is not None:
            self._thread.join(timeout=self.lease_seconds)
        self._pool.shutdown(wait=wait)

    def run_forever(self) -> None:
        log.info("worker %s started (concurrency %d)", self.owner, self.concurrency)
        while not self._stop.is_set():
            did = self.tick()
            if not did:
                self._stop.wait(self.poll_s)
        log.info("worker %s stopping", self.owner)

    def tick(self) -> bool:
        """Lease up to the free capacity and deliver due webhooks. ``True`` if anything ran."""
        did = False
        with self._lock:
            free = self.concurrency - len(self._active)
        for _ in range(max(free, 0)):
            job = self.svc.queue.lease(self.owner, self.lease_seconds)
            if job is None:
                break
            with self._lock:
                self._active.add(job.run_id)
            self._pool.submit(self._run_job, job)
            did = True
        try:
            if self.svc.webhooks.deliver_due():
                did = True
        except Exception:
            log.exception("webhook delivery pass failed")
        return did

    def drain(self, timeout: float = 30.0) -> None:
        """Block until no job is active (tests and graceful shutdown)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if not self._active:
                    return
            time.sleep(0.02)

    # -- one job ---------------------------------------------------------------------------
    def _run_job(self, job: Job) -> None:
        stop_hb = threading.Event()
        lost = threading.Event()

        def heartbeat() -> None:
            interval = max(self.lease_seconds / 3, 1.0)
            while not stop_hb.wait(interval):
                try:
                    if not self.svc.queue.heartbeat(job.run_id, self.owner, self.lease_seconds):
                        lost.set()
                        return
                except Exception:
                    log.exception("heartbeat failed for %s", job.run_id)

        hb = threading.Thread(target=heartbeat, name=f"hb-{job.run_id}", daemon=True)
        hb.start()
        try:
            self._execute(job, lost)
        except Exception:
            log.exception("job %s crashed outside the engine", job.run_id)
        finally:
            stop_hb.set()
            with self._lock:
                self._active.discard(job.run_id)
            self.processed += 1

    def _execute(self, job: Job, lost: threading.Event) -> None:
        run = self.svc.meta.get_run(job.run_id)
        if run is None:
            self.svc.queue.complete(job.run_id, self.owner)
            return
        if run.status in TERMINAL_STATUSES:
            self.svc.queue.complete(job.run_id, self.owner)
            return
        if job.attempts > job.max_attempts:
            error: dict[str, Any] = {
                "type": "WorkerError",
                "code": "worker_attempts_exhausted",
                "message": f"the run was leased {job.attempts - 1} times without finishing",
                "details": {"attempts": job.attempts - 1, "max_attempts": job.max_attempts},
            }
            result = self.svc.engine.abandon(run, status=RunStatus.failed, error=error)
            self.svc.queue.complete(job.run_id, self.owner)
            self._notify(run.tenant_id, run.id, result.status)
            return

        def guard() -> None:
            if lost.is_set() or not self.svc.queue.owns(job.run_id, self.owner):
                raise LeaseLost(f"lease on {job.run_id} was lost")

        resuming = job.resume or job.attempts > 1
        try:
            result = self.svc.engine.execute(run, resuming=resuming, guard=guard)
        except LeaseLost:
            log.warning("lease lost on %s; another worker owns it", job.run_id)
            return
        if result.status in TERMINAL_STATUSES | RESTING_STATUSES | PAUSED_STATUSES:
            self.svc.queue.complete(job.run_id, self.owner)
        self._notify(run.tenant_id, run.id, result.status)

    def _notify(self, tenant_id: str, run_id: str, status: RunStatus) -> None:
        if status in TERMINAL_STATUSES:
            event = "run.completed"
        elif status in RESTING_STATUSES:
            event = "run.needs_review"
        elif status in PAUSED_STATUSES:
            event = "run.awaiting_approval"
        else:
            return
        base = self.svc.config.public_url.rstrip("/")
        try:
            self.svc.webhooks.fan_out(
                tenant_id,
                event,
                {
                    "run_id": run_id,
                    "status": status.value,
                    "result_url": f"{base}/v1/runs/{run_id}/result",
                },
                run_id=run_id,
            )
        except Exception:
            log.exception("webhook fan-out failed for %s", run_id)


__all__ = ["LeaseLost", "Worker"]
