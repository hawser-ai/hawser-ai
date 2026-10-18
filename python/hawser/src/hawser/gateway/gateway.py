"""The gateway: registry lookup, policy check, cassette replay/record, retries, cost accounting."""

from __future__ import annotations

import json
import random
import time
from collections.abc import Callable
from typing import Any

from hawser.errors import PolicyViolation, ProviderError
from hawser.gateway.base import ModelAdapter, ModelRequest, ModelResponse
from hawser.gateway.registry import Registry
from hawser.models import ModelSpec, Policy
from hawser.recording import Cassette, request_fingerprint

Sleeper = Callable[[float], None]


class Gateway:
    def __init__(
        self,
        registry: Registry,
        adapters: dict[str, ModelAdapter],
        *,
        cassette: Cassette | None = None,
        store_raw: bool = False,
        sleeper: Sleeper = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self.registry = registry
        self.adapters = adapters
        self.cassette = cassette
        self.store_raw = store_raw
        self._sleep = sleeper
        self._rng = rng or random.Random()
        #: Observers called with (request, response, {"replayed": bool, "attempts": int}).
        self.observers: list[Callable[[ModelRequest, ModelResponse, dict[str, Any]], None]] = []

    # -- policy ----------------------------------------------------------------------------
    @staticmethod
    def check_policy(spec: ModelSpec, policy: Policy) -> None:
        if policy.allowed_models is not None and spec.id not in policy.allowed_models:
            raise PolicyViolation(
                f"model {spec.id} is not in the policy allow-list", code="model_not_allowed"
            )
        if policy.allowed_providers is not None and spec.provider not in policy.allowed_providers:
            raise PolicyViolation(
                f"provider {spec.provider} is not in the policy allow-list",
                code="provider_not_allowed",
            )
        if policy.privacy.no_hosted and spec.data_handling.hosted:
            raise PolicyViolation(
                f"policy forbids hosted models; {spec.id} is hosted", code="hosted_model_forbidden"
            )
        if policy.privacy.no_data_retention and spec.data_handling.retains_data:
            raise PolicyViolation(
                f"policy forbids data-retaining providers; {spec.id} retains data",
                code="retention_forbidden",
            )
        if policy.privacy.allowed_regions is not None and not (
            set(spec.data_handling.regions) & set(policy.privacy.allowed_regions)
        ):
            raise PolicyViolation(
                f"model {spec.id} is not served in an allowed region", code="region_not_allowed"
            )

    # -- pricing ---------------------------------------------------------------------------
    @staticmethod
    def price(spec: ModelSpec, response: ModelResponse) -> None:
        u = response.usage
        if u.pricing_version == "provider-reported":
            return  # the provider billed an exact amount; keep it
        if spec.pricing is None:
            u.cost_usd = 0.0
            u.pricing_version = None
            return
        p = spec.pricing
        cost = u.input_tokens * p.input_per_mtok + u.output_tokens * p.output_per_mtok
        if p.cache_read_per_mtok is not None:
            cost += u.cache_read_tokens * p.cache_read_per_mtok
        if p.cache_write_per_mtok is not None:
            cost += u.cache_write_tokens * p.cache_write_per_mtok
        u.cost_usd = round(cost / 1_000_000, 8)
        u.pricing_version = p.version

    # -- main entry ------------------------------------------------------------------------
    def complete(self, request: ModelRequest, *, policy: Policy | None = None) -> ModelResponse:
        spec = self.registry.get(request.model)
        if policy is not None:
            self.check_policy(spec, policy)

        key = request_fingerprint("model", spec.provider, spec.model, request.fingerprint_payload())
        if self.cassette is not None and self.cassette.replays:
            hit = self.cassette.lookup(key)
            if hit is not None:
                response = ModelResponse.from_cassette(hit["response"])
                self._notify(
                    request, response, {"replayed": True, "attempts": 0, "cassette_key": key}
                )
                return response
            if not self.cassette.records:
                raise ProviderError(
                    f"no cassette entry for this request (key {key}) and record mode is 'replay'",
                    code="cassette_miss",
                    retryable=False,
                    details={"cassette_key": key, "cassette_dir": str(self.cassette.directory)},
                )

        adapter = self.adapters.get(spec.provider)
        if adapter is None:
            raise ProviderError(
                f"no adapter registered for provider {spec.provider!r}",
                code="adapter_missing",
                retryable=False,
            )

        try:
            response, attempts = self._call_with_retries(adapter, spec, request)
        except ProviderError as exc:
            if request.output_schema is None or not _is_grammar_rejection(exc):
                raise
            response, attempts = self._call_with_retries(
                adapter, spec, prompt_guided_request(request)
            )
            response.structured_fallback = True
            if response.parsed is None and response.text:
                response.parsed, response.parse_error = parse_json_text(response.text)
        self.price(spec, response)
        if not self.store_raw:
            response.raw = None

        if self.cassette is not None and self.cassette.records:
            self.cassette.store(
                key,
                kind="model",
                provider=spec.provider,
                name=spec.model,
                request=request.fingerprint_payload(),
                response=response.to_cassette(),
            )
        self._notify(
            request, response, {"replayed": False, "attempts": attempts, "cassette_key": key}
        )
        return response

    def complete_any(
        self, request: ModelRequest, candidates: list[str], *, policy: Policy | None = None
    ) -> ModelResponse:
        """Try ``candidates`` in order; fall back on provider failure, never on policy."""
        if not candidates:
            raise ProviderError("no model candidates", code="no_model_available", retryable=False)
        failed: list[str] = []
        last: ProviderError | None = None
        for model_id in candidates:
            req = (
                request
                if request.model == model_id
                else request.model_copy(update={"model": model_id})
            )
            try:
                resp = self.complete(req, policy=policy)
            except PolicyViolation:
                raise  # policy failures are never worked around
            except ProviderError as exc:
                last = exc
                if not fallback_worthy(exc) or model_id == candidates[-1]:
                    exc.details.setdefault("fallback_from", list(failed))
                    raise
                failed.append(model_id)
                continue
            resp.fallback_from = failed
            return resp
        raise last or ProviderError(
            "no candidate answered", code="no_model_available", retryable=False
        )

    def _call_with_retries(
        self, adapter: ModelAdapter, spec: ModelSpec, request: ModelRequest
    ) -> tuple[ModelResponse, int]:
        rb = spec.retry
        last: ProviderError | None = None
        for attempt in range(1, rb.max_attempts + 1):
            try:
                resp = adapter.complete(spec, request)
            except ProviderError as exc:
                last = exc
                if not exc.retryable or attempt == rb.max_attempts:
                    raise
                delay = self._backoff(rb.base_delay_ms, rb.max_delay_ms, attempt, exc)
                self._sleep(delay)
                continue
            resp.usage.attempts = attempt
            return resp, attempt
        raise last or ProviderError("retry loop exited without a response", code="retry_exhausted")

    def _backoff(self, base_ms: int, max_ms: int, attempt: int, exc: ProviderError) -> float:
        retry_after = exc.details.get("retry_after")
        if retry_after is not None:
            try:
                return min(float(retry_after), max_ms / 1000)
            except (TypeError, ValueError):
                pass
        raw = min(base_ms * (2 ** (attempt - 1)), max_ms)
        return (raw * (0.5 + self._rng.random())) / 1000

    def _notify(self, request: ModelRequest, response: ModelResponse, info: dict[str, Any]) -> None:
        for fn in self.observers:
            fn(request, response, info)


_FALLBACK_CODES = {
    "model_refusal",
    "document_parts_unsupported",
    "empty_response",
    "missing_credentials",
    "adapter_missing",
    "cassette_miss",
}


def fallback_worthy(exc: ProviderError) -> bool:
    """Errors after which trying the next candidate makes sense (not a bad request)."""
    return exc.retryable or exc.code in _FALLBACK_CODES or exc.code.startswith("upstream_")


def _is_grammar_rejection(exc: ProviderError) -> bool:
    if not exc.code.startswith("http_4"):
        return False
    msg = exc.message.lower()
    return any(k in msg for k in ("grammar", "schema", "union types", "response_format"))


def prompt_guided_request(request: ModelRequest) -> ModelRequest:
    """The same request without provider-enforced structure: schema goes into the prompt."""
    schema_text = json.dumps(request.output_schema, separators=(",", ":"))
    guidance = (
        "\n\nRespond with a single JSON document and nothing else — no prose, no code fences. "
        "It must conform exactly to this JSON Schema:\n" + schema_text
    )
    return request.model_copy(
        update={"system": (request.system or "") + guidance, "output_schema": None}
    )


def parse_json_text(text: str) -> tuple[Any | None, str | None]:
    """Parse JSON from model text, tolerating code fences and surrounding prose."""
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.split("\n", 1)[1] if "\n" in candidate else ""
        candidate = candidate.rsplit("```", 1)[0]
    try:
        return json.loads(candidate), None
    except ValueError:
        pass
    start, end = candidate.find("{"), candidate.rfind("}")
    if 0 <= start < end:
        try:
            return json.loads(candidate[start : end + 1]), None
        except ValueError as exc:
            return None, str(exc)
    return None, "no JSON object found in the response"


__all__ = ["Gateway", "fallback_worthy", "parse_json_text", "prompt_guided_request"]
