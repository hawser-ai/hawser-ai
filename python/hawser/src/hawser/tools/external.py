"""External-facing tools: HTTP POST (side effect: external_write) and review requests."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import httpx

from hawser.errors import ToolError
from hawser.models import SideEffect
from hawser.models.tool import RetryClass, ToolLimits
from hawser.tools.registry import registry, tool

_client: httpx.Client | None = None


def set_http_client(client: httpx.Client | None) -> None:
    """Inject an HTTP client (tests use ``httpx.MockTransport``)."""
    global _client  # noqa: PLW0603
    _client = client


@tool(
    "http.post",
    description="POST a JSON body to a URL. External write: requires approval unless pre-approved.",
    side_effect=SideEffect.external_write,
    permissions=["network.egress"],
    input_schema={
        "type": "object",
        "properties": {
            "url": {"type": "string", "pattern": "^https?://"},
            "json": {},
            "headers": {"type": "object", "additionalProperties": {"type": "string"}},
        },
        "required": ["url", "json"],
        "additionalProperties": False,
    },
    output_schema={
        "type": "object",
        "properties": {"status": {"type": "integer"}, "action_id": {"type": "string"}, "body": {}},
        "required": ["status", "action_id"],
        "additionalProperties": False,
    },
)
def http_post(
    url: str, json: Any, headers: dict[str, str] | None = None, action_id: str = ""
) -> dict[str, Any]:
    host = urlparse(url).hostname or ""
    client = _client or httpx.Client(timeout=30)
    try:
        resp = client.post(
            url,
            json=json,
            headers={**(headers or {}), "Idempotency-Key": action_id, "X-Hawser-Action": action_id},
        )
    except httpx.HTTPError as exc:
        raise ToolError(f"POST {host} failed: {exc}", code="http_error", retryable=True) from exc
    finally:
        if _client is None:
            client.close()
    try:
        body: Any = resp.json()
    except ValueError:
        body = resp.text[:2000]
    if resp.status_code >= 400:
        raise ToolError(
            f"POST {host} returned HTTP {resp.status_code}",
            code=f"http_{resp.status_code}",
            retryable=resp.status_code >= 500,
            details={"body": body},
        )
    return {"status": resp.status_code, "action_id": action_id, "body": body}


# Harden the spec: external writes are retried only with a stable action id and must be quick.
_spec = registry.get("http.post").spec
_spec.retry = RetryClass.idempotent_with_key
_spec.idempotency = "keyed"
_spec.limits = ToolLimits(wall_s=60)


@tool(
    "review.request",
    description="Ask for human review of this run with a reason. Routes the run to needs_review.",
    output_schema={
        "type": "object",
        "properties": {"requested": {"type": "boolean"}, "reason": {"type": "string"}},
    },
)
def review_request(reason: str) -> dict[str, Any]:
    return {"requested": True, "reason": reason}


__all__ = ["http_post", "review_request", "set_http_client"]
