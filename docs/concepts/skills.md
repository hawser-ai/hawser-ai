# Skills

A skill is a versioned, reusable workflow bundle (blueprint §12). It lives in a directory:

```
skills/invoice-po-reconciliation/
  skill.yaml        manifest: inputs, output schema, mode, tools, validators, evidence, policy
  schemas.py        Pydantic models; the manifest refers to them as schemas.py:ClassName
  workflow.py       def run(ctx: WorkflowContext) -> dict      (mode: workflow)
  validators.py     skill-local validators (objects with id, version, validate(ctx))
  prompts/*.md      system prompts referenced from the manifest
  examples/         <case>/inputs/*, <case>/expected.json, cassette/ (for `hawser skill test`)
  CHANGELOG.md
```

Search paths: `./skills`, `~/.hawser/skills`, and `skill_paths` in `hawser.toml`.

## Running a skill

```bash
hawser run --skill invoice-po-reconciliation -f invoice.pdf -f purchase_order.pdf
```

```python
hawser.run(skill="invoice-po-reconciliation", inputs=["invoice.pdf", "purchase_order.pdf"])
```

Inputs bind to the manifest's declared inputs by file name (`invoice*.pdf` → `invoice`), then
by position. `--tools` may only narrow what the manifest allows.

## Writing a workflow

```python
from hawser.workflow import WorkflowContext
from schemas import InvoiceRecord, PurchaseOrderRecord

def run(ctx: WorkflowContext) -> dict:
    invoice = ctx.extract("invoice", InvoiceRecord, prompt="extract_invoice")
    po = ctx.extract("purchase_order", PurchaseOrderRecord, prompt="extract_po")
    ctx.attach("/invoice", invoice)          # expose fields + evidence under /invoice
    ctx.attach("/purchase_order", po)
    alignment = ctx.step("align", align_line_items, invoice.result["line_items"], po.result["line_items"])
    rec = ctx.step("reconcile", reconcile, invoice.result, po.result, alignment)
    ctx.derive("/reconciliation/total_delta", expression="invoice.total - purchase_order.total",
               inputs=["/invoice/total", "/purchase_order/total"], value=rec.total_delta)
    ctx.flag("invoice_po.unmatched_invoice_item", "no counterpart on the PO",
             field="/invoice/line_items/0/description")   # becomes a validation issue
    return {"invoice": invoice.result, "purchase_order": po.result, "reconciliation": rec.model_dump()}
```

- `ctx.extract` and `ctx.step` are **checkpointed by key**: a resumed run skips completed steps.
- `ctx.attach(prefix, extraction)` carries field evidence into the final result.
- `ctx.derive(pointer, ...)` records calculation evidence linking a computed value to its inputs.
- `ctx.flag(...)` raises findings from workflow logic; `ctx.parallel`/`ctx.loop` are bounded by policy.
- Tools used through `ctx.step` must be listed under `tools:` in the manifest.

## Validators in the manifest

```yaml
validators:
  - id: rules.formats
    params: { formats: { /invoice/invoice_date: date, /invoice/currency: iso4217 } }
  - id: rules.expressions
    params:
      rules:
        - name: totals
          expr: approx(invoice.subtotal + invoice.tax, invoice.total, 0.02)
          message: subtotal + tax does not equal the printed total
          field: /invoice/total
          repair: review
  - invoice_po.summary_consistent      # skill-local, from validators.py
evidence:
  required: [/invoice/total, /purchase_order/po_number]
  preferred: [/invoice/line_items/*/line_total]
```

See [validation.md](validation.md) for the rule language.

## Lifecycle commands

```bash
hawser skill new my-skill            # scaffold
hawser skill validate skills/my-skill
hawser skill install skills/my-skill # records hawser.lock; refuses widened permissions without --accept-permissions
hawser skill test skills/my-skill    # runs examples/ (replay by default; --live to call the provider)
hawser skill export skills/my-skill  # tarball with the bundle hash in the name
hawser skill list
```

`hawser.lock` pins name, version, bundle hash and the tools/models the skill held when it was
installed. `hawser skill list` shows `drift` when a bundle's contents changed since locking.
