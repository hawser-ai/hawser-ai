"""Output contracts for invoice ↔ purchase-order reconciliation."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class LineItem(BaseModel):
    sku: str | None = Field(default=None, description="Stock-keeping unit or item code, if printed")
    description: str
    quantity: float
    unit_price: float
    line_total: float


class InvoiceRecord(BaseModel):
    invoice_number: str
    invoice_date: str = Field(description="ISO 8601 date, YYYY-MM-DD")
    due_date: str | None = Field(default=None, description="ISO 8601 date, YYYY-MM-DD")
    vendor_name: str
    customer_name: str
    purchase_order_number: str | None = Field(default=None, description="The PO reference printed on the invoice")
    currency: str = Field(description="ISO 4217 code")
    line_items: list[LineItem]
    subtotal: float
    tax: float
    total: float


class PurchaseOrderRecord(BaseModel):
    po_number: str
    po_date: str = Field(description="ISO 8601 date, YYYY-MM-DD")
    vendor_name: str = Field(description="The supplier the order is placed with")
    buyer_name: str = Field(description="The organisation placing the order")
    currency: str = Field(description="ISO 4217 code")
    line_items: list[LineItem]
    subtotal: float
    tax: float
    total: float


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
    matched_items: list[MatchedItem]
    unmatched_invoice_items: list[LineItem]
    unmatched_po_items: list[LineItem]
    header_discrepancies: list[HeaderDiscrepancy]
    invoice_total: float | None = None
    po_total: float | None = None
    total_delta: float | None = None
    discrepancy_count: int
    summary_status: str = Field(description="match | discrepancies | unreadable")


class ReconciliationResult(BaseModel):
    invoice: InvoiceRecord
    purchase_order: PurchaseOrderRecord
    reconciliation: Reconciliation
