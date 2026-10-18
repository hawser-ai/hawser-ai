"""Signed webhooks with retries and a dead-letter list (blueprint §22, §24).

Delivery rows are created when a run rests or a reviewer decides; the worker sends them.
``Hawser-Signature: t=<unix ts>,v1=<hex hmac-sha256(secret, "<t>.<body>")>``; consumers dedup
on ``Hawser-Delivery-Id``.
"""

from __future__ import annotations

import hmac
import json
import secrets
import time
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

import httpx

from hawser.ids import new_id
from hawser.storage.sql import SqlBackend
from hawser_server.util import parse_ts, ts, ts_after

EVENTS = (
    "run.completed",
    "run.needs_review",
    "run.awaiting_approval",
    "review.decided",
)


@dataclass(frozen=True)
class Webhook:
    id: str
    tenant_id: str
    url: str
    events: list[str]
    active: bool
    created_at: str


@dataclass(frozen=True)
class Delivery:
    id: str
    tenant_id: str
    webhook_id: str
    run_id: str | None
    event: str
    payload: dict[str, Any]
    state: str
    attempts: int
    next_attempt_at: str
    last_status: int | None
    last_error: str | None
    created_at: str
    delivered_at: str | None


def sign(secret: str, body: bytes, *, at: int | None = None) -> str:
    t = int(time.time()) if at is None else at
    mac = hmac.new(secret.encode(), f"{t}.".encode() + body, sha256).hexdigest()
    return f"t={t},v1={mac}"


def verify(secret: str, body: bytes, header: str, *, tolerance_s: int = 300) -> bool:
    """Consumer-side check: signature matches and the timestamp is recent."""
    parts = dict(p.split("=", 1) for p in header.split(",") if "=" in p)
    try:
        t = int(parts.get("t", ""))
    except ValueError:
        return False
    if abs(time.time() - t) > tolerance_s:
        return False
    expected = sign(secret, body, at=t).split("v1=", 1)[1]
    return hmac.compare_digest(expected, parts.get("v1", ""))


def _backoff(attempts: int) -> float:
    return min(10.0 * (2 ** max(attempts - 1, 0)), 3600.0)


def _delivery(row: tuple[object, ...]) -> Delivery:
    return Delivery(
        id=str(row[0]),
        tenant_id=str(row[1]),
        webhook_id=str(row[2]),
        run_id=None if row[3] is None else str(row[3]),
        event=str(row[4]),
        payload=json.loads(str(row[5])),
        state=str(row[6]),
        attempts=int(row[7]),  # type: ignore[arg-type]
        next_attempt_at=str(row[8]),
        last_status=None if row[9] is None else int(row[9]),  # type: ignore[arg-type]
        last_error=None if row[10] is None else str(row[10]),
        created_at=str(row[11]),
        delivered_at=None if row[12] is None else str(row[12]),
    )


_DCOLS = (
    "id, tenant_id, webhook_id, run_id, event, payload, state, attempts, next_attempt_at,"
    " last_status, last_error, created_at, delivered_at"
)


class WebhookService:
    def __init__(
        self,
        backend: SqlBackend,
        *,
        timeout_s: float = 10.0,
        max_age_s: int = 24 * 3600,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.backend = backend
        self.timeout_s = timeout_s
        self.max_age_s = max_age_s
        self._client = httpx.Client(timeout=timeout_s, transport=transport)

    # -- registration ----------------------------------------------------------------------
    def register(self, tenant_id: str, url: str, events: list[str]) -> tuple[Webhook, str]:
        unknown = sorted(set(events) - set(EVENTS))
        if unknown:
            raise ValueError(f"unknown webhook events: {', '.join(unknown)}")
        hook = Webhook(
            id=new_id("whk"),
            tenant_id=tenant_id,
            url=url,
            events=sorted(set(events)) or list(EVENTS),
            active=True,
            created_at=ts(),
        )
        secret = "whsec_" + secrets.token_urlsafe(24)
        with self.backend.transaction(write=True) as tx:
            tx.execute(
                "INSERT INTO webhooks (id, tenant_id, url, events, secret, active, created_at)"
                " VALUES (?,?,?,?,?,1,?)",
                (hook.id, tenant_id, url, json.dumps(hook.events), secret, hook.created_at),
            )
        return hook, secret

    def list(self, tenant_id: str) -> list[Webhook]:
        with self.backend.transaction() as tx:
            rows = tx.execute(
                "SELECT id, tenant_id, url, events, active, created_at FROM webhooks"
                " WHERE tenant_id = ? AND active = 1 ORDER BY created_at",
                (tenant_id,),
            )
        return [
            Webhook(
                id=str(r[0]),
                tenant_id=str(r[1]),
                url=str(r[2]),
                events=json.loads(str(r[3])),
                active=bool(int(r[4])),  # type: ignore[arg-type]
                created_at=str(r[5]),
            )
            for r in rows
        ]

    def get(self, tenant_id: str, webhook_id: str) -> Webhook | None:
        return next((h for h in self.list(tenant_id) if h.id == webhook_id), None)

    def delete(self, tenant_id: str, webhook_id: str) -> bool:
        with self.backend.transaction(write=True) as tx:
            tx.execute(
                "UPDATE webhooks SET active = 0 WHERE id = ? AND tenant_id = ?",
                (webhook_id, tenant_id),
            )
            rows = tx.execute(
                "SELECT 1 FROM webhooks WHERE id = ? AND tenant_id = ? AND active = 0",
                (webhook_id, tenant_id),
            )
        return bool(rows)

    # -- fan-out ---------------------------------------------------------------------------
    def fan_out(
        self, tenant_id: str, event: str, payload: dict[str, Any], *, run_id: str | None = None
    ) -> list[Delivery]:
        """Create one pending delivery per active webhook subscribed to ``event``."""
        created: list[Delivery] = []
        body = {"event": event, "at": ts(), **payload}
        for hook in self.list(tenant_id):
            if event not in hook.events:
                continue
            d = Delivery(
                id=new_id("dlv"),
                tenant_id=tenant_id,
                webhook_id=hook.id,
                run_id=run_id,
                event=event,
                payload={**body, "delivery_id": ""},
                state="pending",
                attempts=0,
                next_attempt_at=ts(),
                last_status=None,
                last_error=None,
                created_at=ts(),
                delivered_at=None,
            )
            payload_json = json.dumps({**body, "delivery_id": d.id}, sort_keys=True)
            with self.backend.transaction(write=True) as tx:
                tx.execute(
                    "INSERT INTO webhook_deliveries (id, tenant_id, webhook_id, run_id, event,"
                    " payload, state, attempts, next_attempt_at, created_at)"
                    " VALUES (?,?,?,?,?,?,'pending',0,?,?)",
                    (
                        d.id,
                        tenant_id,
                        hook.id,
                        run_id,
                        event,
                        payload_json,
                        d.next_attempt_at,
                        d.created_at,
                    ),
                )
            created.append(d)
        return created

    def deliveries(
        self, tenant_id: str, webhook_id: str, *, state: str | None = None, limit: int = 100
    ) -> list[Delivery]:
        sql = f"SELECT {_DCOLS} FROM webhook_deliveries WHERE tenant_id = ? AND webhook_id = ?"
        params: list[Any] = [tenant_id, webhook_id]
        if state is not None:
            sql += " AND state = ?"
            params.append(state)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self.backend.transaction() as tx:
            rows = tx.execute(sql, params)
        return [_delivery(r) for r in rows]

    # -- sending ---------------------------------------------------------------------------
    def _claim_due(self, limit: int = 20) -> list[Delivery]:
        now = ts()
        claimed: list[Delivery] = []
        with self.backend.transaction(write=True) as tx:
            rows = tx.execute(
                f"SELECT {_DCOLS} FROM webhook_deliveries WHERE state = 'pending'"
                " AND next_attempt_at <= ? ORDER BY next_attempt_at LIMIT ?"
                + self.backend.skip_locked,
                (now, limit),
            )
            for r in rows:
                tx.execute(
                    "UPDATE webhook_deliveries SET state = 'sending'"
                    " WHERE id = ? AND state = 'pending'",
                    (r[0],),
                )
                claimed.append(_delivery(r))
        return claimed

    def deliver_due(self) -> int:
        """Send every due delivery once; returns how many were attempted."""
        sent = 0
        for d in self._claim_due():
            self._attempt(d)
            sent += 1
        return sent

    def _secret(self, webhook_id: str) -> str | None:
        with self.backend.transaction() as tx:
            rows = tx.execute("SELECT secret, url FROM webhooks WHERE id = ?", (webhook_id,))
        return None if not rows else str(rows[0][0])

    def _url(self, webhook_id: str) -> str | None:
        with self.backend.transaction() as tx:
            rows = tx.execute("SELECT url FROM webhooks WHERE id = ?", (webhook_id,))
        return None if not rows else str(rows[0][0])

    def _attempt(self, d: Delivery) -> None:
        secret, url = self._secret(d.webhook_id), self._url(d.webhook_id)
        attempts = d.attempts + 1
        status: int | None = None
        error: str | None = None
        if secret is None or url is None:
            error = "webhook removed"
        else:
            body = json.dumps({**d.payload, "delivery_id": d.id}, sort_keys=True).encode()
            try:
                resp = self._client.post(
                    url,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "hawserd-webhooks/1",
                        "Hawser-Delivery-Id": d.id,
                        "Hawser-Event": d.event,
                        "Hawser-Signature": sign(secret, body),
                    },
                )
                status = resp.status_code
                if 200 <= status < 300:
                    with self.backend.transaction(write=True) as tx:
                        tx.execute(
                            "UPDATE webhook_deliveries SET state = 'delivered', attempts = ?,"
                            " last_status = ?, last_error = NULL, delivered_at = ? WHERE id = ?",
                            (attempts, status, ts(), d.id),
                        )
                    return
                error = f"HTTP {status}"
            except httpx.HTTPError as exc:
                error = f"{type(exc).__name__}: {exc}"
        age = (time.time() - parse_ts(d.created_at).timestamp()) if d.created_at else 0.0
        dead = age > self.max_age_s or error == "webhook removed"
        with self.backend.transaction(write=True) as tx:
            tx.execute(
                "UPDATE webhook_deliveries SET state = ?, attempts = ?, last_status = ?,"
                " last_error = ?, next_attempt_at = ? WHERE id = ?",
                (
                    "failed" if dead else "pending",
                    attempts,
                    status,
                    (error or "")[:500],
                    ts_after(_backoff(attempts)),
                    d.id,
                ),
            )

    def close(self) -> None:
        self._client.close()


__all__ = ["EVENTS", "Delivery", "Webhook", "WebhookService", "sign", "verify"]
