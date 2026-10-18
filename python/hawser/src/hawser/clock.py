"""Time helpers. All timestamps in Hawser are timezone-aware UTC, serialised as RFC 3339."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator


def now() -> datetime:
    """Current UTC time, microsecond precision, tz-aware."""
    return datetime.now(UTC)


def monotonic_ms() -> int:
    """Monotonic clock in milliseconds, for latency measurement only."""
    return time.monotonic_ns() // 1_000_000


def ensure_utc(value: datetime) -> datetime:
    """Reject naive datetimes; normalise aware ones to UTC."""
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError("timestamps must be timezone-aware (UTC)")
    return value.astimezone(UTC)


UtcDatetime = Annotated[datetime, AfterValidator(ensure_utc)]

__all__ = ["UtcDatetime", "ensure_utc", "monotonic_ms", "now"]
