"""The HTTP API end to end on the SQLite backend, with an in-process worker."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from hawser.models import RunStatus
from hawser.models.common import JsonSchema
from hawser_server.app import create_app
from hawser_server.services import Services
from hawser_server.testing import ServicesFactory, wait_for
from hawser_server.worker import Worker


def _upload(client: TestClient, pdf: Path) -> str:
    with pdf.open("rb") as fh:
        res = client.post("/v1/assets", files={"file": ("invoice.pdf", fh, "application/pdf")})
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["media_class"] == "pdf" and body["manifest"]["pdf"]["page_count"] >= 1
    return body["id"]


def _create_run(
    client: TestClient, asset_id: str, schema: JsonSchema, **extra: Any
) -> dict[str, Any]:
    body = {
        "instruction": "Extract the invoice.",
        "inputs": [{"kind": "asset", "asset_id": asset_id}],
        "output_schema": schema,
        **extra,
    }
    res = client.post("/v1/runs", json=body)
    assert res.status_code in (200, 201), res.text
    return res.json()


def _wait_rested(client: TestClient, run_id: str) -> dict[str, Any]:
    def rested() -> bool:
        status = client.get(f"/v1/runs/{run_id}").json()["status"]
        return status in (
            "accepted",
            "needs_review",
            "rejected",
            "failed",
            "cancelled",
            "awaiting_approval",
        )

    wait_for(rested)
    res = client.get(f"/v1/runs/{run_id}/result")
    assert res.status_code == 200, res.text
    return res.json()


def test_health_and_auth(client: TestClient, services: Services) -> None:
    assert client.get("/healthz").json()["status"] == "ok"
    ready = client.get("/readyz")
    assert ready.status_code == 200 and ready.json()["database"] is True
    assert "X-Hawser-Request-Id" in ready.headers

    anon = TestClient(create_app(services))
    res = anon.get("/v1/runs")
    assert res.status_code == 401
    assert res.headers["content-type"].startswith("application/problem+json")
    assert res.json()["code"] == "unauthorized"
    res = anon.get("/v1/runs", headers={"Authorization": "Bearer hk_live_wrong"})
    assert res.status_code == 401


def test_run_end_to_end(
    client: TestClient, worker: Worker, invoice_pdf: Path, invoice_schema: JsonSchema
) -> None:
    asset_id = _upload(client, invoice_pdf)
    assert client.get(f"/v1/assets/{asset_id}").status_code == 200

    created = _create_run(client, asset_id, invoice_schema)
    run_id = created["id"]
    assert created["status"] in (
        "queued",
        "admitting",
        "planning",
        "running",
        "validating",
        "accepted",
    )

    result = _wait_rested(client, run_id)
    assert result["status"] == "accepted", result.get("error")
    assert result["result"]["invoice_number"].startswith("INV-")
    assert result["usage"]["totals"]["model_calls"] == 1

    events = client.get(f"/v1/runs/{run_id}/events").json()
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert events[0]["type"] == "run.created" and events[-1]["type"] == "result.assembled"
    steps = client.get(f"/v1/runs/{run_id}/steps").json()
    assert {s["key"] for s in steps} >= {"admit"}
    revisions = client.get(f"/v1/runs/{run_id}/revisions").json()
    assert [r["kind"] for r in revisions] == ["machine"]

    page = client.get("/v1/runs", params={"limit": 10}).json()
    assert page["items"][0]["id"] == run_id and page["next"] is None

    # artifacts are reachable and tenant-owned
    art = result["artifacts"][0]["artifact_id"]
    assert client.get(f"/v1/artifacts/{art}").status_code == 200
    content = client.get(f"/v1/artifacts/{art}/content")
    assert content.status_code == 200 and len(content.content) > 0
    url = client.get(f"/v1/artifacts/{art}/url").json()
    assert url["url"].endswith(f"/v1/artifacts/{art}/content")

    # SSE replays the finished log and closes with stream.end
    with client.stream(
        "GET", f"/v1/runs/{run_id}/events", headers={"Accept": "text/event-stream"}
    ) as res:
        assert res.headers["content-type"].startswith("text/event-stream")
        lines = list(res.iter_lines())
    ids = [ln for ln in lines if ln.startswith("id: ")]
    assert ids[0] == "id: 1" and ids[-1] == f"id: {len(events)}"
    assert "event: stream.end" in lines

    # Last-Event-ID resumes
    with client.stream(
        "GET",
        f"/v1/runs/{run_id}/events",
        headers={"Accept": "text/event-stream", "Last-Event-ID": str(len(events) - 1)},
    ) as res:
        tail = [ln for ln in res.iter_lines() if ln.startswith("id: ")]
    assert tail == [f"id: {len(events)}"]

    usage = client.get("/v1/usage").json()
    assert usage["runs"] >= 1 and usage["model_calls"] >= 1


def test_idempotency_key(
    client: TestClient, worker: Worker, invoice_pdf: Path, invoice_schema: JsonSchema
) -> None:
    asset_id = _upload(client, invoice_pdf)
    body = {
        "instruction": "Extract the invoice.",
        "inputs": [{"kind": "asset", "asset_id": asset_id}],
        "output_schema": invoice_schema,
    }
    first = client.post("/v1/runs", json=body, headers={"Idempotency-Key": "abc"})
    second = client.post("/v1/runs", json=body, headers={"Idempotency-Key": "abc"})
    assert first.status_code == 201 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    bad = client.post(
        "/v1/runs", json={**body, "idempotency_key": "other"}, headers={"Idempotency-Key": "abc"}
    )
    assert bad.status_code == 400 and bad.json()["code"] == "idempotency_mismatch"
    path_input = client.post(
        "/v1/runs", json={**body, "inputs": [{"kind": "path", "path": "/tmp/x.pdf"}]}
    )
    assert path_input.status_code == 400 and path_input.json()["code"] == "path_inputs_unsupported"


def test_cancel_queued_run_without_worker(
    client: TestClient, invoice_pdf: Path, invoice_schema: JsonSchema
) -> None:
    asset_id = _upload(client, invoice_pdf)
    run_id = _create_run(client, asset_id, invoice_schema)["id"]
    res = client.post(f"/v1/runs/{run_id}/cancel").json()
    assert res == {"run_id": run_id, "cancelled": True, "status": "cancelled"}
    result = client.get(f"/v1/runs/{run_id}/result").json()
    assert result["status"] == "cancelled"
    again = client.post(f"/v1/runs/{run_id}/cancel").json()
    assert again["cancelled"] is False


def test_cross_tenant_reads_are_404(
    client: TestClient, services: Services, invoice_pdf: Path, invoice_schema: JsonSchema
) -> None:
    asset_id = _upload(client, invoice_pdf)
    run_id = _create_run(client, asset_id, invoice_schema)["id"]
    art = client.get(f"/v1/runs/{run_id}/result")  # not ready yet
    assert art.status_code == 404 and art.json()["code"] == "result_not_ready"

    other = services.auth.create_tenant("other")
    _, token = services.auth.create_key(other.id, "t")
    stranger = TestClient(create_app(services), headers={"Authorization": f"Bearer {token}"})
    assert stranger.get(f"/v1/runs/{run_id}").status_code == 404
    assert stranger.get(f"/v1/assets/{asset_id}").status_code == 404
    assert stranger.get(f"/v1/runs/{run_id}/events").status_code == 404
    assert stranger.post(f"/v1/runs/{run_id}/cancel").status_code == 404
    assert stranger.get("/v1/runs").json()["items"] == []
    assert (
        stranger.post(
            "/v1/runs",
            json={"instruction": "x", "inputs": [{"kind": "asset", "asset_id": asset_id}]},
        ).status_code
        == 201
    )  # creating is fine; admission fails later as asset_not_found


def test_review_flow_over_http(
    make_services: ServicesFactory,
    unknown_envelope: Any,
    invoice_pdf: Path,
    invoice_schema: JsonSchema,
) -> None:
    svc = make_services(output=unknown_envelope)
    tenant = svc.auth.create_tenant("acme")
    _, token = svc.auth.create_key(tenant.id, "t")
    client = TestClient(create_app(svc), headers={"Authorization": f"Bearer {token}"})
    w = Worker(svc, lease_seconds=5, poll_s=0.02)
    w.start()
    try:
        asset_id = _upload(client, invoice_pdf)
        run_id = _create_run(client, asset_id, invoice_schema)["id"]
        result = _wait_rested(client, run_id)
        assert result["status"] == "needs_review"
        assert result["result"]["vendor_name"] is None

        assert client.get(f"/v1/reviews/{run_id}").status_code == 404
        opened = client.post(f"/v1/reviews/{run_id}/open", json={"assigned_to": "chetan"})
        assert opened.status_code == 200 and opened.headers["ETag"] == 'W/"1"'
        assert [r["run_id"] for r in client.get("/v1/reviews").json()] == [run_id]

        edited = client.post(
            f"/v1/reviews/{run_id}/edit",
            json={"changes": {"/vendor_name": "Northwind"}, "reviewer": "chetan"},
            headers={"If-Match": 'W/"1"'},
        )
        assert edited.status_code == 200 and edited.json()["version"] == 2
        stale = client.post(
            f"/v1/reviews/{run_id}/edit",
            json={"changes": {"/total": 1}},
            headers={"If-Match": '"1"'},
        )
        assert stale.status_code == 409 and stale.json()["code"] == "review_conflict"
        current = client.get(f"/v1/reviews/{run_id}/current").json()
        assert current["result"]["vendor_name"] == "Northwind"

        approved = client.post(
            f"/v1/reviews/{run_id}/approve",
            json={"reviewer": "chetan"},
            headers={"If-Match": '"2"'},
        )
        assert approved.status_code == 200 and approved.json()["kind"] == "reviewed"
        view = client.get(f"/v1/runs/{run_id}/result").json()
        assert view["status"] == "accepted" and view["result"]["vendor_name"] == "Northwind"
        assert view["revision_id"] == approved.json()["id"]
        kinds = [r["kind"] for r in client.get(f"/v1/runs/{run_id}/revisions").json()]
        assert kinds == ["machine", "reviewed"]
        assert (
            client.get("/v1/runs", params={"status": "accepted"}).json()["items"][0]["id"] == run_id
        )
    finally:
        w.stop()


def test_catalog_endpoints(client: TestClient) -> None:
    models = client.get("/v1/models").json()
    assert any(m["id"] == "anthropic/claude-opus-5-5" for m in models)
    tools = client.get("/v1/tools").json()
    assert any(t["name"] == "document.extract" for t in tools)
    assert client.get("/v1/skills").status_code == 200


def test_validation_errors_are_problems(client: TestClient) -> None:
    res = client.post("/v1/runs", json={"inputs": []})  # neither instruction nor skill
    assert res.status_code == 422
    body = res.json()
    assert body["code"] == "invalid_request" and body["errors"]
    assert json.dumps(body)  # serialisable
    assert client.get("/v1/runs", params={"cursor": "***"}).status_code == 400
    assert client.get("/v1/runs/run_01J9X3K4N7Q2M8R5T6V7W8X9Y0").status_code == 404


def test_result_not_ready_then_status_overlay(
    client: TestClient, invoice_pdf: Path, invoice_schema: JsonSchema
) -> None:
    asset_id = _upload(client, invoice_pdf)
    run = _create_run(client, asset_id, invoice_schema)
    assert run["status"] == RunStatus.queued.value
    summary = client.get(f"/v1/runs/{run['id']}").json()
    assert summary["status"] == "queued" and summary["cost_usd"] == 0
