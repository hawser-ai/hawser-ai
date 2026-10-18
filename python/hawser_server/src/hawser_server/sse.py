"""Server-sent events for a run: ``id`` = seq (so ``Last-Event-ID`` resumes), ``event`` = type.

The stream polls the events table and closes with ``event: stream.end`` once the run rests
(terminal, ``needs_review`` or ``awaiting_approval``) and every event has been sent.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator

from hawser.models import PAUSED_STATUSES, RESTING_STATUSES, TERMINAL_STATUSES, Event
from hawser_server.services import Services

RESTED = TERMINAL_STATUSES | RESTING_STATUSES | PAUSED_STATUSES


def format_event(event: Event) -> bytes:
    data = event.model_dump_json()
    return f"id: {event.seq}\nevent: {event.type.value}\ndata: {data}\n\n".encode()


def stream_run_events(
    svc: Services,
    run_id: str,
    *,
    after_seq: int = 0,
    poll_s: float = 0.5,
    keepalive_s: float = 15.0,
    max_s: float | None = None,
) -> Iterator[bytes]:
    started = time.monotonic()
    last_sent = started
    seq = after_seq
    while True:
        events = svc.meta.list_events(run_id, after_seq=seq)
        for ev in events:
            yield format_event(ev)
            seq = ev.seq
            last_sent = time.monotonic()
        if not events:
            run = svc.meta.get_run(run_id)
            if run is None or run.status in RESTED:
                payload = json.dumps(
                    {"run_id": run_id, "status": run.status.value if run else None}
                )
                yield f"event: stream.end\ndata: {payload}\n\n".encode()
                return
            if max_s is not None and time.monotonic() - started > max_s:
                return
            if time.monotonic() - last_sent >= keepalive_s:
                yield b": keep-alive\n\n"
                last_sent = time.monotonic()
            time.sleep(poll_s)


__all__ = ["RESTED", "format_event", "stream_run_events"]
