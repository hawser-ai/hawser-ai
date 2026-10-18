from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi.testclient import TestClient

from hawser.config import LOCAL_TENANT
from hawser.storage import SqlBackend
from hawser_server.webhooks import EVENTS, WebhookService, sign, verify


def test_signature_round_trip() -> None:
    body = b'{"event":"run.completed"}'
    header = sign("whsec_x", body)
    assert header.startswith("t=") and ",v1=" in header
    assert verify("whsec_x", body, header)
    assert not verify("whsec_y", body, header)
    assert not verify("whsec_x", body + b" ", header)
    assert not verify("whsec_x", body, "t=1,v1=00")
    assert not verify("whsec_x", body, "garbage")


class _Receiver:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json={"ok": True})


def _service(backend: SqlBackend, receiver: _Receiver, **kw: Any) -> WebhookService:
    return WebhookService(backend, transport=httpx.MockTransport(receiver.handler), **kw)


def test_fan_out_and_delivery(backend: SqlBackend) -> None:
    rx = _Receiver()
    svc = _service(backend, rx)
    hook, secret = svc.register(LOCAL_TENANT, "https://example.test/hook", ["run.completed"])
    assert hook.events == ["run.completed"]
    assert [h.id for h in svc.list(LOCAL_TENANT)] == [hook.id]

    assert svc.fan_out(LOCAL_TENANT, "run.needs_review", {"run_id": "r"}) == []  # not subscribed
    created = svc.fan_out(LOCAL_TENANT, "run.completed", {"run_id": "r", "status": "accepted"})
    assert len(created) == 1
    assert svc.deliver_due() == 1
    assert len(rx.requests) == 1
    req = rx.requests[0]
    assert req.headers["Hawser-Event"] == "run.completed"
    assert req.headers["Hawser-Delivery-Id"] == created[0].id
    assert verify(secret, req.content, req.headers["Hawser-Signature"])
    payload = json.loads(req.content)
    assert payload["run_id"] == "r" and payload["delivery_id"] == created[0].id
    deliveries = svc.deliveries(LOCAL_TENANT, hook.id)
    assert deliveries[0].state == "delivered" and deliveries[0].last_status == 200
    assert svc.deliver_due() == 0  # nothing left


def test_failed_delivery_retries_then_dead_letters(backend: SqlBackend) -> None:
    rx = _Receiver(status=500)
    svc = _service(backend, rx, max_age_s=0)
    hook, _ = svc.register(LOCAL_TENANT, "https://example.test/hook", [])
    assert hook.events == list(EVENTS)
    svc.fan_out(LOCAL_TENANT, "run.completed", {"run_id": "r"})
    assert svc.deliver_due() == 1
    d = svc.deliveries(LOCAL_TENANT, hook.id)[0]
    assert d.state == "failed" and d.attempts == 1 and d.last_status == 500
    assert svc.deliveries(LOCAL_TENANT, hook.id, state="failed")[0].id == d.id

    # with a generous max age the delivery stays pending with backoff
    rx2 = _Receiver(status=503)
    svc2 = _service(backend, rx2, max_age_s=3600)
    svc2.fan_out(LOCAL_TENANT, "run.completed", {"run_id": "r2"})
    assert svc2.deliver_due() == 1
    pending = svc2.deliveries(LOCAL_TENANT, hook.id, state="pending")[0]
    assert pending.attempts == 1 and pending.next_attempt_at > pending.created_at
    assert svc2.deliver_due() == 0  # not due yet
    with backend.transaction(write=True) as tx:
        tx.execute(
            "UPDATE webhook_deliveries SET next_attempt_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00.000000+00:00", pending.id),
        )
    rx2.status = 200
    assert svc2.deliver_due() == 1
    assert svc2.deliveries(LOCAL_TENANT, hook.id, state="delivered")[0].attempts == 2

    assert svc.delete(LOCAL_TENANT, hook.id)
    assert svc.list(LOCAL_TENANT) == []


def test_webhook_api(client: TestClient) -> None:
    assert client.get("/v1/webhooks/events").json() == list(EVENTS)
    bad = client.post("/v1/webhooks", json={"url": "https://x.test/h", "events": ["nope"]})
    assert bad.status_code == 400 and bad.json()["code"] == "unknown_webhook_event"
    created = client.post("/v1/webhooks", json={"url": "https://x.test/h", "events": []})
    assert created.status_code == 201
    body = created.json()
    assert body["secret"].startswith("whsec_") and body["webhook"]["events"] == list(EVENTS)
    hook_id = body["webhook"]["id"]
    assert [h["id"] for h in client.get("/v1/webhooks").json()] == [hook_id]
    assert client.get(f"/v1/webhooks/{hook_id}/deliveries").json() == []
    assert client.delete(f"/v1/webhooks/{hook_id}").status_code == 204
    assert client.get("/v1/webhooks").json() == []
    assert client.get(f"/v1/webhooks/{hook_id}/deliveries").status_code == 404
