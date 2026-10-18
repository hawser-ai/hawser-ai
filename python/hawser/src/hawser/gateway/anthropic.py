"""Anthropic adapter on the official SDK. Structured output via ``output_config.format``."""

from __future__ import annotations

import base64
import json
from typing import Any

from hawser.clock import monotonic_ms
from hawser.errors import ProviderError
from hawser.gateway.base import DocumentPart, ImagePart, ModelRequest, ModelResponse, TextPart
from hawser.models import ModelCallUsage, ModelSpec

_RETRYABLE_STATUS = {408, 409, 429}


class AnthropicAdapter:
    provider = "anthropic"

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            import anthropic  # noqa: PLC0415 - lazy: replay mode never needs the SDK

            try:
                # Resolves ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / an `ant auth login` profile.
                # The SDK retries 429/5xx itself; the gateway adds policy-aware retries on top.
                self._client = anthropic.Anthropic(max_retries=0)
            except anthropic.AnthropicError as exc:
                raise ProviderError(
                    "no Anthropic credentials found; set ANTHROPIC_API_KEY or run `ant auth login`",
                    code="missing_credentials",
                    retryable=False,
                ) from exc
        return self._client

    @staticmethod
    def _content(parts: list[TextPart | ImagePart | DocumentPart]) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for p in parts:
            if isinstance(p, TextPart):
                blocks.append({"type": "text", "text": p.text})
            elif isinstance(p, ImagePart):
                blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": p.media_type,
                            "data": base64.standard_b64encode(p.data).decode("ascii"),
                        },
                    }
                )
            else:
                blocks.append(
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": p.media_type,
                            "data": base64.standard_b64encode(p.data).decode("ascii"),
                        },
                    }
                )
        return blocks

    def build_kwargs(self, spec: ModelSpec, request: ModelRequest) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": spec.model,
            "max_tokens": min(request.max_output_tokens, spec.max_output_tokens),
            "messages": [
                {"role": m.role, "content": self._content(m.parts)} for m in request.messages
            ],
        }
        if request.system:
            kwargs["system"] = request.system
        output_config: dict[str, Any] = {}
        if request.output_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": request.output_schema}
        if request.effort:
            output_config["effort"] = request.effort
        if output_config:
            kwargs["output_config"] = output_config
        return kwargs

    def complete(self, spec: ModelSpec, request: ModelRequest) -> ModelResponse:
        import anthropic  # noqa: PLC0415

        client = self._get_client()
        kwargs = self.build_kwargs(spec, request)
        started = monotonic_ms()
        try:
            resp = client.messages.create(**kwargs)
        except anthropic.RateLimitError as exc:
            retry_after = exc.response.headers.get("retry-after")
            raise ProviderError(
                "rate limited",
                code="rate_limited",
                retryable=True,
                details={"retry_after": retry_after},
            ) from exc
        except anthropic.APITimeoutError as exc:
            raise ProviderError("request timed out", code="timeout", retryable=True) from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError(
                f"connection error: {exc}", code="connection", retryable=True
            ) from exc
        except TypeError as exc:
            # anthropic 1.x raises TypeError at request time when no credential resolves.
            if "authentication" not in str(exc).lower():
                raise
            raise ProviderError(
                "no Anthropic credentials found; set ANTHROPIC_API_KEY or run `ant auth login`",
                code="missing_credentials",
                retryable=False,
            ) from exc
        except anthropic.APIStatusError as exc:
            status = int(exc.status_code)
            raise ProviderError(
                f"provider returned HTTP {status}: {exc.message}",
                code=f"http_{status}",
                retryable=status >= 500 or status in _RETRYABLE_STATUS,
                details={"status": status},
            ) from exc
        latency = monotonic_ms() - started

        if resp.stop_reason == "refusal":
            details: dict[str, Any] = {}
            sd = getattr(resp, "stop_details", None)
            if sd is not None:
                details = {
                    "category": getattr(sd, "category", None),
                    "explanation": getattr(sd, "explanation", None),
                }
            raise ProviderError(
                "the model declined this request",
                code="model_refusal",
                retryable=False,
                details=details,
            )

        text = (
            "".join(block.text for block in resp.content if getattr(block, "type", None) == "text")
            or None
        )
        parsed: Any | None = None
        parse_error: str | None = None
        if request.output_schema is not None and text is not None:
            try:
                parsed = json.loads(text)
            except ValueError as exc:
                parse_error = str(exc)

        u = resp.usage
        usage = ModelCallUsage(
            provider=self.provider,
            model=spec.model,
            input_tokens=int(u.input_tokens),
            output_tokens=int(u.output_tokens),
            cache_read_tokens=int(getattr(u, "cache_read_input_tokens", 0) or 0),
            cache_write_tokens=int(getattr(u, "cache_creation_input_tokens", 0) or 0),
            cost_usd=0.0,  # the gateway prices it
            latency_ms=latency,
            provider_request_id=getattr(resp, "_request_id", None),
        )
        return ModelResponse(
            text=text,
            parsed=parsed,
            parse_error=parse_error,
            usage=usage,
            stop_reason=resp.stop_reason,
            truncated=resp.stop_reason == "max_tokens",
            raw=resp.to_dict() if hasattr(resp, "to_dict") else None,
        )


__all__ = ["AnthropicAdapter"]
