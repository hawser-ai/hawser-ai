# Models, selection, fallback and budgets

## Registry

`hawser models list` shows the registry (`hawser/gateway/registry.yaml`): id, provider,
modalities, context window, structured-output support, pricing, data handling (hosted?,
regions, retention) and priors for quality and speed. `hawser models check <id>` verifies an
entry live with the compatibility kit (text, structured output, image part).

Providers: `anthropic` (first-party SDK), and one OpenAI-compatible adapter with profiles for
`openrouter`, `openai`, `ollama` (local) and vLLM. Model ids are `<provider>/<model>`, e.g.
`openrouter/google/gemini-2.5-flash`.

## Selection

For every run the planner computes **requirements** from the inputs (modalities — image when a
page has no text layer or an image is supplied —, structured output when a schema is given,
minimum context from the estimated input size) and builds an ordered **candidate list**:

1. filter by policy: `allowed_models`, `allowed_providers`, `privacy.no_hosted`,
   `privacy.allowed_regions`, `privacy.no_data_retention`
2. filter by capability (the `plan.created` event lists every exclusion and why)
3. rank by the request's strategy: `cheapest`, `fastest`, `best_quality`, `balanced`
   (the configured default model leads when it qualifies); a pinned model or an explicit
   `candidates` list is used as given — after the same filters.

```python
hawser.run(..., models="openrouter/anthropic/claude-haiku-5.5")              # pin
hawser.run(..., models=ModelPreference(strategy="cheapest"))
hawser.run(..., policy=Policy(privacy=PrivacyPolicy(no_hosted=True)))      # local only
```

## Fallback

The gateway tries candidates in order and moves to the next one only on provider failure
(retry exhaustion, refusal, upstream error, missing credentials). It never falls back on a
policy violation or a bad request, and because the list was policy-filtered first, fallback
cannot reach a model the policy excluded. `policy.fallback.enabled=false` keeps one candidate;
`policy.fallback.candidates` restricts the fallbacks. Each hop is a `model.fallback` event and
`usage.model_calls[].provider/model` always name the model that answered.

Separately, if a provider rejects a schema grammar, the gateway retries once with the schema in
the prompt and marks the result `structured_output_fallback`.

## Budgets

Checked *before* each paid call from a pre-call estimate (chars/4 + images + schema, ¼ of
`max_output_tokens` expected out), so a limit is never discovered after money was spent:

| policy limit | enforced at |
|---|---|
| `limits.max_cost_usd` | every model/tool call (estimate + spent so far) |
| `limits.max_model_calls`, `max_tool_calls`, `max_input_tokens` | every call |
| `limits.max_pages`, `max_pixels` | admission |
| `repair.attempts`, `repair.max_cost_usd`, `repair.max_seconds` | the repair loop |

A budget stop ends the run `needs_review` (default policy) or `failed`, with the limit named in
`error.code`. Usage is **reconciled** at the end of every run: the run's totals must equal the
sum of its steps' usage or the run fails with `usage_mismatch` — a bug detector, not a user
error. OpenRouter's billed amounts override registry pricing (`pricing_version:
provider-reported`).
