from __future__ import annotations

from pathlib import Path

import pytest

from hawser.config import RecordMode
from hawser.errors import PolicyViolation, ProviderError
from hawser.gateway import Gateway, Message, ModelRequest, TextPart, load_registry
from hawser.models import Policy
from hawser.models.policy import PrivacyPolicy
from hawser.recording import Cassette
from hawser.testing import StaticAdapter

SCHEMA = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]}


def _req(text: str = "hi", schema: bool = True) -> ModelRequest:
    return ModelRequest(
        model="anthropic/claude-opus-5-5",
        system="sys",
        messages=[Message(role="user", parts=[TextPart(text=text)])],
        output_schema=SCHEMA if schema else None,
    )


def test_pricing_and_registry_version() -> None:
    adapter = StaticAdapter({"a": "x"})
    gw = Gateway(load_registry(), {"anthropic": adapter})
    resp = gw.complete(_req())
    assert resp.parsed == {"a": "x"}
    u = resp.usage
    expected = (u.input_tokens * 4.0 + u.output_tokens * 20.0) / 1_000_000
    assert u.cost_usd == pytest.approx(expected)
    assert u.pricing_version == "2026-10-06"
    assert gw.registry.version.startswith("2026-10-18+")


def test_unknown_model() -> None:
    gw = Gateway(load_registry(), {"anthropic": StaticAdapter({"a": "x"})})
    with pytest.raises(PolicyViolation) as exc:
        gw.complete(_req().model_copy(update={"model": "anthropic/nope"}))
    assert exc.value.code == "model_unknown"


def test_policy_checks() -> None:
    gw = Gateway(load_registry(), {"anthropic": StaticAdapter({"a": "x"})})
    with pytest.raises(PolicyViolation) as exc:
        gw.complete(_req(), policy=Policy(allowed_models=["anthropic/claude-haiku-5-5"]))
    assert exc.value.code == "model_not_allowed"
    with pytest.raises(PolicyViolation) as exc:
        gw.complete(_req(), policy=Policy(privacy=PrivacyPolicy(no_hosted=True)))
    assert exc.value.code == "hosted_model_forbidden"
    with pytest.raises(PolicyViolation) as exc:
        gw.complete(_req(), policy=Policy(privacy=PrivacyPolicy(allowed_regions=["eu"])))
    assert exc.value.code == "region_not_allowed"
    gw.complete(_req(), policy=Policy(allowed_providers=["anthropic"]))


def test_record_then_replay(tmp_path: Path) -> None:
    adapter = StaticAdapter({"a": "recorded"})
    cas = Cassette(tmp_path / "cas", RecordMode.record)
    gw = Gateway(load_registry(), {"anthropic": adapter}, cassette=cas)
    first = gw.complete(_req())
    assert not first.usage.replayed
    assert len(cas) == 1
    assert first.usage.cost_usd > 0

    replay = Gateway(
        load_registry(),
        {"anthropic": StaticAdapter({"a": "LIVE"})},
        cassette=Cassette(tmp_path / "cas", RecordMode.replay),
    )
    second = replay.complete(_req())
    assert second.parsed == {"a": "recorded"}
    assert second.usage.replayed is True
    assert second.usage.cost_usd == 0.0
    assert len(adapter.calls) == 1

    with pytest.raises(ProviderError) as exc:
        replay.complete(_req("different prompt"))
    assert exc.value.code == "cassette_miss"

    auto = Gateway(
        load_registry(),
        {"anthropic": StaticAdapter({"a": "auto"})},
        cassette=Cassette(tmp_path / "cas", RecordMode.auto),
    )
    assert auto.complete(_req()).parsed == {"a": "recorded"}
    assert auto.complete(_req("different prompt")).parsed == {"a": "auto"}
    assert len(cas) == 2


def test_retries_then_succeeds() -> None:
    sleeps: list[float] = []
    adapter = StaticAdapter(
        {"a": "ok"}, fail_times=2, failure=ProviderError("boom", code="http_503", retryable=True)
    )
    gw = Gateway(load_registry(), {"anthropic": adapter}, sleeper=sleeps.append)
    resp = gw.complete(_req())
    assert resp.parsed == {"a": "ok"}
    assert resp.usage.attempts == 3
    assert len(sleeps) == 2
    assert all(s > 0 for s in sleeps)


def test_non_retryable_raises_immediately() -> None:
    sleeps: list[float] = []
    adapter = StaticAdapter(
        {"a": "ok"}, fail_times=5, failure=ProviderError("bad", code="http_400", retryable=False)
    )
    gw = Gateway(load_registry(), {"anthropic": adapter}, sleeper=sleeps.append)
    with pytest.raises(ProviderError) as exc:
        gw.complete(_req())
    assert exc.value.code == "http_400"
    assert sleeps == []
    assert len(adapter.calls) == 1


def test_retry_exhaustion() -> None:
    adapter = StaticAdapter(
        {"a": "ok"}, fail_times=10, failure=ProviderError("boom", code="http_503", retryable=True)
    )
    gw = Gateway(load_registry(), {"anthropic": adapter}, sleeper=lambda _: None)
    with pytest.raises(ProviderError):
        gw.complete(_req())
    assert len(adapter.calls) == 3  # registry default max_attempts
