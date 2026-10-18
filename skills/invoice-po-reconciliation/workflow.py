"""invoice-po-reconciliation: extract both documents, align line items, reconcile."""

from __future__ import annotations

from typing import Any

from hawser.models import RepairKind, Severity
from hawser.tools.compute import Alignment, align_line_items, check_arithmetic, reconcile
from hawser.workflow import WorkflowContext

from schemas import InvoiceRecord, PurchaseOrderRecord  # bundle-local module


def run(ctx: WorkflowContext) -> dict[str, Any]:
    invoice = ctx.extract("invoice", InvoiceRecord, "Extract the supplier invoice.", prompt="extract_invoice")
    po = ctx.extract("purchase_order", PurchaseOrderRecord, "Extract the purchase order.", prompt="extract_po")
    ctx.attach("/invoice", invoice)
    ctx.attach("/purchase_order", po)
    inv: dict[str, Any] = invoice.result or {}
    pod: dict[str, Any] = po.result or {}

    alignment = ctx.step(
        "align", align_line_items, inv.get("line_items") or [], pod.get("line_items") or [], output_type=Alignment
    )
    arithmetic = ctx.step(
        "arithmetic",
        check_arithmetic,
        inv.get("line_items") or [],
        subtotal=inv.get("subtotal"),
        tax=inv.get("tax"),
        total=inv.get("total"),
    )
    for i in arithmetic.line_total_mismatches:
        ctx.flag(
            "invoice_po.line_arithmetic",
            "quantity × unit price does not equal the printed line total",
            severity=Severity.warning,
            field=f"/invoice/line_items/{i}/line_total",
            repair=RepairKind.ask_field,
        )

    rec = ctx.step("reconcile", reconcile, inv, pod, alignment)
    result = rec.model_dump(mode="json")

    for m in alignment.matches:
        ctx.derive(
            f"/reconciliation/matched_items/{alignment.matches.index(m)}/amount_delta",
            expression="invoice.line_total - po.line_total",
            inputs=[f"/invoice/line_items/{m.left}/line_total", f"/purchase_order/line_items/{m.right}/line_total"],
        )
    ctx.derive(
        "/reconciliation/total_delta",
        expression="invoice.total - purchase_order.total",
        inputs=["/invoice/total", "/purchase_order/total"],
        value=rec.total_delta,
    )
    ctx.mark("/reconciliation/discrepancy_count")
    ctx.mark("/reconciliation/summary_status")
    for i in alignment.unmatched_left:
        ctx.flag(
            "invoice_po.unmatched_invoice_item",
            "invoice line item has no counterpart on the purchase order",
            severity=Severity.warning,
            field=f"/invoice/line_items/{i}/description",
        )
    for j in alignment.unmatched_right:
        ctx.flag(
            "invoice_po.unmatched_po_item",
            "purchase order line item was not invoiced",
            severity=Severity.warning,
            field=f"/purchase_order/line_items/{j}/description",
        )
    return {"invoice": inv, "purchase_order": pod, "reconciliation": result}
