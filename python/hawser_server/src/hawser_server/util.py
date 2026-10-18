"""Small shared helpers."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

from hawser.clock import now


def ts(dt: datetime | None = None) -> str:
    """Fixed-width UTC timestamp so lexical order equals chronological order."""
    dt = dt or now()
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def ts_after(seconds: float) -> str:
    return ts(now() + timedelta(seconds=seconds))


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def encode_cursor(created_at: str, run_id: str) -> str:
    return base64.urlsafe_b64encode(f"{created_at}|{run_id}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[str, str] | None:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        created_at, _, run_id = raw.partition("|")
    except (ValueError, UnicodeDecodeError):
        return None
    if not created_at or not run_id:
        return None
    return created_at, run_id


__all__ = ["decode_cursor", "encode_cursor", "parse_ts", "ts", "ts_after"]
