"""Build the ``invoice-po`` evaluation dataset: ``python -m hawser_synth.dataset OUT_DIR [--n 60]``.

Expected outputs are computed from the ground truths with the same deterministic alignment
and reconciliation functions the skill uses, so they are exact by construction.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import yaml

from hawser.tools.compute import align_line_items, reconcile
from hawser_synth.purchase_order import generate_pair

DATASET_ID = "invoice-po"
DATASET_VERSION = "v1"


def expected_for(pair_invoice: dict[str, object], pair_po: dict[str, object]) -> dict[str, object]:
    inv_items = list(pair_invoice.get("line_items") or [])  # type: ignore[call-overload]
    po_items = list(pair_po.get("line_items") or [])  # type: ignore[call-overload]
    alignment = align_line_items(inv_items, po_items)  # type: ignore[arg-type]
    rec = reconcile(pair_invoice, pair_po, alignment)  # type: ignore[arg-type]
    return {
        "invoice": pair_invoice,
        "purchase_order": pair_po,
        "reconciliation": rec.model_dump(mode="json"),
    }


def expected_status(expected: dict[str, object], *, scanned: bool) -> str:
    """What the skill should decide. Scanned pairs need review until OCR lands (Phase 7):
    the skill requires evidence on totals and a page without a text layer cannot anchor it."""
    # Phase 7: scanned pages are OCR'd and carry box evidence, so they are judged like native.
    _ = scanned
    rec = expected.get("reconciliation")
    summary = rec.get("summary_status") if isinstance(rec, dict) else None
    return "accepted" if summary == "match" else "needs_review"


def build(
    out: Path, *, n: int = 60, seed: int = 1000, scanned_fraction: float = 0.2, smoke: int = 8
) -> Path:
    # Recorded model responses are expensive; a rebuild never touches them.
    cassettes = out / "cassettes"
    keep = out.parent / f".{out.name}.cassettes.keep"
    if cassettes.exists():
        if keep.exists():
            shutil.rmtree(keep)
        shutil.move(str(cassettes), str(keep))
    if out.exists():
        shutil.rmtree(out)
    cases_dir = out / "cases"
    cases_dir.mkdir(parents=True)
    if keep.exists():
        shutil.move(str(keep), str(cassettes))
    cases: list[dict[str, object]] = []
    for i in range(n):
        case_seed = seed + i
        scanned = (
            (i % int(1 / scanned_fraction)) == (int(1 / scanned_fraction) - 1)
            if scanned_fraction
            else False
        )
        pair = generate_pair(case_seed, scanned=scanned)
        cid = f"pair-{case_seed:05d}"
        d = cases_dir / cid
        (d / "inputs").mkdir(parents=True)
        (d / "inputs" / "invoice.pdf").write_bytes(pair.invoice.pdf)
        (d / "inputs" / "purchase_order.pdf").write_bytes(pair.purchase_order.pdf)
        expected = expected_for(
            pair.invoice.truth.model_dump(mode="json"),
            pair.purchase_order.truth.model_dump(mode="json"),
        )
        (d / "expected.json").write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")
        tags = ["scanned" if scanned else "native", *[f"disc:{k}" for k in pair.discrepancies]]
        if not pair.discrepancies:
            tags.append("disc:none")
        if i < smoke:
            tags.append("smoke")
        cases.append(
            {
                "id": cid,
                "inputs": {
                    "invoice": f"cases/{cid}/inputs/invoice.pdf",
                    "purchase_order": f"cases/{cid}/inputs/purchase_order.pdf",
                },
                "expected": f"cases/{cid}/expected.json",
                "tags": tags,
                "expected_status": expected_status(expected, scanned=scanned),
            }
        )
    manifest = {
        "id": DATASET_ID,
        "version": DATASET_VERSION,
        "description": "Synthetic invoice / purchase-order pairs with injected discrepancies.",
        "skill": "invoice-po-reconciliation",
        "generator": {
            "module": "hawser_synth.dataset",
            "seed": seed,
            "n": n,
            "scanned_fraction": scanned_fraction,
        },
        "scoring": {
            "list_keys": {
                "/invoice/line_items": "sku",
                "/purchase_order/line_items": "sku",
                "/reconciliation/matched_items": "sku",
            },
            "ignore": [
                "/reconciliation/matched_items/*/invoice_index",
                "/reconciliation/matched_items/*/po_index",
            ],
            "numeric_tolerance": 0.011,
        },
        "cases": cases,
    }
    (out / "dataset.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", default="datasets/invoice-po/v1")
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--seed", type=int, default=1000)
    args = ap.parse_args(argv)
    path = build(Path(args.out), n=args.n, seed=args.seed)
    print(f"dataset written to {path} ({args.n} cases)")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
