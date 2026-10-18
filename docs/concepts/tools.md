# Tools, the broker and approvals

Tools are typed, versioned capabilities (blueprint §11). A run can only invoke a tool through
the **broker**, which enforces, in order:

1. **Permission** — the tool name must be allowed by *every* layer that declares a list: the
   skill manifest's `tools:`, the request's `--tools` (narrowing only), and the policy's
   `allowed_tools`. Globs like `compute.*` are accepted.
2. **Input schema** — the payload must validate against the `ToolSpec.input_schema`.
3. **Side-effect approval** — `external_write` tools need an approval record unless a policy
   `approvals.pre_approved` pattern matches `tool:action_id` or `tool:url`. Without one the run
   **pauses** on `awaiting_approval` and the pending action is recorded.
4. **Budget** — `max_tool_calls` and `max_cost_usd` are checked before execution.
5. **Execution** with a wall-time limit (`ToolSpec.limits.wall_s`); tools get the `RunContext`
   only if they declare a `ctx` parameter, and never see provider credentials.
6. **Output schema** — the result is validated; unknown keys are stripped when the schema says
   `additionalProperties: false`, so a tool cannot smuggle anything else back into the run.
7. **Usage + event** — a `ToolCallUsage` is settled and `tool.called` is emitted.

## Built-in families

| tool | side effect | purpose |
|---|---|---|
| `document.inspect` | none | asset manifest (pages, text layer, sizes) |
| `document.extract_text` | none | native text with character boxes (the document index) |
| `document.render_page` | local_artifact | PNG artifact for one page |
| `document.extract` | external_read | schema-guided extraction (via `ctx.extract`) |
| `compute.align_line_items` / `check_arithmetic` / `reconcile` | none | deterministic reconciliation |
| `search.text_index` | none | BM25 over the run's document indexes |
| `convert.office_to_pdf` | local_artifact | LibreOffice conversion (used automatically at admission) |
| `http.post` | **external_write** | POST JSON with an `Idempotency-Key` = action id |
| `review.request` | none | ask for human review |

Define your own with the decorator; it is registered globally and tagged for the broker:

```python
from hawser.tools import tool
from hawser.models import SideEffect

@tool("erp.post_invoice", description="...", side_effect=SideEffect.external_write,
      input_schema={...}, output_schema={...})
def post_invoice(invoice: dict, action_id: str = "") -> dict: ...
```

In a workflow, `ctx.step("key", fn, ...)` routes registered tools through the broker; plain
functions run directly. `ctx.tool("http.post", url=..., json=..., action_id="inv-42")` calls a
tool by name.

## Approvals and resume

```bash
hawser approvals list                       # pending actions: tool, action id, payload
hawser approve run_… --comment "ok"         # decide every pending action, then resume the run
hawser approve run_… --reject
```

Resume re-executes the run: every finished step replays from its checkpoint (no model calls are
repeated), the broker finds the decision and either runs the action or fails the run with
`action_rejected`. Stable `action_id`s are what make retries safe: the same action is never
recorded twice, and `http.post` sends it as `Idempotency-Key`.
