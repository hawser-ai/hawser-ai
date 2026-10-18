from __future__ import annotations

import json
from pathlib import Path

from hawser_server.cli import openapi_document

ROOT = Path(__file__).resolve().parents[3]


def test_openapi_document_is_committed_and_current() -> None:
    text = openapi_document()
    doc = json.loads(text)
    paths = set(doc["paths"])
    assert {
        "/v1/runs",
        "/v1/runs/{run_id}/events",
        "/v1/reviews/{run_id}/approve",
        "/healthz",
    } <= paths
    assert "RunResult" in doc["components"]["schemas"]
    committed = ROOT / "api" / "openapi" / "openapi.json"
    assert committed.exists(), "run `hawserd openapi` and commit api/openapi/openapi.json"
    assert committed.read_text(encoding="utf-8") == text, "api/openapi/openapi.json is stale"
