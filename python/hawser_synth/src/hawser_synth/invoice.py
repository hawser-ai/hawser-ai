"""Deterministic synthetic invoices (PDF + ground truth).

The same ``seed`` always yields the same bytes, so fixtures and cassettes stay stable.
Two layouts exist; Phase 2 adds more. ``scanned=True`` produces an image-only PDF (no text
layer) to exercise the ``needs_ocr`` path.
"""

from __future__ import annotations

import io
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import pypdfium2 as pdfium
from pydantic import BaseModel, Field
from reportlab.lib.pagesizes import A4, LETTER
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from hawser_media.pdf import PDFIUM_LOCK


class LineItem(BaseModel):
    sku: str | None = Field(default=None, description="Vendor stock-keeping unit, if printed")
    description: str
    quantity: float
    unit_price: float
    line_total: float


class Invoice(BaseModel):
    invoice_number: str
    invoice_date: str = Field(description="ISO 8601 date, YYYY-MM-DD")
    due_date: str | None = Field(default=None, description="ISO 8601 date, YYYY-MM-DD")
    vendor_name: str
    customer_name: str
    purchase_order_number: str | None = None
    currency: str = Field(description="ISO 4217 code")
    line_items: list[LineItem]
    subtotal: float
    tax: float
    total: float


def invoice_schema() -> dict[str, Any]:
    return Invoice.model_json_schema()


_VENDORS = [
    ("Northwind Components Ltd", "14 Harbour Road, Bristol BS1 4QA, United Kingdom"),
    ("Aurora Fasteners GmbH", "Industriestraße 9, 70565 Stuttgart, Germany"),
    ("Pacific Rim Supply Co.", "1200 Mission St, San Francisco, CA 94103, USA"),
    ("Keystone Industrial Pty", "88 Collins Street, Melbourne VIC 3000, Australia"),
]
_CUSTOMERS = [
    "Larkspur Software Ltd",
    "Meridian Robotics Inc.",
    "Bluewater Marine Services",
    "Hollis & Partners LLP",
]
_PRODUCTS = [
    ("HX-1042", "Hex bolt M8 x 40 zinc plated, box of 100"),
    ("WS-2210", "Flat washer M8 stainless, pack of 200"),
    ("NT-0808", "Nyloc nut M8, pack of 100"),
    ("CB-5503", "Cable gland PG13.5 IP68"),
    ("BR-7781", "Ball bearing 6204-2RS"),
    ("SP-3310", "Spring pin 6 x 30"),
    ("GK-9004", "Gasket kit, nitrile, assorted"),
    ("TM-1180", "Thermal paste 10 g tube"),
    ("LB-4420", "Lithium grease 400 g cartridge"),
    ("SC-6007", "Self-tapping screw 4.2 x 16, box of 500"),
]
_CURRENCIES = [("GBP", "£"), ("EUR", "€"), ("USD", "$")]


@dataclass
class GeneratedInvoice:
    pdf: bytes
    truth: Invoice
    layout: int
    scanned: bool
    #: JSON pointer -> the exact string printed on the page for that value.
    source_texts: dict[str, str] = field(default_factory=dict)


def money(sym: str, value: float) -> str:
    return f"{sym}{value:,.2f}"


def generate_invoice(
    seed: int, *, scanned: bool = False, layout: int | None = None
) -> GeneratedInvoice:
    rng = random.Random(seed)
    vendor, vendor_addr = rng.choice(_VENDORS)
    customer = rng.choice(_CUSTOMERS)
    currency, sym = rng.choice(_CURRENCIES)
    inv_date = date(2026, 1, 1) + timedelta(days=rng.randint(0, 280))
    due = inv_date + timedelta(days=rng.choice([14, 30, 45]))
    number = f"INV-{2026}-{seed:05d}"
    po = f"PO-{rng.randint(10000, 99999)}" if rng.random() < 0.85 else None
    chosen = rng.sample(_PRODUCTS, k=rng.randint(3, 7))
    items: list[LineItem] = []
    for sku, desc in chosen:
        qty = float(rng.choice([1, 2, 3, 4, 5, 10, 12, 24, 50]))
        unit = round(rng.uniform(1.5, 180.0), 2)
        items.append(
            LineItem(
                sku=sku,
                description=desc,
                quantity=qty,
                unit_price=unit,
                line_total=round(qty * unit, 2),
            )
        )
    subtotal = round(sum(i.line_total for i in items), 2)
    tax_rate = rng.choice([0.0, 0.05, 0.10, 0.19, 0.20])
    tax = round(subtotal * tax_rate, 2)
    total = round(subtotal + tax, 2)
    truth = Invoice(
        invoice_number=number,
        invoice_date=inv_date.isoformat(),
        due_date=due.isoformat(),
        vendor_name=vendor,
        customer_name=customer,
        purchase_order_number=po,
        currency=currency,
        line_items=items,
        subtotal=subtotal,
        tax=tax,
        total=total,
    )
    chosen_layout = layout if layout is not None else rng.choice([0, 1])
    pdf = _render(truth, vendor_addr, sym, tax_rate, chosen_layout)
    if scanned:
        pdf = rasterise(pdf, dpi=200, rotate=rng.uniform(-1.5, 1.5))
    return GeneratedInvoice(
        pdf=pdf,
        truth=truth,
        layout=chosen_layout,
        scanned=scanned,
        source_texts=_source_texts(truth, sym),
    )


def _source_texts(inv: Invoice, sym: str) -> dict[str, str]:
    out: dict[str, str] = {
        "/invoice_number": inv.invoice_number,
        "/invoice_date": inv.invoice_date,
        "/vendor_name": inv.vendor_name,
        "/customer_name": inv.customer_name,
        "/currency": inv.currency,
        "/subtotal": money(sym, inv.subtotal),
        "/tax": money(sym, inv.tax),
        "/total": money(sym, inv.total),
    }
    if inv.due_date:
        out["/due_date"] = inv.due_date
    if inv.purchase_order_number:
        out["/purchase_order_number"] = inv.purchase_order_number
    for i, item in enumerate(inv.line_items):
        if item.sku:
            out[f"/line_items/{i}/sku"] = item.sku
        out[f"/line_items/{i}/description"] = item.description
        out[f"/line_items/{i}/quantity"] = f"{item.quantity:g}"
        out[f"/line_items/{i}/unit_price"] = money(sym, item.unit_price)
        out[f"/line_items/{i}/line_total"] = money(sym, item.line_total)
    return out


def _render(  # noqa: PLR0915
    inv: Invoice, vendor_addr: str, sym: str, tax_rate: float, layout: int
) -> bytes:
    buf = io.BytesIO()
    pagesize = LETTER if layout == 0 else A4
    c = canvas.Canvas(buf, pagesize=pagesize, invariant=True)
    width, height = pagesize
    margin = 54
    y = height - margin

    if layout == 0:
        c.setFont("Helvetica-Bold", 20)
        c.drawString(margin, y, "INVOICE")
        c.setFont("Helvetica", 10)
        c.drawRightString(width - margin, y, inv.vendor_name)
        c.drawRightString(width - margin, y - 14, vendor_addr)
        y -= 50
        c.setFont("Helvetica", 10)
        c.drawString(margin, y, f"Invoice No: {inv.invoice_number}")
        c.drawString(margin, y - 14, f"Invoice Date: {inv.invoice_date}")
        c.drawString(margin, y - 28, f"Due Date: {inv.due_date}")
        if inv.purchase_order_number:
            c.drawString(margin, y - 42, f"PO Number: {inv.purchase_order_number}")
        c.drawString(width / 2, y, "Bill To:")
        c.drawString(width / 2, y - 14, inv.customer_name)
        c.drawString(width / 2, y - 28, f"Currency: {inv.currency}")
        y -= 80
    else:
        c.setFont("Helvetica-Bold", 14)
        c.drawString(margin, y, inv.vendor_name)
        c.setFont("Helvetica", 9)
        c.drawString(margin, y - 12, vendor_addr)
        c.setFont("Helvetica-Bold", 22)
        c.drawRightString(width - margin, y, "TAX INVOICE")
        y -= 44
        c.setFont("Helvetica", 10)
        c.drawRightString(width - margin, y, f"Invoice # {inv.invoice_number}")
        c.drawRightString(width - margin, y - 14, f"Date: {inv.invoice_date}")
        c.drawRightString(width - margin, y - 28, f"Payment due: {inv.due_date}")
        c.drawString(margin, y, "Customer:")
        c.drawString(margin, y - 14, inv.customer_name)
        if inv.purchase_order_number:
            c.drawString(margin, y - 28, f"Your reference: {inv.purchase_order_number}")
        c.drawString(margin, y - 42, f"All amounts in {inv.currency}")
        y -= 76

    # Table header
    cols = (margin, margin + 70, width - margin - 200, width - margin - 120, width - margin)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(cols[0], y, "SKU")
    c.drawString(cols[1], y, "Description")
    c.drawRightString(cols[2], y, "Qty")
    c.drawRightString(cols[3], y, "Unit Price")
    c.drawRightString(cols[4], y, "Amount")
    y -= 4
    c.line(margin, y, width - margin, y)
    y -= 14
    c.setFont("Helvetica", 9)
    for item in inv.line_items:
        c.drawString(cols[0], y, item.sku or "")
        c.drawString(cols[1], y, item.description)
        c.drawRightString(cols[2], y, f"{item.quantity:g}")
        c.drawRightString(cols[3], y, money(sym, item.unit_price))
        c.drawRightString(cols[4], y, money(sym, item.line_total))
        y -= 14
    y -= 6
    c.line(width / 2, y, width - margin, y)
    y -= 16
    c.setFont("Helvetica", 10)
    c.drawRightString(cols[3], y, "Subtotal:")
    c.drawRightString(cols[4], y, money(sym, inv.subtotal))
    y -= 14
    c.drawRightString(cols[3], y, f"Tax ({tax_rate * 100:g}%):")
    c.drawRightString(cols[4], y, money(sym, inv.tax))
    y -= 16
    c.setFont("Helvetica-Bold", 11)
    c.drawRightString(cols[3], y, "Total Due:")
    c.drawRightString(cols[4], y, money(sym, inv.total))
    y -= 40
    c.setFont("Helvetica", 8)
    c.drawString(
        margin,
        y,
        "Payment terms: net 30 days. Please quote the invoice number on all correspondence.",
    )
    c.showPage()
    c.save()
    return buf.getvalue()


def rasterise(pdf: bytes, *, dpi: float, rotate: float) -> bytes:
    """Re-encode page 1 as an image-only PDF (simulates a scan)."""
    with PDFIUM_LOCK:  # pdfium is not thread-safe; share the process-wide lock
        doc = pdfium.PdfDocument(pdf)
        page = doc[0]
        width_pt, height_pt = page.get_size()
        image = page.render(scale=dpi / 72.0).to_pil().convert("RGB")  # type: ignore[arg-type]
        page.close()
        doc.close()
    image = image.rotate(rotate, expand=False, fillcolor="white")
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width_pt, height_pt), invariant=True)
    c.drawImage(ImageReader(image), 0, 0, width=width_pt, height=height_pt)
    c.showPage()
    c.save()
    return buf.getvalue()


__all__ = ["GeneratedInvoice", "Invoice", "LineItem", "generate_invoice", "invoice_schema"]
