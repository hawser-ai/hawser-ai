# Policy

A policy sets the rules for one run: how much it may spend, which models it may use, how hard
it tries to fix problems, and what happens when checks fail. The policy is fixed when the run
starts, and nothing a model or tool returns can change it.

Every field is optional. Leave the policy out and you get the defaults below.

## Setting a policy

From Python, pass a dict (or a `hawser.Policy`):

```python
result = hawser.run(
    "Extract the invoice.",
    inputs=["invoice.pdf"],
    output_schema=Invoice,
    policy={
        "limits": {"max_cost_usd": 0.10},
        "validation": {"escalate": {"evidence_missing": "error"}},
    },
)
```

From the command line, pass a YAML file:

```bash
hawser run -i "Extract the invoice." -f invoice.pdf --schema invoice.schema.json --policy policy.yaml
```

```yaml
# policy.yaml
limits:
  max_cost_usd: 0.25      # stop before spending more than 25 cents
repair:
  attempts: 1             # try once to fix a failed check
review_on_failure: true   # send failures to a person instead of rejecting them
```

## The settings you will use most

| Setting | Default | What it does |
|---|---|---|
| `limits.max_cost_usd` | deployment default | Hard spending cap for the run. Hawser checks the estimated cost *before* each model call. |
| `limits.max_pages` | 200 | Refuse documents longer than this. |
| `repair.attempts` | 1 | How many times Hawser may try to fix a failed check (for example, by re-reading one field). `0` turns repair off. |
| `review_on_failure` | `true` | When checks fail: `true` gives `needs_review`, `false` gives `rejected`. |
| `allowed_models` | any | Only these model IDs may be used, e.g. `["openrouter/anthropic/claude-haiku-5.5"]`. |
| `privacy.no_hosted` | `false` | Use only local or self-hosted models (Ollama, vLLM). |
| `validation.escalate` | none | Make a check stricter, e.g. `{"evidence_missing": "error"}` fails the run when a value can't be found on the page (normally just a warning). |
| `validation.max_unknown_fraction` | none | Raise an issue when more than this share of fields (0 to 1) is `unknown`. |

## Built-in checks you can escalate or downgrade

Use the short name (`evidence_missing`) or the full ID (`core.evidence_missing`) in
`validation.escalate` and `validation.downgrade`.

| Check | Default severity | Fires when |
|---|---|---|
| `schema` | error | The result doesn't match your output schema. |
| `unknown_required` | error | A required field could not be found in the document. |
| `evidence_missing` | warning | A value was returned but couldn't be located on the page. |
| `evidence_consistent` | warning | The located text doesn't agree with the returned value. |
| `unknown_budget` | error | More than `validation.max_unknown_fraction` of the fields are `unknown` (only when that is set). |

Your own rules (see [Validation](validation.md)) can be escalated or downgraded the same way,
by their rule ID.

## Full reference

```yaml
mode: direct | workflow | agent        # default: chosen from the request
limits:                                # hard caps; omitted = deployment default
  max_cost_usd, max_model_calls, max_tool_calls,
  max_input_tokens, max_output_tokens,
  max_pages, max_pixels, max_media_duration_ms, max_sampled_frames,
  max_plan_steps, max_loop_iterations, max_parallel,
  max_sandbox_runs, max_wall_time_s
repair:
  attempts: 1
  max_cost_usd: null                   # separate cap for repair work
  max_seconds: null
review_on_failure: true
allowed_models: null                   # list of model IDs
allowed_providers: null                # e.g. [anthropic, openrouter]
allowed_tools: null                    # list of tool names
privacy:
  no_hosted: false
  allowed_regions: null
  no_data_retention: false
fallback:
  enabled: true                        # try another permitted model if one fails
  candidates: null                     # ordered list of model IDs
validation:
  escalate: {}                         # {check: error | warning | info}
  downgrade: {}
  allow_model_judges: false
  max_unknown_fraction: null
approvals:
  require_for_external_write: true     # tools that change outside systems need approval
  pre_approved: []                     # e.g. ["erp.post_invoice:create"]
planning:                              # agent mode
  max_replans: 2
  cache: true
  context_tokens: null
allow_public_urls: false               # fetching public URLs as inputs is off by default
```

The policy schema is published as JSON Schema in
[`api/schemas/`](../../api/schemas/), and the model is `hawser.Policy`.
