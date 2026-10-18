"""Cassettes: a directory of JSON files, one per recorded call, keyed by request fingerprint."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from hawser.clock import now
from hawser.config import RecordMode


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def request_fingerprint(kind: str, provider: str, name: str, request: dict[str, Any]) -> str:
    payload = canonical_json({"kind": kind, "provider": provider, "name": name, "request": request})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


class Cassette:
    def __init__(self, directory: Path, mode: RecordMode) -> None:
        self.directory = directory
        self.mode = mode
        if mode in (RecordMode.record, RecordMode.auto):
            directory.mkdir(parents=True, exist_ok=True)

    @property
    def replays(self) -> bool:
        return self.mode in (RecordMode.replay, RecordMode.auto)

    @property
    def records(self) -> bool:
        return self.mode in (RecordMode.record, RecordMode.auto)

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def lookup(self, key: str) -> dict[str, Any] | None:
        path = self._path(key)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None

    def store(
        self,
        key: str,
        *,
        kind: str,
        provider: str,
        name: str,
        request: dict[str, Any],
        response: dict[str, Any],
        source: str = "live",
    ) -> Path:
        entry = {
            "key": key,
            "kind": kind,
            "provider": provider,
            "name": name,
            "source": source,  # "live" or "synthetic"
            "recorded_at": now().isoformat(),
            "request": request,
            "response": response,
        }
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(entry, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return path

    def __len__(self) -> int:
        return len(list(self.directory.glob("*.json"))) if self.directory.exists() else 0


__all__ = ["Cassette", "canonical_json", "request_fingerprint"]
