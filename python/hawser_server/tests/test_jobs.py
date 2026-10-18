from __future__ import annotations

import threading

from hawser.config import LOCAL_TENANT
from hawser.storage import SqlBackend
from hawser_server.jobs import JobQueue


def test_lease_heartbeat_complete(backend: SqlBackend) -> None:
    q = JobQueue(backend, max_attempts=3)
    job = q.enqueue("run_01J9X3K4N7Q2M8R5T6V7W8X9Y0", LOCAL_TENANT)
    assert job.state == "pending" and job.attempts == 0

    leased = q.lease("w1", 60)
    assert leased is not None and leased.lease_owner == "w1" and leased.attempts == 1
    assert q.lease("w2", 60) is None  # nothing else to lease
    assert q.owns(leased.run_id, "w1")
    assert not q.owns(leased.run_id, "w2")
    assert q.heartbeat(leased.run_id, "w1", 60)
    assert not q.heartbeat(leased.run_id, "w2", 60)
    assert q.complete(leased.run_id, "w1")
    assert q.lease("w1", 60) is None
    assert q.counts() == {"done": 1}


def test_expired_lease_is_taken_over(backend: SqlBackend) -> None:
    q = JobQueue(backend)
    q.enqueue("run_01J9X3K4N7Q2M8R5T6V7W8X9Y0", LOCAL_TENANT)
    first = q.lease("w1", 60)
    assert first is not None
    with backend.transaction(write=True) as tx:
        tx.execute(
            "UPDATE jobs SET lease_expires_at = ? WHERE run_id = ?",
            ("2000-01-01T00:00:00.000000+00:00", first.run_id),
        )
    second = q.lease("w2", 60)
    assert second is not None and second.lease_owner == "w2" and second.attempts == 2
    assert not q.heartbeat(first.run_id, "w1", 60)  # the old owner learns it lost the lease
    assert not q.owns(first.run_id, "w1")


def test_concurrent_workers_lease_each_job_once(backend: SqlBackend) -> None:
    q = JobQueue(backend)
    ids = [f"run_01J9X3K4N7Q2M8R5T6V7W8X9{c}" for c in "ABCDEFGHJK"]
    for rid in ids:
        q.enqueue(rid, LOCAL_TENANT)
    seen: list[str] = []
    lock = threading.Lock()

    def work(owner: str) -> None:
        while True:
            job = q.lease(owner, 30)
            if job is None:
                return
            with lock:
                seen.append(job.run_id)
            q.complete(job.run_id, owner)

    threads = [threading.Thread(target=work, args=(f"w{i}",)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(seen) == sorted(ids)
    assert len(seen) == len(set(seen))


def test_reenqueue_resets_and_remove(backend: SqlBackend) -> None:
    q = JobQueue(backend)
    rid = "run_01J9X3K4N7Q2M8R5T6V7W8X9Y0"
    q.enqueue(rid, LOCAL_TENANT)
    leased = q.lease("w1", 60)
    assert leased is not None
    q.complete(rid, "w1")
    again = q.enqueue(rid, LOCAL_TENANT, resume=True)
    assert again.state == "pending" and again.resume and again.attempts == 0
    q.remove(rid)
    assert q.get(rid) is None
