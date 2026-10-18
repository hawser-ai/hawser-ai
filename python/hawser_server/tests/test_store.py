"""The metadata store contract on SQLite and Postgres, plus the tenancy lint."""

from __future__ import annotations

import re
import threading
from pathlib import Path

from hawser.clock import now
from hawser.config import LOCAL_TENANT
from hawser.ids import new_id
from hawser.models import EventType, Policy, RunRequest, RunStatus, Step, StepKind, StepStatus
from hawser.storage import RunRecord, SqlBackend, SqlMetadataStore


def _run(tenant: str = LOCAL_TENANT, key: str | None = None) -> RunRecord:
    rid = new_id("run")
    return RunRecord(
        id=rid,
        tenant_id=tenant,
        status=RunStatus.queued,
        request=RunRequest(instruction="x", policy=Policy(), idempotency_key=key),
        trace_id=rid,
        created_at=now(),
        updated_at=now(),
    )


def test_runs_steps_events_round_trip(backend: SqlBackend) -> None:
    store = SqlMetadataStore(backend, migrations=())
    run = _run(key="k1")
    store.put_run(run)
    assert store.get_run(run.id) == run
    assert store.find_run_by_idempotency_key(LOCAL_TENANT, "k1") == run
    assert store.find_run_by_idempotency_key("ten_01J9X3K4N7Q2M8R5T6V7W8X9Y0", "k1") is None

    run.status = RunStatus.running
    store.put_run(run)  # upsert
    got = store.get_run(run.id)
    assert got is not None and got.status == RunStatus.running

    step = Step(id=new_id("step"), run_id=run.id, key="admit", name="Admit", kind=StepKind.admit)
    store.put_step(step)
    step2 = step.model_copy(update={"id": new_id("step"), "status": StepStatus.completed})
    store.put_step(step2)  # same key: replaced
    assert [s.id for s in store.list_steps(run.id)] == [step2.id]

    for i in range(3):
        ev = store.append_event(
            run_id=run.id, tenant_id=LOCAL_TENANT, type=EventType.step_started, payload={"i": i}
        )
        assert ev.seq == i + 1
    assert [e.seq for e in store.list_events(run.id)] == [1, 2, 3]
    assert [e.seq for e in store.list_events(run.id, after_seq=2)] == [3]

    assert not store.cancel_requested(run.id)
    store.request_cancel(run.id, LOCAL_TENANT)
    assert store.cancel_requested(run.id)


def test_event_sequence_is_safe_under_concurrency(backend: SqlBackend) -> None:
    store = SqlMetadataStore(backend, migrations=())
    run = _run()
    store.put_run(run)
    errors: list[Exception] = []

    def emit() -> None:
        try:
            for _ in range(10):
                store.append_event(
                    run_id=run.id, tenant_id=LOCAL_TENANT, type=EventType.model_called
                )
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=emit) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors[:1]
    seqs = [e.seq for e in store.list_events(run.id)]
    assert seqs == list(range(1, 61))


def test_list_runs_pagination_and_status(backend: SqlBackend) -> None:
    store = SqlMetadataStore(backend, migrations=())
    runs = [_run() for _ in range(5)]
    for i, r in enumerate(runs):
        if i % 2:
            r.status = RunStatus.accepted
        store.put_run(r)
    page1 = store.list_runs(LOCAL_TENANT, limit=2)
    assert len(page1) == 2
    last = page1[-1]
    page2 = store.list_runs(LOCAL_TENANT, limit=2, before=(last.created_at.isoformat(), last.id))
    assert len(page2) == 2
    assert {r.id for r in page1}.isdisjoint({r.id for r in page2})
    accepted = store.list_runs(LOCAL_TENANT, status="accepted")
    assert len(accepted) == 2
    assert store.list_runs("ten_01J9X3K4N7Q2M8R5T6V7W8X9Y0") == []


def test_every_list_query_is_tenant_scoped() -> None:
    """Lint: selects that are not primary-key lookups must filter on tenant_id (WP5.2)."""
    import hawser.storage.sql as sql_mod

    text = Path(sql_mod.__file__).read_text(encoding="utf-8")
    text = re.sub(r'"\s*\n\s*"', "", text)  # join adjacent string literals
    text = re.sub(r"\"\s*\+\s*\"", "", text)
    offenders: list[str] = []
    for m in re.finditer(r"SELECT [^\"]*? FROM (\w+) WHERE ([^\"]*)", text):
        table, where = m.group(1), m.group(2)
        if table == "schema_migrations":
            continue
        keyed = re.search(r"\b(id|run_id|revision_id|artifact_id|asset_id|approval_id) = \?", where)
        if "tenant_id = ?" not in where and not keyed:
            offenders.append(f"{table}: {where.strip()}")
    assert not offenders, offenders
