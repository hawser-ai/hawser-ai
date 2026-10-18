from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from hawser.errors import ProviderError
from hawser.gateway import Gateway, Message, ModelRequest, TextPart, load_registry
from hawser.gateway.openai_compat import OpenAICompatibleAdapter
from hawser.gateway.testing import compatibility_checks, summarize
from hawser.models import ModelSpec
from hawser.testing import StaticAdapter

REG = load_registry()
OR_SPEC: ModelSpec = REG.get("openrouter/anthropic/claude-haiku-5.5")


def _adapter(handler: Any) -> OpenAICompatibleAdapter:
    a = OpenAICompatibleAdapter.openrouter()
    a._client = httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[attr-defined]
    return a


def _chat(
    content: str, *, finish: str = "stop", usage: dict[str, Any] | None = None
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "gen-1",
            "choices": [
                {"message": {"role": "assistant", "content": content}, "finish_reason": finish}
            ],
            "usage": usage or {"prompt_tokens": 12, "completion_tokens": 4, "cost": 0.0001},
        },
    )


def test_kit_against_static_adapter() -> None:
    adapter = StaticAdapter({"reply": "pong", "number": 7}, text="pong")
    results = compatibility_checks(adapter, OR_SPEC, live=True)
    s = summarize(results)
    assert s["failed"] == [], s


def test_openai_compat_request_shape_and_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        body = json.loads(req.content)
        if "response_format" in body:
            return _chat(json.dumps({"reply": "pong", "number": 7}))
        return _chat("pong")

    adapter = _adapter(handler)
    results = compatibility_checks(adapter, OR_SPEC, live=True)
    assert summarize(results)["failed"] == []
    first = json.loads(seen[0].content)
    assert first["model"] == "anthropic/claude-haiku-5.5"
    assert seen[0].headers["Authorization"] == "Bearer test-key"
    assert first["usage"] == {"include": True}
    structured = next(
        json.loads(r.content) for r in seen if "response_format" in json.loads(r.content)
    )
    assert structured["response_format"]["json_schema"]["strict"] is True
    image_req = next(
        json.loads(r.content)
        for r in seen
        if any(
            isinstance(p, dict) and p.get("type") == "image_url"
            for p in json.loads(r.content)["messages"][-1]["content"]
        )
    )
    assert image_req["messages"][-1]["content"][0]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )


@pytest.mark.parametrize(
    ("response", "code", "retryable"),
    [
        (
            httpx.Response(
                429, headers={"retry-after": "3"}, json={"error": {"message": "slow down"}}
            ),
            "rate_limited",
            True,
        ),
        (httpx.Response(500, json={"error": {"message": "boom"}}), "http_500", True),
        (
            httpx.Response(
                400,
                json={
                    "error": {
                        "message": "Provider returned error",
                        "metadata": {"raw": "bad schema"},
                    }
                },
            ),
            "http_400",
            False,
        ),
        (
            httpx.Response(200, json={"error": {"message": "upstream failed", "code": 502}}),
            "upstream_502",
            False,
        ),
        (_chat("", finish="content_filter"), "model_refusal", False),
        (httpx.Response(200, json={"id": "x", "choices": []}), "empty_response", True),
    ],
)
def test_openai_compat_error_mapping(
    monkeypatch: pytest.MonkeyPatch, response: httpx.Response, code: str, retryable: bool
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    adapter = _adapter(lambda _r: response)  # type: ignore[reportUnknownLambdaType]
    with pytest.raises(ProviderError) as exc:
        adapter.complete(
            OR_SPEC,
            ModelRequest(
                model=OR_SPEC.id, messages=[Message(role="user", parts=[TextPart(text="x")])]
            ),
        )
    assert exc.value.code == code
    assert exc.value.retryable is retryable
    if code == "rate_limited":
        assert exc.value.details["retry_after"] == "3"
    if code == "http_400":
        assert "bad schema" in exc.value.message


def test_missing_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    adapter = _adapter(lambda _r: _chat("x"))  # type: ignore[reportUnknownLambdaType]
    with pytest.raises(ProviderError) as exc:
        adapter.complete(
            OR_SPEC,
            ModelRequest(
                model=OR_SPEC.id, messages=[Message(role="user", parts=[TextPart(text="x")])]
            ),
        )
    assert exc.value.code == "missing_credentials"


def test_gateway_grammar_fallback_via_openai_compat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    calls: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        calls.append(body)
        if "response_format" in body:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "Provider returned error",
                        "metadata": {"raw": "The compiled grammar is too large"},
                    }
                },
            )
        return _chat('```json\n{"reply": "pong", "number": 7}\n```')

    gw = Gateway(REG, {"openrouter": _adapter(handler)}, sleeper=lambda _s: None)
    resp = gw.complete(
        ModelRequest(
            model=OR_SPEC.id,
            messages=[Message(role="user", parts=[TextPart(text="x")])],
            output_schema={
                "type": "object",
                "properties": {"reply": {"type": "string"}, "number": {"type": "integer"}},
            },
        )
    )
    assert resp.structured_fallback is True
    assert resp.parsed == {"reply": "pong", "number": 7}
    assert len(calls) == 2
    assert (
        "JSON Schema" in calls[1]["messages"][0]["content"]
    )  # schema moved into the system prompt
    assert resp.usage.cost_usd == 0.0001  # provider-reported cost kept
