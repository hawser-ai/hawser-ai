from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from hawser.client import Client
from hawser.errors import PolicyViolation
from hawser.models import EventType, RunStatus
from hawser.models.common import JsonSchema
from hawser.models.review import ReviewStatus
from hawser.review import ReviewConflict, apply_pointer
from hawser.runtime.local import LocalRuntime
from hawser.testing import StaticAdapter, build_envelope
from hawser_synth import Invoice
from hawser_synth.invoice import GeneratedInvoice

RuntimeFactory = Callable[..., LocalRuntime]


def test_apply_pointer() -> None:
    base: dict[str, Any] = {"a": {"b": [1, 2]}, "c": 3}
    out = apply_pointer(base, "/a/b/1", 9)
    assert out["a"]["b"] == [1, 9]
    assert base["a"]["b"] == [1, 2]
    assert apply_pointer(base, "/new/key", "v")["new"]["key"] == "v"
    assert apply_pointer(None, "", {"x": 1}) == {"x": 1}


def _review_run(
    rt: LocalRuntime, invoice_pdf: Path, invoice: GeneratedInvoice, schema: JsonSchema
) -> str:
    env = build_envelope(
        schema,
        invoice.truth.model_dump(mode="json"),
        source_texts=invoice.source_texts,
        unknown={"/vendor_name"},
    )
    rt.gateway.adapters["anthropic"] = StaticAdapter(env)
    result = Client(rt).run("x", inputs=[invoice_pdf], output_schema=Invoice)
    assert result.status == RunStatus.needs_review
    assert result.revision_id is not None
    return result.run_id


def test_edit_and_approve_creates_reviewed_revision(
    runtime_factory: RuntimeFactory,
    invoice_pdf: Path,
    invoice: GeneratedInvoice,
    invoice_schema: JsonSchema,
) -> None:
    rt = runtime_factory(StaticAdapter({}))
    run_id = _review_run(rt, invoice_pdf, invoice, invoice_schema)
    machine = rt.get(run_id)
    assert machine is not None
    assert machine.result is not None
    assert machine.result["vendor_name"] is None

    review = rt.reviews.open(run_id, assigned_to="chetan")
    assert review.status == ReviewStatus.open
    assert review.version == 1
    assert rt.reviews.open(run_id).id == review.id  # idempotent

    edited = rt.reviews.edit(
        run_id,
        {"/vendor_name": "Northwind Components Ltd"},
        reviewer="chetan",
        comment="read from the header",
    )
    assert edited.version == 2
    assert edited.decisions[0].old is None
    assert edited.decisions[0].new == "Northwind Components Ltd"
    assert rt.reviews.current_result(run_id)["vendor_name"] == "Northwind Components Ltd"

    with pytest.raises(ReviewConflict):
        rt.reviews.edit(run_id, {"/total": 1.0}, expected_version=1)  # stale etag

    revision = rt.reviews.approve(run_id, reviewer="chetan", comment="ok", expected_version=2)
    assert revision.kind == "reviewed"
    assert revision.result is not None
    assert revision.result["vendor_name"] == "Northwind Components Ltd"
    assert revision.parent_revision_id == machine.revision_id

    # the run moved on, the machine revision is untouched, the view shows the reviewed result
    view = rt.get(run_id)
    assert view is not None
    assert view.status == RunStatus.accepted
    assert view.revision_id == revision.id
    assert view.result is not None
    assert view.result["vendor_name"] == "Northwind Components Ltd"
    revs = rt.reviews.revisions(run_id)
    assert [r.kind for r in revs] == ["machine", "reviewed"]
    assert revs[0].result is not None
    assert revs[0].result["vendor_name"] is None
    assert rt.list()[0].status == RunStatus.accepted
    types = [e.type for e in rt.events(run_id)]
    assert EventType.review_requested in types
    assert EventType.review_decided in types
    assert types[-1] == EventType.run_status_changed

    with pytest.raises(PolicyViolation) as exc:
        rt.reviews.edit(run_id, {"/total": 1.0})
    assert exc.value.code == "review_closed"


def test_reject(
    runtime_factory: RuntimeFactory,
    invoice_pdf: Path,
    invoice: GeneratedInvoice,
    invoice_schema: JsonSchema,
) -> None:
    rt = runtime_factory(StaticAdapter({}))
    run_id = _review_run(rt, invoice_pdf, invoice, invoice_schema)
    rt.reviews.reject(run_id, reviewer="chetan", comment="unreadable")
    view = rt.get(run_id)
    assert view is not None and view.status == RunStatus.rejected
    assert rt.reviews.get(run_id).status == ReviewStatus.rejected  # type: ignore[union-attr]


def test_review_requires_needs_review(
    runtime_factory: RuntimeFactory, invoice_pdf: Path, truth_envelope: object
) -> None:
    rt = runtime_factory(StaticAdapter(truth_envelope))
    result = Client(rt).run("x", inputs=[invoice_pdf], output_schema=Invoice)
    assert result.status == RunStatus.accepted
    with pytest.raises(PolicyViolation) as exc:
        rt.reviews.open(result.run_id)
    assert exc.value.code == "review_not_applicable"
