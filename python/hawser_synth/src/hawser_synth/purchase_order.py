"""Synthetic purchase orders paired with invoices, with controlled discrepancies."""

from __future__ import annotations

import io
import random
from dataclasses import dataclass, field
from datetime import date, timedelta

from pydantic import BaseModel, Field
from reportlab.lib.pagesizes import A4, LETTER
from reportlab.pdfgen import canvas

from hawser_synth.invoice import (
    GeneratedInvoice,
    Invoice,
    LineItem,
    generate_invoice,
    money,
    rasterise,
)

DISCREPANCY_KINDS = [
    "none",
    "qty_change",
    "price_change",
    "missing_on_po",
    "extra_on_po",
    "rounding",
    "currency",
]


class PurchaseOrder(BaseModel):
    po_number: str
    po_date: str = Field(description="ISO 8601 date, YYYY-MM-DD")
    vendor_name: str
    buyer_name: str
    currency: str = Field(description="ISO 4217 code")
    line_items: list[LineItem]
    subtotal: float
    tax: float
    total: float


@dataclass
class GeneratedPurchaseOrder:
    pdf: bytes
    truth: PurchaseOrder
    layout: int
    scanned: bool
    source_texts: dict[str, str] = field(default_factory=dict)


@dataclass
class GeneratedPair:
    invoice: GeneratedInvoice
    purchase_order: GeneratedPurchaseOrder
    discrepancies: list[str]
    seed: int


def po_from_invoice(
    inv: Invoice, rng: random.Random, discrepancies: list[str]
) -> tuple[PurchaseOrder, float]:
    items = [LineItem(**i.model_dump()) for i in inv.line_items]
    currency = inv.currency
    for kind in discrepancies:
        if kind == "qty_change" and items:
            i = rng.randrange(len(items))
            items[i].quantity = items[i].quantity + rng.choice([1, 2, 5, -1])
            if items[i].quantity <= 0:
                items[i].quantity = 1.0
            items[i].line_total = round(items[i].quantity * items[i].unit_price, 2)
        elif kind == "price_change" and items:
            i = rng.randrange(len(items))
            items[i].unit_price = round(items[i].unit_price * rng.choice([0.9, 1.1, 1.25]), 2)
            items[i].line_total = round(items[i].quantity * items[i].unit_price, 2)
        elif kind == "missing_on_po" and len(items) > 1:
            del items[rng.randrange(len(items))]
        elif kind == "extra_on_po":
            items.append(
                LineItem(
                    sku="ZZ-0001",
                    description="Expedited handling surcharge",
                    quantity=1.0,
                    unit_price=25.0,
                    line_total=25.0,
                )
            )
        elif kind == "rounding" and items:
            i = rng.randrange(len(items))
            items[i].line_total = round(items[i].line_total + 0.01, 2)
        elif kind == "currency":
            currency = "CHF" if currency != "CHF" else "SEK"
    subtotal = round(sum(i.line_total for i in items), 2)
    tax_rate = round(inv.tax / inv.subtotal, 4) if inv.subtotal else 0.0
    tax = round(subtotal * tax_rate, 2)
    po = PurchaseOrder(
        po_number=inv.purchase_order_number or f"PO-{rng.randint(10000, 99999)}",
        po_date=(
            date.fromisoformat(inv.invoice_date) - timedelta(days=rng.randint(3, 21))
        ).isoformat(),
        vendor_name=inv.vendor_name,
        buyer_name=inv.customer_name,
        currency=currency,
        line_items=items,
        subtotal=subtotal,
        tax=tax,
        total=round(subtotal + tax, 2),
    )
    return po, tax_rate


def _source_texts(po: PurchaseOrder, sym: str) -> dict[str, str]:
    out = {
        "/po_number": po.po_number,
        "/po_date": po.po_date,
        "/vendor_name": po.vendor_name,
        "/buyer_name": po.buyer_name,
        "/currency": po.currency,
        "/subtotal": money(sym, po.subtotal),
        "/tax": money(sym, po.tax),
        "/total": money(sym, po.total),
    }
    for i, item in enumerate(po.line_items):
        if item.sku:
            out[f"/line_items/{i}/sku"] = item.sku
        out[f"/line_items/{i}/description"] = item.description
        out[f"/line_items/{i}/quantity"] = f"{item.quantity:g}"
        out[f"/line_items/{i}/unit_price"] = money(sym, item.unit_price)
        out[f"/line_items/{i}/line_total"] = money(sym, item.line_total)
    return out


def _render_po(po: PurchaseOrder, sym: str, tax_rate: float, layout: int) -> bytes:  # noqa: PLR0915
    buf = io.BytesIO()
    pagesize = A4 if layout == 0 else LETTER
    c = canvas.Canvas(buf, pagesize=pagesize, invariant=True)
    width, height = pagesize
    margin = 50
    y = height - margin
    if layout == 0:
        c.setFont("Helvetica-Bold", 18)
        c.drawString(margin, y, "PURCHASE ORDER")
        c.setFont("Helvetica", 10)
        c.drawRightString(width - margin, y, f"PO Number: {po.po_number}")
        c.drawRightString(width - margin, y - 14, f"Order Date: {po.po_date}")
        y -= 44
        c.drawString(margin, y, "Supplier:")
        c.drawString(margin + 60, y, po.vendor_name)
        c.drawString(margin, y - 14, "Buyer:")
        c.drawString(margin + 60, y - 14, po.buyer_name)
        c.drawString(margin, y - 28, "Currency:")
        c.drawString(margin + 60, y - 28, po.currency)
        y -= 60
    else:
        c.setFont("Helvetica-Bold", 12)
        c.drawString(margin, y, po.buyer_name)
        c.setFont("Helvetica", 9)
        c.drawString(margin, y - 12, "Procurement Department")
        c.setFont("Helvetica-Bold", 20)
        c.drawRightString(width - margin, y, "Purchase Order")
        y -= 40
        c.setFont("Helvetica", 10)
        c.drawString(margin, y, f"Order No. {po.po_number}")
        c.drawString(margin, y - 14, f"Issued {po.po_date}")
        c.drawString(margin, y - 28, f"Amounts in {po.currency}")
        c.drawRightString(width - margin, y, "Vendor:")
        c.drawRightString(width - margin, y - 14, po.vendor_name)
        y -= 60
    cols = (margin, margin + 70, width - margin - 200, width - margin - 120, width - margin)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(cols[0], y, "Item")
    c.drawString(cols[1], y, "Description")
    c.drawRightString(cols[2], y, "Qty")
    c.drawRightString(cols[3], y, "Unit")
    c.drawRightString(cols[4], y, "Total")
    y -= 4
    c.line(margin, y, width - margin, y)
    y -= 14
    c.setFont("Helvetica", 9)
    for item in po.line_items:
        c.drawString(cols[0], y, item.sku or "")
        c.drawString(cols[1], y, item.description)
        c.drawRightString(cols[2], y, f"{item.quantity:g}")
        c.drawRightString(cols[3], y, money(sym, item.unit_price))
        c.drawRightString(cols[4], y, money(sym, item.line_total))
        y -= 14
    y -= 8
    c.line(width / 2, y, width - margin, y)
    y -= 16
    c.setFont("Helvetica", 10)
    c.drawRightString(cols[3], y, "Net:")
    c.drawRightString(cols[4], y, money(sym, po.subtotal))
    y -= 14
    c.drawRightString(cols[3], y, f"VAT ({tax_rate * 100:g}%):")
    c.drawRightString(cols[4], y, money(sym, po.tax))
    y -= 16
    c.setFont("Helvetica-Bold", 11)
    c.drawRightString(cols[3], y, "Order Total:")
    c.drawRightString(cols[4], y, money(sym, po.total))
    y -= 36
    c.setFont("Helvetica", 8)
    c.drawString(
        margin,
        y,
        "Please quote the PO number on invoices. Deliveries accepted Mon-Fri 08:00-16:00.",
    )
    c.showPage()
    c.save()
    return buf.getvalue()


_SYMBOLS = {"GBP": "£", "EUR": "€", "USD": "$", "CHF": "CHF ", "SEK": "SEK "}


def generate_pair(
    seed: int, *, scanned: bool = False, discrepancies: list[str] | None = None
) -> GeneratedPair:
    rng = random.Random(seed * 7919 + 17)
    invoice = generate_invoice(seed, scanned=scanned)
    if discrepancies is None:
        roll = rng.random()
        if roll < 0.4:
            discrepancies = []
        elif roll < 0.8:
            discrepancies = [rng.choice(DISCREPANCY_KINDS[1:])]
        else:
            discrepancies = rng.sample(DISCREPANCY_KINDS[1:], k=2)
    po, tax_rate = po_from_invoice(invoice.truth, rng, discrepancies)
    sym = _SYMBOLS.get(po.currency, "")
    layout = rng.choice([0, 1])
    pdf = _render_po(po, sym, tax_rate, layout)
    if scanned:
        pdf = rasterise(pdf, dpi=200, rotate=rng.uniform(-1.5, 1.5))
    gpo = GeneratedPurchaseOrder(
        pdf=pdf, truth=po, layout=layout, scanned=scanned, source_texts=_source_texts(po, sym)
    )
    return GeneratedPair(
        invoice=invoice, purchase_order=gpo, discrepancies=discrepancies, seed=seed
    )


__all__ = [
    "DISCREPANCY_KINDS",
    "GeneratedPair",
    "GeneratedPurchaseOrder",
    "PurchaseOrder",
    "generate_pair",
    "po_from_invoice",
]
