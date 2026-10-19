"""Scanned documents and photos end to end: OCR, deskew, evidence on the original (Phase 7)."""

from __future__ import annotations

import io
import json
import math
from pathlib import Path
from typing import Any

import httpx
import pytest
from PIL import Image

from hawser.client import Client
from hawser.config import HawserConfig, RecordMode
from hawser.evidence.transforms import compose
from hawser.gateway import Gateway, load_registry
from hawser.models import EventType, Policy, RunStatus, Severity
from hawser.models.common import JsonSchema
from hawser.models.policy import ValidationPolicy
from hawser.runtime.local import LocalRuntime
from hawser.testing import StaticAdapter, build_envelope
from hawser.tools import Broker
from hawser.tools import image as image_tools
from hawser_media import ocr, pdf
from hawser_synth import Invoice, generate_invoice
from hawser_synth.invoice import GeneratedInvoice

pytestmark = pytest.mark.skipif(not ocr.RapidOcrEngine.available(), reason="rapidocr not installed")


def _rt(tmp_path: Path, adapter: StaticAdapter, **config: Any) -> LocalRuntime:
    cfg = HawserConfig(home=tmp_path / "home", record_mode=RecordMode.live, **config)
    return LocalRuntime(cfg, gateway=Gateway(load_registry(), {"anthropic": adapter}))


@pytest.fixture(scope="module")
def scanned() -> GeneratedInvoice:
    return generate_invoice(9, layout=0, scanned=True)


@pytest.fixture(scope="module")
def schema() -> JsonSchema:
    from hawser.structured import inline_refs, to_json_schema

    return inline_refs(to_json_schema(Invoice))


def _envelope(inv: GeneratedInvoice, schema: JsonSchema) -> Any:
    return build_envelope(schema, inv.truth.model_dump(mode="json"), source_texts=inv.source_texts)


def test_scanned_pdf_gets_ocr_evidence(
    tmp_path: Path, scanned: GeneratedInvoice, schema: JsonSchema
) -> None:
    pdf_path = tmp_path / "scan.pdf"
    pdf_path.write_bytes(scanned.pdf)
    rt = _rt(tmp_path, StaticAdapter(_envelope(scanned, schema)))
    result = Client(rt).run("Extract the invoice.", inputs=[pdf_path], output_schema=Invoice)
    assert result.status == RunStatus.accepted, result.error or result.validation.issues
    found = [p for p, m in result.field_meta.items() if m.status.value == "found"]
    anchored = [p for p in found if m_ev(result, p)]
    assert len(anchored) / len(found) >= 0.9, f"{len(anchored)}/{len(found)} anchored"
    total = result.evidence_for("/total")[0]
    assert total.kind == "document_region" and total.bbox is not None
    page = pdf.extract_text(scanned.pdf)[0]
    assert (
        0 <= total.bbox.x0 < total.bbox.x1 <= page.width_pt
        and 0 <= total.bbox.y0 < total.bbox.y1 <= page.height_pt
    )
    types = [e.type for e in rt.events(result.run_id)]
    assert EventType.tool_called in types  # the OCR call is in the trace
    index = next(a for a in result.artifacts if a.media_type == "application/json")
    assert index.artifact_id
    steps = {s.key for s in rt.steps(result.run_id)}
    assert any(k.startswith("index:") for k in steps)


def m_ev(result: Any, pointer: str) -> bool:
    return bool(result.field_meta[pointer].evidence)


def _photo(inv: GeneratedInvoice, degrees: float) -> bytes:
    """A 'photo' of the invoice: rendered page, rotated by ``degrees`` with a grey border."""
    rendered = pdf.render_page(inv.pdf, 1, dpi=150)
    img = Image.open(io.BytesIO(rendered.png)).convert("RGB")
    rotated = img.rotate(
        degrees, expand=True, fillcolor=(235, 235, 235), resample=Image.Resampling.BICUBIC
    )
    buf = io.BytesIO()
    rotated.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def test_rotated_photo_is_deskewed_and_evidence_lands_on_original(
    tmp_path: Path, schema: JsonSchema
) -> None:
    inv = generate_invoice(12, layout=0)
    photo = tmp_path / "receipt.jpg"
    photo.write_bytes(_photo(inv, 12.0))
    rt = _rt(tmp_path, StaticAdapter(_envelope(inv, schema)))
    result = Client(rt).run("Extract the invoice.", inputs=[photo], output_schema=Invoice)
    assert result.status == RunStatus.accepted, result.error or [
        i.message for i in result.validation.issues
    ]
    ocr_events = [
        e
        for e in rt.events(result.run_id)
        if e.type == EventType.tool_called and str(e.payload.get("tool", "")).startswith("ocr.")
    ]
    assert ocr_events and math.isclose(
        abs(float(ocr_events[0].payload["deskew_degrees"])), 12.0, abs_tol=1.5
    )

    ev = result.evidence_for("/invoice_number")[0]
    assert ev.kind == "document_region" and ev.coordinate_system.value == "pixels"
    assert ev.transform_chain and [t.kind for t in ev.transform_chain][-2:] == ["rotate", "pad"]
    assert ev.artifact_id is not None  # located on the deskewed image
    # The box is now in the ORIGINAL photo's pixels: inside the image, and when mapped
    # forward through the chain it lands where the text is on the straightened image.
    orig = Image.open(photo)
    assert ev.bbox is not None
    assert (
        0 <= ev.bbox.x0 < ev.bbox.x1 <= orig.width and 0 <= ev.bbox.y0 < ev.bbox.y1 <= orig.height
    )
    straight = compose(ev.transform_chain).apply(
        (ev.bbox.x0 + ev.bbox.x1) / 2, (ev.bbox.y0 + ev.bbox.y1) / 2
    )
    deskewed = Image.open(io.BytesIO(rt.artifacts.get_bytes(ev.artifact_id)))
    assert 0 <= straight[0] <= deskewed.width and 0 <= straight[1] <= deskewed.height
    # The invoice number is near the top of the page on the straightened image.
    assert straight[1] < deskewed.height * 0.35
    found = [p for p, m in result.field_meta.items() if m.status.value == "found"]
    anchored = [p for p in found if m_ev(result, p)]
    assert len(anchored) / len(found) >= 0.8


def test_reocr_repair_runs_when_evidence_is_required(
    tmp_path: Path, scanned: GeneratedInvoice, schema: JsonSchema
) -> None:
    """With evidence_missing escalated to error, an unanchored OCR'd field triggers a re-OCR
    (no model call) before any re-extraction."""
    pdf_path = tmp_path / "scan.pdf"
    pdf_path.write_bytes(scanned.pdf)
    env = _envelope(scanned, schema)
    env["vendor_name"]["source_text"] = "zzz not on the page qqq"  # cannot be anchored
    rt = _rt(tmp_path, StaticAdapter(env))
    policy = Policy(validation=ValidationPolicy(escalate={"core.evidence_missing": Severity.error}))
    result = Client(rt).run(
        "Extract the invoice.", inputs=[pdf_path], output_schema=Invoice, policy=policy
    )
    repairs = [e.payload for e in rt.events(result.run_id) if e.type == EventType.repair_started]
    assert repairs and repairs[0]["strategy"] == "reocr"
    assert any(s.key.startswith("reocr1:") for s in rt.steps(result.run_id))
    assert result.usage.totals.model_calls <= 2  # the re-OCR attempt itself made no model call


def test_vision_tools_with_static_vlm(tmp_path: Path) -> None:
    inv = generate_invoice(2, layout=0)
    photo = tmp_path / "doc.png"
    photo.write_bytes(pdf.render_page(inv.pdf, 1, dpi=72).png)
    answers: dict[str, Any] = {
        "image.caption": {"caption": "An invoice."},
        "image.detect": {
            "detections": [
                {"label": "stamp", "box": [100, 100, 300, 200], "confidence": 0.8},
                {"label": "bad", "box": [900, 900, 100, 100], "confidence": 0.5},
            ]
        },
        "image.ground": {"matches": [{"box": [0, 0, 500, 100], "confidence": 0.9}]},
        "image.classify": {"label": "invoice", "confidence": 0.95},
        "image.tag": {"tags": ["invoice", "table"]},
        "image.ui_parse": {
            "elements": [{"type": "text", "text": "Total", "box": [10, 10, 200, 40]}]
        },
        "image.vqa": {"answer": "3,569.76", "confidence": 0.7},
    }

    def output_fn(req: Any) -> Any:
        text = next(p.text for p in req.messages[0].parts if p.kind == "text")
        if "Describe" in text:
            return answers["image.caption"]
        if "Detect" in text:
            return answers["image.detect"]
        if "Find every region" in text:
            return answers["image.ground"]
        if "Which single label" in text:
            return answers["image.classify"]
        if "List up to" in text:
            return answers["image.tag"]
        if "screenshot" in text:
            return answers["image.ui_parse"]
        return answers["image.vqa"]

    adapter = StaticAdapter(output_fn=output_fn)
    rt = _rt(tmp_path, adapter)
    run = Client(rt).run(
        "Describe.", inputs=[photo]
    )  # a direct text run to get a context with the asset admitted
    assert run.status == RunStatus.accepted
    asset_id = next(
        e.payload["asset_id"] for e in rt.events(run.run_id) if e.type == EventType.asset_admitted
    )
    ctx = _ctx(rt, run.run_id)
    broker = Broker(ctx)
    cap = broker.call("image.caption", {"ref": asset_id}).output
    assert cap["caption"] == "An invoice."
    det = broker.call("image.detect", {"ref": asset_id, "labels": ["stamp"]}).output
    assert len(det["detections"]) == 1 and det["dropped"] == 1
    w, h = Image.open(photo).size
    assert det["detections"][0]["box"] == [
        pytest.approx(w * 0.1, abs=0.2),
        pytest.approx(h * 0.1, abs=0.2),
        pytest.approx(w * 0.3, abs=0.2),
        pytest.approx(h * 0.2, abs=0.2),
    ]
    assert (
        det["evidence"][0]["kind"] == "image_region" and det["evidence"][0]["asset_id"] == asset_id
    )
    g = broker.call("image.ground", {"ref": asset_id, "phrase": "the header"}).output
    assert g["matches"][0]["point"][1] == pytest.approx(h * 0.05, abs=0.2)
    assert (
        broker.call("image.classify", {"ref": asset_id, "labels": ["invoice", "receipt"]}).output[
            "label"
        ]
        == "invoice"
    )
    assert broker.call("image.tag", {"ref": asset_id}).output["tags"] == ["invoice", "table"]
    assert broker.call("image.ui_parse", {"ref": asset_id}).output["elements"][0]["text"] == "Total"
    assert (
        broker.call("image.vqa", {"ref": asset_id, "question": "total?"}).output["answer"]
        == "3,569.76"
    )
    # the same call again replays the checkpoint: no new model call
    calls_before = len(adapter.calls)
    broker.call("image.caption", {"ref": asset_id})
    assert len(adapter.calls) == calls_before

    # ops chain through artifacts: crop a rotated image and detect on it -> evidence maps back
    rot = broker.call("image.rotate", {"ref": asset_id, "degrees": 10}).output
    crop = broker.call(
        "image.crop", {"ref": rot["artifact_id"], "x": 20, "y": 20, "w": 200, "h": 100}
    ).output
    assert [t["kind"] for t in crop["transform_chain"]] == ["rotate", "pad", "crop"]
    det2 = broker.call("image.detect", {"ref": crop["artifact_id"]}).output
    ev = det2["evidence"][0]
    assert [t["kind"] for t in ev["transform_chain"]] == ["rotate", "pad", "crop"] and ev[
        "asset_id"
    ] == asset_id
    seg = broker.call("image.segment", {"ref": asset_id, "boxes": [[0, 0, 10, 10]]}).output
    assert seg["segmenter"] == "box" and seg["rle"]["size"] == [h, w]
    emb = broker.call("image.embed", {"ref": asset_id}).output
    assert emb["dimensions"] == 256
    assert broker.call("image.similarity", {"ref_a": asset_id, "ref_b": asset_id}).output[
        "score"
    ] == pytest.approx(1.0, abs=1e-3)
    red = broker.call("image.redact", {"ref": asset_id, "boxes": [[0, 0, 50, 50]]}).output
    assert red["regions"] == 1
    ann = broker.call(
        "image.annotate", {"ref": asset_id, "boxes": [{"box": [0, 0, 50, 50], "label": "x"}]}
    ).output
    assert ann["regions"] == 1
    lay = broker.call("document.layout", {"ref": asset_id}).output
    assert lay["coordinate_system"] == "pixels" and lay["blocks"]
    rec = broker.call("ocr.recognize", {"ref": asset_id}).output
    assert rec["engine"] == "rapidocr" and "INV" in rec["text"]
    assert "rapidocr" in broker.call("ocr.engines", {}).output["available"]


def _ctx(rt: LocalRuntime, run_id: str) -> Any:
    from hawser.models import Usage
    from hawser.runtime.context import RunContext
    from hawser.runtime.events import EventEmitter
    from hawser.runtime.steps import StepExecutor

    run = rt.meta.get_run(run_id)
    assert run is not None
    emitter = EventEmitter(rt.meta, run_id=run.id, tenant_id=run.tenant_id)
    usage = Usage()
    return RunContext(
        config=rt.config,
        run=run,
        request=run.request,
        meta=rt.meta,
        artifacts=rt.artifacts,
        gateway=rt.gateway,
        emitter=emitter,
        steps=StepExecutor(rt.meta, rt.artifacts, emitter, run_id=run.id, usage=usage),
        usage=usage,
    )


def test_generation_is_policy_gated_and_uses_images_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inv = generate_invoice(2, layout=0)
    photo = tmp_path / "doc.png"
    photo.write_bytes(pdf.render_page(inv.pdf, 1, dpi=72).png)
    rt = _rt(tmp_path, StaticAdapter(text="ok"))
    run = Client(rt).run("Describe.", inputs=[photo])
    ctx = _ctx(rt, run.run_id)
    broker = Broker(ctx)
    from hawser.errors import PolicyViolation

    with pytest.raises(PolicyViolation):
        broker.call("image.generate", {"prompt": "a cat"})

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        png = io.BytesIO()
        Image.new("RGB", (8, 8), "red").save(png, format="PNG")
        import base64

        return httpx.Response(
            200, json={"data": [{"b64_json": base64.b64encode(png.getvalue()).decode()}]}
        )

    image_tools.set_http_client(httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setenv("HAWSER_IMAGE_API_KEY", "k")
    try:
        ctx.request = ctx.request.model_copy(update={"policy": Policy(generation="enabled")})
        out = Broker(ctx).call("image.generate", {"prompt": "a cat"}).output
        assert out["artifact_id"].startswith("art_") and seen == ["/v1/images/generations"]
        art = rt.meta.get_artifact(out["artifact_id"])
        assert (
            art is not None
            and art.meta.kind.value == "generated"
            and art.meta.media["evidence"] is False
        )
        edited = (
            Broker(ctx).call("image.edit", {"ref": out["artifact_id"], "prompt": "bluer"}).output
        )
        assert edited["kind"] == "edit" and seen[-1] == "/v1/images/edits"
        assert ctx.usage.tool_calls[-1].cost_usd == 0.04
    finally:
        image_tools.set_http_client(None)
    assert json.dumps(out)
