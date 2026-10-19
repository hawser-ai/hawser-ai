"""Endpoints added for the web app: pages, evals, policy, schemas, usage, filters, static."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from hawser.models.common import JsonSchema
from hawser_server.app import create_app
from hawser_server.services import Services
from hawser_server.testing import ServicesFactory, wait_for
from hawser_server.worker import Worker

REPORT = {
    "id": "eval_01J9X3K4N7Q2M8R5T6V7W8X9Y0",
    "config": {
        "dataset": "datasets/invoice-po/v1",
        "skill": "invoice-po-reconciliation",
        "skill_version": "1.0.0",
        "mode": "workflow",
        "model": "openrouter/anthropic/claude-haiku-5.5",
        "registry_version": "x",
        "record_mode": "replay",
        "started_at": "2026-10-18T00:00:00+00:00",
    },
    "cases": [
        {"case_id": "pair-01000", "status": "accepted", "correct": 10, "total": 10, "passed": True},
        {
            "case_id": "pair-01001",
            "status": "needs_review",
            "correct": 9,
            "total": 10,
            "passed": False,
        },
    ],
    "metrics": {"cases": 2, "field_accuracy": 0.95, "cost_total_usd": 0.004},
    "clusters": [{"field": "/total", "outcome": "mismatch", "count": 1, "cases": ["pair-01001"]}],
    "finished_at": "2026-10-18T00:01:00+00:00",
}


def _upload(client: TestClient, pdf: Path) -> str:
    with pdf.open("rb") as fh:
        res = client.post("/v1/assets", files={"file": ("invoice.pdf", fh, "application/pdf")})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def test_page_render(client: TestClient, invoice_pdf: Path) -> None:
    asset_id = _upload(client, invoice_pdf)
    res = client.get(f"/v1/assets/{asset_id}/pages/1", params={"dpi": 72})
    assert res.status_code == 200 and res.headers["content-type"] == "image/png"
    assert res.headers["X-Hawser-Dpi"] == "72.0" and res.content[:8] == b"\x89PNG\r\n\x1a\n"
    again = client.get(f"/v1/assets/{asset_id}/pages/1", params={"dpi": 72})
    assert again.content == res.content  # cached
    assert client.get(f"/v1/assets/{asset_id}/pages/9").status_code == 404
    assert client.get(f"/v1/assets/{asset_id}/pages/1", params={"dpi": 10}).status_code == 422


def test_evals_publish_list_get_delete(client: TestClient) -> None:
    bad = client.post("/v1/evals", json={"report": {"nope": 1}})
    assert bad.status_code == 400 and bad.json()["code"] == "bad_report"
    created = client.post("/v1/evals", json={"report": REPORT, "name": "haiku smoke"})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == "haiku smoke" and body["cases"] == 2
    assert body["metrics"]["field_accuracy"] == 0.95
    assert body["skill"] == "invoice-po-reconciliation"
    listed = client.get("/v1/evals").json()
    assert [e["id"] for e in listed] == [body["id"]]
    full = client.get(f"/v1/evals/{body['id']}/report").json()
    assert full["cases"][1]["status"] == "needs_review" and full["clusters"][0]["field"] == "/total"
    assert client.get(f"/v1/evals/{body['id']}").json()["artifact_id"] == body["artifact_id"]
    assert client.delete(f"/v1/evals/{body['id']}").status_code == 204
    assert client.get("/v1/evals").json() == []
    assert client.get(f"/v1/evals/{body['id']}").status_code == 404


def test_policy_and_schemas(client: TestClient) -> None:
    assert client.get("/v1/policy").json()["review_on_failure"] is True
    res = client.put(
        "/v1/policy", json={"limits": {"max_cost_usd": 0.5}, "review_on_failure": False}
    )
    assert res.status_code == 200
    stored = client.get("/v1/policy").json()
    assert stored["limits"]["max_cost_usd"] == 0.5 and stored["review_on_failure"] is False
    bad = client.put("/v1/policy", json={"limits": {"max_cost_usd": -1}})
    assert bad.status_code == 422
    names = client.get("/v1/schemas").json()
    assert "Policy" in names and "RunResult" in names
    schema = client.get("/v1/schemas/Policy").json()
    assert schema["title"] == "Policy" and "properties" in schema
    assert client.get("/v1/schemas/Nope").status_code == 404


def test_run_filters_and_usage_breakdown(
    client: TestClient, worker: Worker, invoice_pdf: Path, invoice_schema: JsonSchema
) -> None:
    asset_id = _upload(client, invoice_pdf)
    body: dict[str, Any] = {
        "instruction": "Extract the invoice.",
        "inputs": [{"kind": "asset", "asset_id": asset_id}],
        "output_schema": invoice_schema,
        "metadata": {"batch": "october"},
    }
    run_id = client.post("/v1/runs", json=body).json()["id"]
    wait_for(lambda: client.get(f"/v1/runs/{run_id}").json()["status"] == "accepted")
    req = client.get(f"/v1/runs/{run_id}/request").json()
    assert req["instruction"] == "Extract the invoice." and req["metadata"] == {"batch": "october"}
    assert [r["id"] for r in client.get("/v1/runs", params={"q": "octo"}).json()["items"]] == [
        run_id
    ]
    assert client.get("/v1/runs", params={"q": "nomatch"}).json()["items"] == []
    assert client.get("/v1/runs", params={"skill": "other"}).json()["items"] == []
    assert client.get("/v1/runs", params={"mode": "direct"}).json()["items"] == []  # mode unset

    br = client.get("/v1/usage/breakdown").json()
    assert br["by_status"][0]["key"] == "accepted" and br["by_status"][0]["runs"] == 1
    assert br["by_skill"][0]["key"] == "(none)" and len(br["by_day"]) == 1
    assert br["runs"][0]["id"] == run_id and br["runs"][0]["model_calls"] == 1
    assert client.get("/v1/usage/breakdown", params={"since": "2999-01-01"}).json()["runs"] == []


def test_quickstart_example(client: TestClient) -> None:
    res = client.post("/v1/examples/quickstart")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["asset"]["media_class"] == "pdf" and body["instruction"] == "Extract the invoice."
    assert "invoice_number" in body["output_schema"]["properties"]
    assert body["policy"]["limits"]["max_cost_usd"] == 0.25
    again = client.post("/v1/examples/quickstart").json()
    assert again["asset"]["id"] == body["asset"]["id"]  # content-addressed: same asset


def test_static_web_app_mount(make_services: ServicesFactory, tmp_path: Path) -> None:
    web = tmp_path / "dist"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<html><body>hawser app</body></html>")
    (web / "assets" / "app.js").write_text("console.log(1)")
    svc = make_services(output={}, web_dir=web)
    client = TestClient(create_app(svc))
    assert client.get("/", follow_redirects=False).status_code == 307
    assert "hawser app" in client.get("/app/").text
    assert "hawser app" in client.get("/app/runs/run_x").text  # SPA fallback
    assert client.get("/app/assets/app.js").text == "console.log(1)"
    escaped = client.get("/app/../etc/passwd")  # never escapes the root
    assert escaped.status_code in (200, 404) and "root:" not in escaped.text
    svc_none: Services = make_services(output={}, web_dir=tmp_path / "missing")
    assert TestClient(create_app(svc_none)).get("/app/").status_code == 404
    cors = client.options(
        "/v1/runs",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert cors.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert json.dumps(svc.config.cors_origins)
