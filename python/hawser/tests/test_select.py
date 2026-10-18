from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from hawser.errors import PolicyViolation, ProviderError
from hawser.gateway import Gateway, Message, ModelRequest, TextPart, load_registry
from hawser.gateway.registry import Registry
from hawser.gateway.select import Requirements, candidates, capable, rank
from hawser.models import Modality, ModelPreference, ModelSpec, Policy
from hawser.models.model_spec import DataHandling, Pricing
from hawser.models.policy import FallbackPolicy, PrivacyPolicy
from hawser.testing import StaticAdapter

REG = load_registry()
DEFAULT = "anthropic/claude-opus-5-5"


def test_capability_filtering() -> None:
    req = Requirements(modalities={Modality.text, Modality.image}, structured_output=True)
    sel = candidates(REG, req, Policy(), None, default_model=DEFAULT)
    assert "ollama/llama3.1" in sel.excluded  # text only
    assert sel.excluded["ollama/llama3.1"].startswith("lacks modalities")
    assert sel.candidates[0] == DEFAULT  # the configured default leads when it qualifies
    big = Requirements(min_context=2_000_000)
    with pytest.raises(PolicyViolation) as exc:
        candidates(REG, big, Policy(), None, default_model=DEFAULT)
    assert exc.value.code == "no_model_available"


def test_policy_filtering_and_strategies() -> None:
    local_only = Policy(privacy=PrivacyPolicy(no_hosted=True))
    sel = candidates(REG, Requirements(), local_only, None, default_model=DEFAULT)
    assert sel.candidates == ["ollama/llama3.1"]
    assert sel.excluded[DEFAULT] == "hosted_model_forbidden"

    cheapest = candidates(
        REG,
        Requirements(),
        Policy(),
        ModelPreference(strategy="cheapest"),
        default_model="nope/none",
    )
    assert cheapest.candidates[0] == "ollama/llama3.1"  # free
    assert cheapest.candidates[1] in (
        "anthropic/claude-haiku-5-5",
        "openrouter/anthropic/claude-haiku-5.5",
    )
    best = candidates(
        REG,
        Requirements(),
        Policy(),
        ModelPreference(strategy="best_quality"),
        default_model="nope/none",
    )
    assert best.candidates[0] in (
        "anthropic/claude-opus-5-5",
        "openrouter/anthropic/claude-opus-5.5",
    )
    fastest = candidates(
        REG,
        Requirements(),
        Policy(),
        ModelPreference(strategy="fastest"),
        default_model="nope/none",
    )
    assert fastest.candidates[0] in (
        "anthropic/claude-haiku-5-5",
        "openrouter/anthropic/claude-haiku-5.5",
    )


def test_pinned_and_explicit_candidates() -> None:
    pinned = candidates(
        REG,
        Requirements(),
        Policy(),
        ModelPreference(pinned="anthropic/claude-haiku-5-5"),
        default_model=DEFAULT,
    )
    assert pinned.candidates == ["anthropic/claude-haiku-5-5"]
    with pytest.raises(PolicyViolation) as exc:
        candidates(
            REG,
            Requirements(),
            Policy(allowed_providers=["openrouter"]),
            ModelPreference(pinned=DEFAULT),
            default_model=DEFAULT,
        )
    assert exc.value.code == "model_pinned_unusable"
    explicit = candidates(
        REG,
        Requirements(),
        Policy(),
        ModelPreference(candidates=["openrouter/anthropic/claude-sonnet-5.5", DEFAULT]),
        default_model=DEFAULT,
    )
    assert explicit.candidates == ["openrouter/anthropic/claude-sonnet-5.5", DEFAULT]  # order kept


def test_fallback_policy_shapes_the_list() -> None:
    no_fb = candidates(
        REG,
        Requirements(),
        Policy(fallback=FallbackPolicy(enabled=False)),
        None,
        default_model=DEFAULT,
    )
    assert no_fb.candidates == [DEFAULT]
    limited = candidates(
        REG,
        Requirements(),
        Policy(fallback=FallbackPolicy(candidates=["anthropic/claude-haiku-5-5"])),
        None,
        default_model=DEFAULT,
    )
    assert limited.candidates == [DEFAULT, "anthropic/claude-haiku-5-5"]


def _spec(i: int, hosted: bool, mods: set[Modality], price: float) -> ModelSpec:
    return ModelSpec(
        id=f"p{i % 3}/m{i}",
        provider=f"p{i % 3}",
        model=f"m{i}",
        modalities=sorted(mods, key=lambda m: m.value),
        context_window=100_000,
        max_output_tokens=8_000,
        structured_output=True,
        data_handling=DataHandling(hosted=hosted, regions=["us" if i % 2 else "eu"]),
        pricing=Pricing(input_per_mtok=price, output_per_mtok=price * 4, version="t"),
        quality_prior=(i % 7) / 7,
    )


@settings(max_examples=60, deadline=None)
@given(
    hosted=st.lists(st.booleans(), min_size=1, max_size=6),
    no_hosted=st.booleans(),
    providers=st.one_of(st.none(), st.lists(st.sampled_from(["p0", "p1", "p2"]), max_size=2)),
    regions=st.one_of(st.none(), st.lists(st.sampled_from(["us", "eu"]), min_size=1, max_size=2)),
    strategy=st.sampled_from(["cheapest", "fastest", "best_quality", "balanced"]),
)
def test_fallback_never_reaches_an_excluded_model(
    hosted: list[bool],
    no_hosted: bool,
    providers: list[str] | None,
    regions: list[str] | None,
    strategy: str,
) -> None:
    specs = [_spec(i, h, {Modality.text}, 1.0 + i) for i, h in enumerate(hosted)]
    reg = Registry(specs, version="t")
    policy = Policy(
        allowed_providers=providers,
        privacy=PrivacyPolicy(no_hosted=no_hosted, allowed_regions=regions),
    )
    pref = ModelPreference.model_validate({"strategy": strategy})
    try:
        sel = candidates(reg, Requirements(), policy, pref, default_model="none/none")
    except PolicyViolation as exc:
        code = exc.code
        assert code == "no_model_available"
        return
    for mid in sel.candidates:
        spec = reg.get(mid)
        assert Gateway.check_policy(spec, policy) is None  # every candidate passes policy
        assert capable(spec, Requirements()) is None


def _req() -> ModelRequest:
    return ModelRequest(
        model="anthropic/claude-opus-5-5",
        messages=[Message(role="user", parts=[TextPart(text="hi")])],
        output_schema={"type": "object", "properties": {"a": {"type": "string"}}},
    )


def test_gateway_fallback_on_retryable_failure() -> None:
    failing = StaticAdapter(
        {"a": "x"}, fail_times=10, failure=ProviderError("down", code="http_503", retryable=True)
    )
    ok = StaticAdapter({"a": "fallback"})
    ok.provider = "openrouter"
    reg = Registry(
        [REG.get("anthropic/claude-opus-5-5"), REG.get("openrouter/anthropic/claude-opus-5.5")],
        version="t",
    )
    gw = Gateway(reg, {"anthropic": failing, "openrouter": ok}, sleeper=lambda _s: None)
    resp = gw.complete_any(
        _req(),
        ["anthropic/claude-opus-5-5", "openrouter/anthropic/claude-opus-5.5"],
        policy=Policy(),
    )
    assert resp.parsed == {"a": "fallback"}
    assert resp.fallback_from == ["anthropic/claude-opus-5-5"]
    assert resp.usage.provider == "openrouter"


def test_gateway_no_fallback_on_bad_request_or_policy() -> None:
    bad = StaticAdapter(
        {"a": "x"}, fail_times=10, failure=ProviderError("bad", code="http_400", retryable=False)
    )
    ok = StaticAdapter({"a": "y"})
    reg = Registry(
        [REG.get("anthropic/claude-opus-5-5"), REG.get("openrouter/anthropic/claude-opus-5.5")],
        version="t",
    )
    gw = Gateway(reg, {"anthropic": bad, "openrouter": ok}, sleeper=lambda _s: None)
    with pytest.raises(ProviderError) as exc:
        gw.complete_any(
            _req(),
            ["anthropic/claude-opus-5-5", "openrouter/anthropic/claude-opus-5.5"],
            policy=Policy(),
        )
    assert exc.value.code == "http_400"
    assert ok.calls == []
    with pytest.raises(PolicyViolation):
        gw.complete_any(
            _req(),
            ["anthropic/claude-opus-5-5", "openrouter/anthropic/claude-opus-5.5"],
            policy=Policy(allowed_providers=["nobody"]),
        )
    assert ok.calls == []


def test_rank_is_deterministic() -> None:
    specs = REG.all()
    assert [s.id for s in rank(specs, "cheapest")] == [
        s.id for s in rank(list(reversed(specs)), "cheapest")
    ]
