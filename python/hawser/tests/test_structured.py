from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import BaseModel

from hawser.models.common import JsonSchema
from hawser.structured import envelope_schema, inline_refs, to_json_schema, unwrap, validate_against
from hawser.structured.schema import ENVELOPE_KEYS, FOUND_DESCRIPTION, count_unions
from hawser.testing import build_envelope
from hawser_synth.invoice import GeneratedInvoice


def _leaves(node: JsonSchema, path: str = "") -> list[tuple[str, JsonSchema]]:
    if "anyOf" in node and len(node["anyOf"]) == 2:
        inner = [o for o in node["anyOf"] if o.get("type") != "null"]
        if inner and ("properties" in inner[0] or "items" in inner[0]):
            return _leaves(inner[0], path)
    if "properties" in node:
        out: list[tuple[str, JsonSchema]] = []
        for k, v in node["properties"].items():
            out.extend(_leaves(v, f"{path}/{k}"))
        return out
    if "items" in node:
        return _leaves(node["items"], f"{path}/*")
    return [(path, node)]


def test_inline_refs_resolves_defs(invoice_schema: JsonSchema) -> None:
    assert "$defs" not in invoice_schema
    assert invoice_schema["properties"]["line_items"]["items"]["properties"]["sku"]


def test_inline_refs_rejects_recursion() -> None:
    class Node(BaseModel):
        children: list[Node] = []

    with pytest.raises(ValueError, match="recursive"):
        inline_refs(to_json_schema(Node))


def test_envelope_wraps_every_leaf(invoice_schema: JsonSchema) -> None:
    env = envelope_schema(invoice_schema)
    # one envelope (one "/value" leaf) per leaf of the original schema
    found = sum(1 for ptr, _ in _leaves(env) if ptr.endswith("/value"))
    assert found == len(_leaves(invoice_schema))

    def check(node: JsonSchema) -> None:
        if "properties" in node and set(node["properties"]) == set(ENVELOPE_KEYS):
            assert node["required"] == ENVELOPE_KEYS
            assert node["additionalProperties"] is False
            assert node["properties"]["found"] == {
                "type": "boolean",
                "description": FOUND_DESCRIPTION,
            }
            assert "anyOf" not in json.dumps(node["properties"]["value"])
            return
        if "properties" in node:
            assert node["additionalProperties"] is False
            assert node["required"] == list(node["properties"])
            for v in node["properties"].values():
                check(v)
        elif "items" in node:
            check(node["items"])
        elif "anyOf" in node:
            for o in node["anyOf"]:
                if o.get("type") != "null":
                    check(o)

    check(env)


def test_unwrap_round_trip(
    invoice: GeneratedInvoice, invoice_schema: JsonSchema, truth_envelope: Any
) -> None:
    u = unwrap(truth_envelope, invoice_schema)
    assert u.parse_issues == []
    assert u.result == invoice.truth.model_dump(mode="json")
    assert validate_against(invoice_schema, u.result) == []
    assert "/invoice_number" in u.field_meta
    assert "/line_items/0/unit_price" in u.field_meta
    anchored = {a.pointer for a in u.anchors}
    assert anchored == set(invoice.source_texts)


def test_unwrap_unknown_and_malformed(
    invoice_schema: JsonSchema, invoice: GeneratedInvoice
) -> None:
    env = build_envelope(
        invoice_schema,
        invoice.truth.model_dump(mode="json"),
        source_texts=invoice.source_texts,
        unknown={"/vendor_name"},
    )
    env["total"] = 12.5  # not an envelope object
    u = unwrap(env, invoice_schema)
    assert u.result["vendor_name"] is None
    assert u.field_meta["/vendor_name"].status == "unknown"
    assert u.result["total"] is None
    assert any(p.startswith("/total") for p in u.parse_issues)
    errors = validate_against(invoice_schema, u.result)
    assert ("/vendor_name", "None is not of type 'string'") in errors


def test_envelope_has_no_unions(invoice_schema: JsonSchema) -> None:
    assert count_unions(envelope_schema(invoice_schema)) == 0
