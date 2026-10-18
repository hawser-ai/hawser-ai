from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from hawser.config import HawserConfig, RecordMode
from hawser.gateway import Gateway, load_registry
from hawser.models.common import JsonSchema
from hawser.runtime.local import LocalRuntime
from hawser.structured import inline_refs, to_json_schema
from hawser.testing import StaticAdapter, build_envelope
from hawser_synth import Invoice, generate_invoice
from hawser_synth.invoice import GeneratedInvoice


@pytest.fixture(scope="session")
def invoice() -> GeneratedInvoice:
    return generate_invoice(7, layout=0)


@pytest.fixture(scope="session")
def scanned_invoice() -> GeneratedInvoice:
    return generate_invoice(7, layout=0, scanned=True)


@pytest.fixture(scope="session")
def invoice_schema() -> JsonSchema:
    return inline_refs(to_json_schema(Invoice))


@pytest.fixture
def invoice_pdf(tmp_path: Path, invoice: GeneratedInvoice) -> Path:
    p = tmp_path / "invoice.pdf"
    p.write_bytes(invoice.pdf)
    return p


@pytest.fixture
def truth_envelope(invoice: GeneratedInvoice, invoice_schema: JsonSchema) -> Any:
    return build_envelope(
        invoice_schema, invoice.truth.model_dump(mode="json"), source_texts=invoice.source_texts
    )


RuntimeFactory = Callable[..., LocalRuntime]


def make_runtime(tmp_path: Path, adapter: StaticAdapter, **config: Any) -> LocalRuntime:
    cfg = HawserConfig(home=tmp_path / "home", record_mode=RecordMode.live, **config)
    gateway = Gateway(load_registry(), {"anthropic": adapter})
    return LocalRuntime(cfg, gateway=gateway)


@pytest.fixture
def runtime_factory(tmp_path: Path) -> RuntimeFactory:
    def factory(adapter: StaticAdapter, **config: Any) -> LocalRuntime:
        return make_runtime(tmp_path, adapter, **config)

    return factory
