"""Deterministic computation tools: line-item alignment, arithmetic checks, reconciliation.

Pure functions over plain dicts, so they are reusable from workflows, validators, synthetic
dataset builders and tests, and never touch a model.
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from hawser.evidence.anchor import normalise
from hawser.tools.registry import tool

DEFAULT_TOLERANCE = 0.01
FUZZY_DESCRIPTION_THRESHOLD = 90.0


class LineMatch(BaseModel):
    left: int
    right: int
    method: str  # sku | description | qty_price
    score: float


class Alignment(BaseModel):
    matches: list[LineMatch] = Field(default_factory=list)
    unmatched_left: list[int] = Field(default_factory=list)
    unmatched_right: list[int] = Field(default_factory=list)


def _norm_key(v: Any) -> str:
    return normalise(str(v))[0].replace(" ", "").replace("-", "") if v not in (None, "") else ""


def _num(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _close(a: Any, b: Any, tol: float = DEFAULT_TOLERANCE) -> bool:
    fa, fb = _num(a), _num(b)
    return fa is not None and fb is not None and math.isclose(fa, fb, abs_tol=tol, rel_tol=0.0)


@tool(
    "compute.align_line_items",
    description="Align two line-item lists by SKU, then description, then quantity+price.",
)
def align_line_items(  # noqa: PLR0912
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
    *,
    key: str = "sku",
    description: str = "description",
    quantity: str = "quantity",
    price: str = "unit_price",
    fuzzy_threshold: float = FUZZY_DESCRIPTION_THRESHOLD,
) -> Alignment:
    out = Alignment()
    free_left = list(range(len(left)))
    free_right = list(range(len(right)))

    # 1. exact key
    for i in list(free_left):
        k = _norm_key(left[i].get(key))
        if not k:
            continue
        for j in free_right:
            if _norm_key(right[j].get(key)) == k:
                out.matches.append(LineMatch(left=i, right=j, method="sku", score=1.0))
                free_left.remove(i)
                free_right.remove(j)
                break
    # 2. fuzzy description (best score first, greedy)
    pairs: list[tuple[float, int, int]] = []
    for i in free_left:
        dl = normalise(str(left[i].get(description) or ""))[0]
        if not dl:
            continue
        for j in free_right:
            dr = normalise(str(right[j].get(description) or ""))[0]
            if not dr:
                continue
            score = fuzz.token_set_ratio(dl, dr)
            if score >= fuzzy_threshold:
                pairs.append((score, i, j))
    for score, i, j in sorted(pairs, key=lambda p: (-p[0], p[1], p[2])):
        if i in free_left and j in free_right:
            out.matches.append(LineMatch(left=i, right=j, method="description", score=score / 100))
            free_left.remove(i)
            free_right.remove(j)
    # 3. same quantity and unit price
    for i in list(free_left):
        for j in list(free_right):
            if _close(left[i].get(quantity), right[j].get(quantity)) and _close(
                left[i].get(price), right[j].get(price)
            ):
                out.matches.append(LineMatch(left=i, right=j, method="qty_price", score=0.6))
                free_left.remove(i)
                free_right.remove(j)
                break
    out.matches.sort(key=lambda m: (m.left, m.right))
    out.unmatched_left = free_left
    out.unmatched_right = free_right
    return out


class ArithmeticCheck(BaseModel):
    line_total_mismatches: list[int] = Field(default_factory=list)
    computed_subtotal: float | None = None
    subtotal_ok: bool | None = None
    computed_total: float | None = None
    total_ok: bool | None = None


@tool(
    "compute.check_arithmetic",
    description="Recompute line totals, subtotal and total and compare with printed values.",
)
def check_arithmetic(
    items: list[dict[str, Any]],
    *,
    subtotal: Any = None,
    tax: Any = None,
    total: Any = None,
    tolerance: float = DEFAULT_TOLERANCE,
) -> ArithmeticCheck:
    out = ArithmeticCheck()
    running = 0.0
    complete = True
    for i, item in enumerate(items):
        q, p, lt = (
            _num(item.get("quantity")),
            _num(item.get("unit_price")),
            _num(item.get("line_total")),
        )
        if q is not None and p is not None and lt is not None:
            if not math.isclose(
                q * p, lt, abs_tol=max(tolerance, 0.005 * max(1.0, abs(lt))), rel_tol=0.0
            ):
                out.line_total_mismatches.append(i)
            running += lt
        elif lt is not None:
            running += lt
        else:
            complete = False
    if complete and items:
        out.computed_subtotal = round(running, 2)
        if subtotal is not None:
            out.subtotal_ok = _close(
                out.computed_subtotal, subtotal, tolerance * max(1, len(items))
            )
        base = _num(subtotal) if subtotal is not None else out.computed_subtotal
        t = _num(tax) or 0.0
        if base is not None:
            out.computed_total = round(base + t, 2)
            if total is not None:
                out.total_ok = _close(out.computed_total, total, tolerance)
    return out


class MatchedItem(BaseModel):
    sku: str | None = None
    description: str
    match_method: str
    invoice_quantity: float | None = None
    po_quantity: float | None = None
    invoice_unit_price: float | None = None
    po_unit_price: float | None = None
    quantity_delta: float | None = None
    price_delta: float | None = None
    amount_delta: float | None = None
    invoice_index: int
    po_index: int


class HeaderDiscrepancy(BaseModel):
    field: str
    invoice_value: Any = None
    po_value: Any = None


class Reconciliation(BaseModel):
    matched_items: list[MatchedItem] = Field(default_factory=list)
    unmatched_invoice_items: list[dict[str, Any]] = Field(default_factory=list)
    unmatched_po_items: list[dict[str, Any]] = Field(default_factory=list)
    header_discrepancies: list[HeaderDiscrepancy] = Field(default_factory=list)
    invoice_total: float | None = None
    po_total: float | None = None
    total_delta: float | None = None
    discrepancy_count: int = 0
    summary_status: str = "match"  # match | discrepancies | unreadable


@tool(
    "compute.reconcile",
    description="Reconcile an invoice against a purchase order using an alignment.",
)
def reconcile(
    invoice: dict[str, Any],
    po: dict[str, Any],
    alignment: Alignment,
    *,
    tolerance: float = DEFAULT_TOLERANCE,
) -> Reconciliation:
    inv_items: list[dict[str, Any]] = list(invoice.get("line_items") or [])
    po_items: list[dict[str, Any]] = list(po.get("line_items") or [])
    out = Reconciliation()
    for m in alignment.matches:
        a, b = inv_items[m.left], po_items[m.right]
        qa, qb = _num(a.get("quantity")), _num(b.get("quantity"))
        pa, pb = _num(a.get("unit_price")), _num(b.get("unit_price"))
        la, lb = _num(a.get("line_total")), _num(b.get("line_total"))
        item = MatchedItem(
            sku=a.get("sku") or b.get("sku"),
            description=str(a.get("description") or b.get("description") or ""),
            match_method=m.method,
            invoice_quantity=qa,
            po_quantity=qb,
            invoice_unit_price=pa,
            po_unit_price=pb,
            quantity_delta=round(qa - qb, 4) if qa is not None and qb is not None else None,
            price_delta=round(pa - pb, 4) if pa is not None and pb is not None else None,
            amount_delta=round(la - lb, 2) if la is not None and lb is not None else None,
            invoice_index=m.left,
            po_index=m.right,
        )
        out.matched_items.append(item)
        if (item.quantity_delta and abs(item.quantity_delta) > 1e-9) or (
            item.price_delta and abs(item.price_delta) > tolerance
        ):
            out.discrepancy_count += 1
    out.unmatched_invoice_items = [inv_items[i] for i in alignment.unmatched_left]
    out.unmatched_po_items = [po_items[j] for j in alignment.unmatched_right]
    out.discrepancy_count += len(out.unmatched_invoice_items) + len(out.unmatched_po_items)

    if (
        invoice.get("currency")
        and po.get("currency")
        and str(invoice["currency"]).upper() != str(po["currency"]).upper()
    ):
        out.header_discrepancies.append(
            HeaderDiscrepancy(
                field="currency", invoice_value=invoice["currency"], po_value=po["currency"]
            )
        )
    ref, po_no = invoice.get("purchase_order_number"), po.get("po_number")
    if ref and po_no and _norm_key(ref) != _norm_key(po_no):
        out.header_discrepancies.append(
            HeaderDiscrepancy(field="purchase_order_number", invoice_value=ref, po_value=po_no)
        )
    va, vb = invoice.get("vendor_name"), po.get("vendor_name")
    if va and vb and fuzz.token_set_ratio(normalise(str(va))[0], normalise(str(vb))[0]) < 85:
        out.header_discrepancies.append(
            HeaderDiscrepancy(field="vendor_name", invoice_value=va, po_value=vb)
        )
    out.discrepancy_count += len(out.header_discrepancies)

    out.invoice_total, out.po_total = _num(invoice.get("total")), _num(po.get("total"))
    if out.invoice_total is not None and out.po_total is not None:
        out.total_delta = round(out.invoice_total - out.po_total, 2)
    if not inv_items or not po_items:
        out.summary_status = "unreadable"
    elif out.discrepancy_count == 0 and (
        out.total_delta is None or abs(out.total_delta) <= tolerance
    ):
        out.summary_status = "match"
    else:
        out.summary_status = "discrepancies"
    return out


__all__ = [
    "Alignment",
    "ArithmeticCheck",
    "HeaderDiscrepancy",
    "LineMatch",
    "MatchedItem",
    "Reconciliation",
    "align_line_items",
    "check_arithmetic",
    "reconcile",
]
