# Validation, repair and acceptance

A result is never accepted because it parsed. It is accepted because every validator passed
(blueprint §14, §16).

## Core validators (always on)

| rule | severity | meaning | repair |
|---|---|---|---|
| `core.schema` | error | result violates the output schema | ask_field |
| `core.envelope` | error | the model did not follow the extraction envelope | reexecute |
| `core.unknown_required` | error | a required, non-nullable field was not found | ask_field |
| `core.evidence_missing` | warning | a found value has no anchored evidence | retrieve |
| `core.evidence_consistent` | warning | the anchored text does not contain the value | ask_field |
| `core.unknown_budget` | error | more than `policy.validation.max_unknown_fraction` unknown | reexecute |

Policies can escalate or downgrade any rule: `validation: { escalate: { evidence_missing: error } }`.

## Declarative validators

### `rules.expressions`

A safe expression language evaluated over the result document — no `eval`:

```
approx(sum(line_items[:].line_total), subtotal, 0.05)
approx(subtotal + tax, total)                   # default tolerance 0.005
invoice.currency == purchase_order.currency
len(line_items) > 0 and all(line_items[:].quantity)
present(purchase_order_number)
```

Names are top-level keys, `.` reads object keys (mapped over lists), `[:]` takes a whole list,
`[0]` indexes. Functions: `approx, sum, len, min, max, abs, round, all, any, count, present`.
Each rule sets `name`, `expr`, `message`, optional `field`, `severity` (default error) and
`repair` (default review). Rule ids are `rules.expressions.<name>`.

### `rules.formats`

`{pointer_pattern: format}` with formats `date`, `iso4217`, `email`, `positive`,
`non_negative`, `integer`, `regex:<pattern>`. Patterns use `*` for array indexes.

### `rules.evidence_required`

Fields that must (`required`, error) or should (`preferred`, warning) carry anchored evidence.
The manifest's `evidence:` section configures it.

### Skill-local validators

Any object in `validators.py` with `id`, `version` and `validate(ctx) -> list[Issue]`.

## Acceptance

Deterministic (`hawser.validation.decide`):

1. no error-severity issues → **accepted**
2. errors with a repairable kind and attempts left → **repair**
3. otherwise → **needs_review** if `review_on_failure` (default) else **rejected**

## Repair (direct mode)

Bounded by `policy.repair.attempts` (default 1), `max_cost_usd`, `max_seconds`.

- Errors on top-level fields with `repair: ask_field` → the model is asked for *only those
  fields* with the issues as corrections (sub-schema), and the values are merged.
- Other repairable errors → a full re-extraction with the issues and previous values attached.
- An attempt that reproduces the same issues and result is **no progress**: the loop stops and
  the run routes per policy.

Every attempt is a checkpointed step (`extract:repair1:model`) and an event (`repair.started`,
`repair.no_progress`). Workflow results route straight to review when their validators fail;
step-level re-execution arrives with agent mode.
