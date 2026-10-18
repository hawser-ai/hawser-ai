"""Structured outputs (blueprint §14, ADR-0005): schema normalisation, envelope, unwrapping."""

from hawser.structured.pointers import escape_token, pointer
from hawser.structured.schema import envelope_schema, inline_refs, to_json_schema
from hawser.structured.unwrap import Anchor, Unwrapped, unwrap, validate_against

__all__ = [
    "Anchor",
    "Unwrapped",
    "envelope_schema",
    "escape_token",
    "inline_refs",
    "pointer",
    "to_json_schema",
    "unwrap",
    "validate_against",
]
