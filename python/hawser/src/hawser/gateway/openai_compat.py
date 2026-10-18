"""OpenAI-compatible chat-completions adapter: OpenRouter, OpenAI, Ollama, vLLM, LM Studio …

One adapter class, several *profiles* (base URL, credential env var, which structured-output
and reasoning parameters the endpoint understands). Raw HTTP via ``httpx`` keeps the request
shape fully under our control and avoids a second provider SDK.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any, Literal

import httpx

from hawser.clock import monotonic_ms
from hawser.errors import ProviderError
from hawser.gateway.base import DocumentPart, ImagePart, ModelRequest, ModelResponse, TextPart
from hawser.models import ModelCallUsage, ModelSpec

_RETRYABLE_STATUS = {408, 409, 429}

StructuredMode = Literal["json_schema", "json_object", "none"]
ReasoningParam = Literal["openrouter", "openai", "none"]


class OpenAICompatibleAdapter:
    def __init__(
        self,
        provider: str,
        base_url: str,
        *,
        api_key_env: str | None,
        structured_mode: StructuredMode = "json_schema",
        reasoning_param: ReasoningParam = "none",
        supports_files: bool = False,
        extra_headers: dict[str, str] | None = None,
        extra_body: dict[str, Any] | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.structured_mode: StructuredMode = structured_mode
        self.reasoning_param: ReasoningParam = reasoning_param
        self.supports_files = supports_files
        self.extra_headers = extra_headers or {}
        self.extra_body = extra_body or {}
        self._client = client

    # -- profiles ----------------------------------------------------------------------------
    @classmethod
    def openrouter(cls) -> OpenAICompatibleAdapter:
        return cls(
            "openrouter",
            "https://openrouter.ai/api/v1",
            api_key_env="OPENROUTER_API_KEY",
            structured_mode="json_schema",
            reasoning_param="openrouter",
            supports_files=True,
            extra_headers={
                "HTTP-Referer": "https://github.com/hawser-ai/hawser",
                "X-Title": "Hawser",
            },
            extra_body={"usage": {"include": True}},
        )

    @classmethod
    def openai(cls) -> OpenAICompatibleAdapter:
        return cls(
            "openai",
            "https://api.openai.com/v1",
            api_key_env="OPENAI_API_KEY",
            structured_mode="json_schema",
            reasoning_param="openai",
        )

    @classmethod
    def ollama(cls, base_url: str = "http://localhost:11434/v1") -> OpenAICompatibleAdapter:
        return cls("ollama", base_url, api_key_env=None, structured_mode="json_schema")

    @classmethod
    def vllm(cls, base_url: str) -> OpenAICompatibleAdapter:
        return cls("vllm", base_url, api_key_env="VLLM_API_KEY", structured_mode="json_schema")

    # -- request building --------------------------------------------------------------------
    def _content(self, parts: list[TextPart | ImagePart | DocumentPart]) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for p in parts:
            if isinstance(p, TextPart):
                blocks.append({"type": "text", "text": p.text})
            elif isinstance(p, ImagePart):
                data = base64.standard_b64encode(p.data).decode("ascii")
                blocks.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{p.media_type};base64,{data}"},
                    }
                )
            else:
                if not self.supports_files:
                    raise ProviderError(
                        f"provider {self.provider!r} does not accept document parts",
                        code="document_parts_unsupported",
                        retryable=False,
                    )
                data = base64.standard_b64encode(p.data).decode("ascii")
                blocks.append(
                    {
                        "type": "file",
                        "file": {
                            "filename": p.label or "document.pdf",
                            "file_data": f"data:{p.media_type};base64,{data}",
                        },
                    }
                )
        return blocks

    def build_body(self, spec: ModelSpec, request: ModelRequest) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        if request.system:
            messages.append({"role": "system", "content": request.system})
        for m in request.messages:
            messages.append({"role": m.role, "content": self._content(m.parts)})
        body: dict[str, Any] = {
            "model": spec.model,
            "messages": messages,
            "max_tokens": min(request.max_output_tokens, spec.max_output_tokens),
        }
        if request.output_schema is not None:
            if self.structured_mode == "json_schema":
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "hawser_output",
                        "strict": True,
                        "schema": request.output_schema,
                    },
                }
            elif self.structured_mode == "json_object":
                body["response_format"] = {"type": "json_object"}
        if request.effort:
            if self.reasoning_param == "openrouter":
                body["reasoning"] = {"effort": request.effort}
            elif self.reasoning_param == "openai":
                body["reasoning_effort"] = request.effort
        body.update(self.extra_body)
        return body

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", **self.extra_headers}
        if self.api_key_env:
            key = os.environ.get(self.api_key_env)
            if not key:
                raise ProviderError(
                    f"no credentials for provider {self.provider!r}; set {self.api_key_env}",
                    code="missing_credentials",
                    retryable=False,
                )
            headers["Authorization"] = f"Bearer {key}"
        return headers

    # -- call --------------------------------------------------------------------------------
    def complete(  # noqa: PLR0912, PLR0915
        self, spec: ModelSpec, request: ModelRequest
    ) -> ModelResponse:
        body = self.build_body(spec, request)
        headers = self._headers()
        client = self._client or httpx.Client(timeout=spec.retry.timeout_ms / 1000)
        started = monotonic_ms()
        try:
            resp = client.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise ProviderError("request timed out", code="timeout", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"connection error: {exc}", code="connection", retryable=True
            ) from exc
        finally:
            if self._client is None:
                client.close()
        latency = monotonic_ms() - started

        if resp.status_code != 200:
            detail = _error_message(resp)
            if resp.status_code == 429:
                raise ProviderError(
                    f"rate limited: {detail}",
                    code="rate_limited",
                    retryable=True,
                    details={"retry_after": resp.headers.get("retry-after")},
                )
            raise ProviderError(
                f"provider returned HTTP {resp.status_code}: {detail}",
                code=f"http_{resp.status_code}",
                retryable=resp.status_code >= 500 or resp.status_code in _RETRYABLE_STATUS,
                details={"status": resp.status_code},
            )
        data = resp.json()
        if "error" in data and not data.get("choices"):
            # OpenRouter can return HTTP 200 with an upstream error payload.
            err = data["error"]
            raise ProviderError(
                f"provider error: {err.get('message', err)}",
                code=f"upstream_{err.get('code', 'error')}",
                retryable=False,
                details={"error": err},
            )
        choices = data.get("choices") or []
        if not choices:
            raise ProviderError(
                "provider returned no choices", code="empty_response", retryable=True
            )
        choice = choices[0]
        finish = choice.get("finish_reason") or choice.get("native_finish_reason")
        if finish == "content_filter":
            raise ProviderError(
                "the model declined this request", code="model_refusal", retryable=False
            )
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            text = "".join(str(c.get("text", "")) for c in content if isinstance(c, dict)) or None  # type: ignore[union-attr]
        else:
            text = content if isinstance(content, str) and content else None

        parsed: Any | None = None
        parse_error: str | None = None
        if request.output_schema is not None and text is not None:
            try:
                parsed = json.loads(text)
            except ValueError as exc:
                parse_error = str(exc)

        u = data.get("usage") or {}
        details = u.get("prompt_tokens_details") or {}
        usage = ModelCallUsage(
            provider=self.provider,
            model=spec.model,
            input_tokens=int(u.get("prompt_tokens", 0) or 0),
            output_tokens=int(u.get("completion_tokens", 0) or 0),
            cache_read_tokens=int(details.get("cached_tokens", 0) or 0),
            cost_usd=0.0,
            latency_ms=latency,
            provider_request_id=data.get("id"),
        )
        if isinstance(u.get("cost"), int | float):
            # OpenRouter reports the billed amount; it beats registry pricing.
            usage.cost_usd = float(u["cost"])
            usage.pricing_version = "provider-reported"
        return ModelResponse(
            text=text,
            parsed=parsed,
            parse_error=parse_error,
            usage=usage,
            stop_reason=finish,
            truncated=finish == "length",
            raw=data,
        )


def _error_message(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:300]
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        message = str(err.get("message", err))  # type: ignore[union-attr]
        meta = err.get("metadata")  # type: ignore[union-attr]
        raw = meta.get("raw") if isinstance(meta, dict) else None  # type: ignore[union-attr]
        if raw:
            message = f"{message}: {str(raw)[:600]}"
        return message
    return str(err or data)[:300]


__all__ = ["OpenAICompatibleAdapter"]
