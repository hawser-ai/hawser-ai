from __future__ import annotations

from fastapi import APIRouter, Response

from hawser_server.deps import PrincipalDep, ServicesDep
from hawser_server.errors import BadRequest, NotFound
from hawser_server.schemas import DeliveryOut, WebhookCreate, WebhookCreated, WebhookOut
from hawser_server.webhooks import EVENTS, Delivery, Webhook

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])


def _out(h: Webhook) -> WebhookOut:
    return WebhookOut(id=h.id, url=h.url, events=h.events, active=h.active, created_at=h.created_at)


def _delivery(d: Delivery) -> DeliveryOut:
    return DeliveryOut(
        id=d.id,
        webhook_id=d.webhook_id,
        run_id=d.run_id,
        event=d.event,
        state=d.state,
        attempts=d.attempts,
        next_attempt_at=d.next_attempt_at,
        last_status=d.last_status,
        last_error=d.last_error,
        created_at=d.created_at,
        delivered_at=d.delivered_at,
    )


@router.get("/events", response_model=list[str])
def list_events(who: PrincipalDep) -> list[str]:
    return list(EVENTS)


@router.post("", response_model=WebhookCreated, status_code=201)
def create_webhook(
    svc: ServicesDep, who: PrincipalDep, body: WebhookCreate, response: Response
) -> WebhookCreated:
    try:
        hook, secret = svc.webhooks.register(who.tenant_id, body.url, body.events)
    except ValueError as exc:
        raise BadRequest(str(exc), code="unknown_webhook_event") from exc
    response.headers["Location"] = f"/v1/webhooks/{hook.id}"
    return WebhookCreated(webhook=_out(hook), secret=secret)


@router.get("", response_model=list[WebhookOut])
def list_webhooks(svc: ServicesDep, who: PrincipalDep) -> list[WebhookOut]:
    return [_out(h) for h in svc.webhooks.list(who.tenant_id)]


@router.delete("/{webhook_id}", status_code=204, response_class=Response)
def delete_webhook(svc: ServicesDep, who: PrincipalDep, webhook_id: str) -> Response:
    if not svc.webhooks.delete(who.tenant_id, webhook_id):
        raise NotFound(f"unknown webhook {webhook_id}", code="webhook_unknown")
    return Response(status_code=204)


@router.get("/{webhook_id}/deliveries", response_model=list[DeliveryOut])
def list_deliveries(
    svc: ServicesDep, who: PrincipalDep, webhook_id: str, state: str | None = None
) -> list[DeliveryOut]:
    """Delivery attempts, newest first. ``state=failed`` is the dead-letter list."""
    if svc.webhooks.get(who.tenant_id, webhook_id) is None:
        raise NotFound(f"unknown webhook {webhook_id}", code="webhook_unknown")
    return [_delivery(d) for d in svc.webhooks.deliveries(who.tenant_id, webhook_id, state=state)]
