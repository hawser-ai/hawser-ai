"""Synthetic documents with exact ground truth. Used by examples, tests and eval datasets."""

from hawser_synth.invoice import Invoice, LineItem, generate_invoice, invoice_schema

__all__ = ["Invoice", "LineItem", "generate_invoice", "invoice_schema"]
