# Agent mode

Agent mode compiles a goal into a **typed plan**, validates it statically, and executes it with
checkpointed steps under the same policy, budgets and broker as every other mode (blueprint §3,
§10). The model recommends the steps; the runtime decides whether they are allowed, runs them,
and judges the result.

```bash
hawser run --mode agent -i "Find every discrepancy between these two documents" -f invoice.pdf -f po.pdf
hawser run --skill invoice-po-reconciliation --mode agent -f invoice.pdf -f purchase_order.pdf   # planner-built steps, skill's contract
hawser inspect run_… --plan
```

## The plan IR

A plan is JSON: `goal`, `inputs`, `nodes` (executed in order; containers run the nodes they
reference). Node kinds: `call_model` (extraction with a schema and a `context` spec),
`call_tool` (any broker tool, or a skill as `skill.<name>`), `compute` (safe expression),
`branch`, `loop` (bounded, with no-progress detection), `parallel` (budget reserved up front),
`validate`, `review`, `repair`, `exit` (exactly one success exit whose `result` is a binding or
an object of bindings). Values flow through bindings: `$input.<name>`, `$node.<id>`,
`$node.<id>.path[0]`.

The static validator rejects a plan before any work starts when it has unknown nodes, inputs or
tools, tools the skill/request/policy do not permit, an external write without a preceding
`review` (or pre-approval), loops or parallel widths over policy limits, invalid expressions, a
schema it cannot inline, more nodes than `max_plan_steps`, or an estimated cost over
`max_cost_usd`.

## Planning

The planner sees the goal, the input manifests (not the content), the permitted tool catalogue
(including installed skills), the policy limits and, when a skill or schema is given, the
required result schema. It returns plain JSON that the runtime parses and validates; an invalid
plan is sent back with the problems, up to `policy.planning.max_replans` times. Validated plans
are cached by goal, tool catalogue, policy and input shape, so a repeated goal costs no planner
call (`plan.created` says `source: cached`).

Deterministic shortcut: a free-form goal that names an installed skill is told to prefer
`skill.<name>`, and `--mode agent` on a workflow skill keeps the skill's inputs, schema, tools
and validators while letting the planner choose the steps.

## Execution

- Every node is a checkpointed step (`plan/<id>`), so a resumed run replays finished nodes.
- `call_model` context: `pages: all | matching | [n…]`; `matching` ranks pages by BM25 against
  the node's `query` within a token budget (`policy.planning.context_tokens`); prior outputs are
  included as compact JSON summaries. Selections are recorded in `context.selected` events.
- `parallel` reserves the estimated cost of its branches against `max_cost_usd` before
  dispatch (`budget.reserved` / `budget.settled` / `budget.released` events) and runs them in
  a bounded thread pool.
- `review` raises an error-severity issue (`plan.review`) so the run ends `needs_review`; a
  `failure` exit does the same with a `plan_failure_exit` warning.
- Evidence travels with bindings: if the success exit's result places `$node.extract` under
  `invoice`, every field under `/invoice/…` carries that extraction's evidence.
- The final result is validated against the request/skill schema and the skill's validators,
  then accepted or routed to review exactly like the other modes.

## When to use it

Direct mode for one document and one schema; workflow mode for a stable, versioned process;
agent mode for variable goals or document sets where the steps are not known in advance. The
eval system compares the three on the same dataset — see `docs/benchmarks/invoice-po.md`.
