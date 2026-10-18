"""Adapter compatibility kit (blueprint §9, plan WP3.4).

Third-party adapters run :func:`compatibility_checks` to prove they honour the gateway
contract: request shapes, structured-output round trip, image parts, usage fields, error
mapping. ``hawser models check <id>`` runs the live subset against a registry model.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from PIL import Image

from hawser.errors import ProviderError
from hawser.gateway.base import (
    ImagePart,
    Message,
    ModelAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
)
from hawser.models import ModelSpec

PING_SCHEMA = {
    "type": "object",
    "properties": {"reply": {"type": "string"}, "number": {"type": "integer"}},
    "required": ["reply", "number"],
    "additionalProperties": False,
}


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buf, format="PNG")
    return buf.getvalue()


def _usage_ok(resp: ModelResponse) -> str | None:
    u = resp.usage
    if u.input_tokens <= 0 or u.output_tokens <= 0:
        return f"usage tokens not reported (in={u.input_tokens}, out={u.output_tokens})"
    if u.latency_ms < 0:
        return "negative latency"
    return None


def compatibility_checks(
    adapter: ModelAdapter,
    spec: ModelSpec,
    *,
    live: bool = True,
    fault: Callable[[], None] | None = None,
) -> list[CheckResult]:
    """Run the contract checks. ``fault`` (optional) makes the adapter raise once, to verify
    that errors surface as ``ProviderError`` with a retryable flag."""
    results: list[CheckResult] = []

    def run(name: str, fn: Callable[[], str | None]) -> None:
        try:
            detail = fn()
        except Exception as exc:
            results.append(CheckResult(name, False, f"{type(exc).__name__}: {exc}"))
            return
        results.append(CheckResult(name, detail is None, detail or "ok"))

    if live:

        def text_only() -> str | None:
            resp = adapter.complete(
                spec,
                ModelRequest(
                    model=spec.id,
                    messages=[
                        Message(
                            role="user", parts=[TextPart(text="Reply with the single word: pong")]
                        )
                    ],
                    max_output_tokens=64,
                ),
            )
            if not resp.text or "pong" not in resp.text.lower():
                return f"unexpected text {resp.text!r}"
            return _usage_ok(resp)

        run("text_only", text_only)

        if spec.structured_output:

            def structured() -> str | None:
                resp = adapter.complete(
                    spec,
                    ModelRequest(
                        model=spec.id,
                        messages=[
                            Message(
                                role="user",
                                parts=[TextPart(text="Set reply to 'pong' and number to 7.")],
                            )
                        ],
                        output_schema=PING_SCHEMA,
                        max_output_tokens=200,
                    ),
                )
                if not isinstance(resp.parsed, dict):
                    return f"no parsed JSON (parse_error={resp.parse_error!r})"
                if resp.parsed.get("number") != 7:  # type: ignore[union-attr]
                    return f"schema not followed: {resp.parsed}"
                return _usage_ok(resp)

            run("structured_output", structured)

        if any(m.value == "image" for m in spec.modalities):

            def image() -> str | None:
                resp = adapter.complete(
                    spec,
                    ModelRequest(
                        model=spec.id,
                        messages=[
                            Message(
                                role="user",
                                parts=[
                                    ImagePart(media_type="image/png", data=_png()),
                                    TextPart(text="What colour is this image? One word."),
                                ],
                            )
                        ],
                        max_output_tokens=64,
                    ),
                )
                return None if resp.text else "no text for image request"

            run("image_part", image)

    if fault is not None:

        def error_mapping() -> str | None:
            fault()
            try:
                adapter.complete(
                    spec,
                    ModelRequest(
                        model=spec.id, messages=[Message(role="user", parts=[TextPart(text="x")])]
                    ),
                )
            except ProviderError as exc:
                return None if exc.code else "ProviderError without code"
            return "fault did not surface as ProviderError"

        run("error_mapping", error_mapping)

    return results


def summarize(results: list[CheckResult]) -> dict[str, Any]:
    return {
        "passed": sum(1 for r in results if r.passed),
        "failed": [r.name for r in results if not r.passed],
        "checks": [r.__dict__ for r in results],
    }


__all__ = ["PING_SCHEMA", "CheckResult", "compatibility_checks", "summarize"]
